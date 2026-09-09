import os
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from streaming.rtsp_client import RTSPClient, _fourcc_to_string
from streaming.frame_reader import FrameReader, FramePacket
from streaming.stream_manager import StreamManager


def test_fourcc_conversion():
    # H264 in integer representation
    h264_int = ord('H') | (ord('2') << 8) | (ord('6') << 16) | (ord('4') << 24)
    assert _fourcc_to_string(h264_int) == "H264"

    # Invalid / 0
    assert _fourcc_to_string(0) is None
    assert _fourcc_to_string(-1) is None


def test_mask_credentials():
    url = "rtsp://admin:secretPass123@192.168.1.100:554/live"
    masked = RTSPClient._mask_credentials(url)
    assert "secretPass123" not in masked
    assert "admin" not in masked
    assert masked == "rtsp://***:***@192.168.1.100:554/live"


@patch("cv2.VideoCapture")
def test_rtsp_client_open_forces_tcp(mock_video_capture):
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.get.side_effect = lambda prop: {
        3: 1280.0,  # CAP_PROP_FRAME_WIDTH
        4: 720.0,   # CAP_PROP_FRAME_HEIGHT
        5: 25.0,    # CAP_PROP_FPS
        6: 0.0,     # CAP_PROP_FOURCC
    }.get(prop, 0.0)
    mock_video_capture.return_value = mock_cap

    client = RTSPClient(
        rtsp_url="rtsp://example.com/stream",
        camera_id="cam-1",
        transport="tcp",
        codec_hint="H.264",
    )

    opened = client.open()
    assert opened is True
    assert client.is_open is True
    assert client.width == 1280
    assert client.height == 720
    assert client.codec == "H.264"
    assert os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS") == "rtsp_transport;tcp"

    # Test read_frame
    dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    mock_cap.read.return_value = (True, dummy_frame)
    # CAP_PROP_POS_MSEC = 0
    mock_cap.get.side_effect = lambda prop: 120.0 if prop == 0 else 0.0

    ret, frame, pts_ms = client.read_frame()
    assert ret is True
    assert frame is not None
    assert pts_ms == pytest.approx(120.0)

    # Test clean close
    client.close()
    assert client.is_open is False
    mock_cap.release.assert_called_once()


def test_frame_reader_integration():
    mock_client = MagicMock()
    mock_client.camera_id = "test-cam"
    mock_client.codec = "H.264"
    mock_client.width = 640
    mock_client.height = 480

    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mock_client.read_frame.return_value = (True, dummy_frame, 200.0)

    reader = FrameReader(client=mock_client)
    success, packet = reader.read_packet()

    assert success is True
    assert isinstance(packet, FramePacket)
    assert packet.camera_id == "test-cam"
    assert packet.pts_ms == 200.0
    assert packet.width == 640
    assert packet.height == 480
    assert packet.received_at > 0
    # Confirm video time is separated from wall-clock arrival
    assert packet.pts_ms != packet.received_at


def test_stream_manager_deduplication():
    manager = StreamManager()

    s1 = manager.add_camera("cam-1", "rtsp://stream/1", auto_start=False)
    s2 = manager.add_camera("cam-1", "rtsp://stream/1", auto_start=False)

    # Must return the same session, avoiding duplicate connections
    assert s1 is s2
    assert len(manager._streams) == 1

    manager.remove_camera("cam-1")
    assert len(manager._streams) == 0
