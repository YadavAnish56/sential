import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from ai_engine.schemas import Detection, DetectionResult
from ai_engine.detector import VehicleDetector, ModelNotAvailableError
from ai_engine.config import DetectorConfig
from streaming.frame_reader import FramePacket



def test_missing_model_handling():
    detector = VehicleDetector(model_path="non_existent_weights.pt")

    assert detector.is_ready is False
    assert detector.status == "MODEL_NOT_AVAILABLE"
    assert "not found" in detector.error_message

    packet = FramePacket(
        frame=np.zeros((480, 640, 3), dtype=np.uint8),
        pts_ms=100.0,
        received_at=1700000000.0,
        width=640,
        height=480,
        camera_id="cam-1",
    )

    with pytest.raises(ModelNotAvailableError) as exc_info:
        detector.detect(packet)
    assert "not found" in str(exc_info.value).lower()



@patch("os.path.isfile", return_value=True)
def test_dynamic_vehicle_class_resolution(mock_isfile):
    with patch("ultralytics.YOLO") as mock_yolo_cls:
        mock_model = MagicMock()
        mock_model.names = {
            0: "person",
            1: "bicycle",
            2: "car",
            3: "motorcycle",
            4: "airplane",
            5: "bus",
            6: "train",
            7: "truck",
            8: "boat",
        }
        mock_yolo_cls.return_value = mock_model

        detector = VehicleDetector(
            model_path="models/yolov8n.pt",
            target_classes=["car", "motorcycle", "bus", "truck"],
        )

        assert detector.is_ready is True
        assert detector.status == "READY"
        # Must resolve IDs [2, 3, 5, 7] dynamically from names, not hardcoded
        assert sorted(detector.target_class_ids) == [2, 3, 5, 7]
        assert detector.class_id_to_name[2] == "car"
        assert detector.class_id_to_name[7] == "truck"


@patch("os.path.isfile", return_value=True)
def test_device_selection_auto_and_cpu(mock_isfile):
    with patch("ultralytics.YOLO"):
        with patch("torch.cuda.is_available", return_value=False):
            detector_auto = VehicleDetector(model_path="models/yolov8n.pt", device="auto")
            assert detector_auto.resolved_device == "cpu"

            detector_cpu = VehicleDetector(model_path="models/yolov8n.pt", device="cpu")
            assert detector_cpu.resolved_device == "cpu"

        with patch("torch.cuda.is_available", return_value=True):
            detector_cuda = VehicleDetector(model_path="models/yolov8n.pt", device="auto")
            assert detector_cuda.resolved_device == "cuda"


@patch("os.path.isfile", return_value=True)
def test_frame_packet_inference_and_pts_preservation(mock_isfile):
    with patch("ultralytics.YOLO") as mock_yolo_cls:
        mock_model = MagicMock()
        mock_model.names = {2: "car", 7: "truck"}

        # Simulate YOLO results structure
        mock_box = MagicMock()
        mock_box.xyxy.cpu().numpy.return_value = np.array([
            [100.0, 150.0, 300.0, 250.0],
            [350.0, 200.0, 500.0, 400.0],
        ])
        mock_box.conf.cpu().numpy.return_value = np.array([0.88, 0.76])
        mock_box.cls.cpu().numpy.return_value = np.array([2, 7])

        mock_prediction = MagicMock()
        mock_prediction.boxes = mock_box
        mock_model.predict.return_value = [mock_prediction]

        mock_yolo_cls.return_value = mock_model

        detector = VehicleDetector(
            model_path="models/yolov8n.pt",
            confidence=0.40,
            target_classes=["car", "truck"],
            device="cpu",
        )

        dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        packet = FramePacket(
            frame=dummy_frame,
            pts_ms=450.5,
            received_at=1770000000.0,  # wall-clock time
            width=1280,
            height=720,
            camera_id="cam-surat-01",
        )

        result = detector.detect(packet)

        # Verify inference was called with frame and parameters
        mock_model.predict.assert_called_once()
        call_kwargs = mock_model.predict.call_args[1]
        assert call_kwargs["conf"] == 0.40
        assert call_kwargs["device"] == "cpu"

        # Verify Result
        assert isinstance(result, DetectionResult)
        assert result.camera_id == "cam-surat-01"
        assert result.pts_ms == 450.5
        assert result.count == 2

        # Verify Detection objects
        d1 = result.detections[0]
        assert d1.class_id == 2
        assert d1.class_name == "car"
        assert d1.confidence == pytest.approx(0.88)
        assert d1.bbox == (100.0, 150.0, 300.0, 250.0)
        assert d1.camera_id == "cam-surat-01"
        assert d1.pts_ms == 450.5
        assert d1.pts_ms != packet.received_at  # Source PTS preserved, NOT arrival wall-clock

        d2 = result.detections[1]
        assert d2.class_id == 7
        assert d2.class_name == "truck"
        assert d2.pts_ms == 450.5
