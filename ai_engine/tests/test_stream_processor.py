
"""
Unit tests for StreamProcessor and StreamProcessorConfig in Sentinel AI Engine.

Verifies:
- Frame stride sampling mechanics
- PTS-based target FPS sampling
- Preservation of camera_id and pts_ms
- Stream discontinuity handling and propagation
- Exception isolation protecting against detector failures
- Telemetry counters and metrics reset
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from ai_engine.schemas import Detection, DetectionResult
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from streaming.frame_reader import FramePacket


def _make_packet(
    camera_id: str = "CAM-01",
    pts_ms: float | None = 100.0,
    is_discontinuity: bool = False,
    is_irregular: bool = False,
) -> FramePacket:
    """Helper to generate a lightweight dummy FramePacket for unit testing."""
    return FramePacket(
        frame=np.zeros((64, 64, 3), dtype=np.uint8),
        pts_ms=pts_ms,
        received_at=time.time(),
        width=64,
        height=64,
        camera_id=camera_id,
        is_discontinuity=is_discontinuity,
        is_irregular=is_irregular,
    )


def test_stream_processor_stride_1_processes_all():
    """Default stride of 1 processes every received frame."""
    mock_detector = MagicMock()
    mock_detector.detect.side_effect = lambda pkt: DetectionResult(
        camera_id=pkt.camera_id,
        pts_ms=pkt.pts_ms,
        detections=[],
        inference_time_ms=5.0,
    )

    processor = StreamProcessor(detector=mock_detector, config=StreamProcessorConfig(frame_stride=1))

    for i in range(5):
        pkt = _make_packet(pts_ms=float(i * 40))
        res = processor.process_packet(pkt)
        assert res is not None
        assert res.pts_ms == float(i * 40)

    stats = processor.get_stats()
    assert stats["frames_received"] == 5
    assert stats["frames_processed"] == 5
    assert stats["frames_skipped"] == 0
    assert stats["inference_errors"] == 0
    assert mock_detector.detect.call_count == 5


def test_stream_processor_frame_stride_3():
    """Stride of 3 processes frame 1, 4, 7 and skips others."""
    mock_detector = MagicMock()
    mock_detector.detect.side_effect = lambda pkt: DetectionResult(
        camera_id=pkt.camera_id,
        pts_ms=pkt.pts_ms,
        detections=[],
    )

    processor = StreamProcessor(detector=mock_detector, config=StreamProcessorConfig(frame_stride=3))

    results = []
    for i in range(7):
        pkt = _make_packet(pts_ms=float(i * 33.33))
        results.append(processor.process_packet(pkt))

    # Frame indices: 0 (processed), 1 (skipped), 2 (skipped), 3 (processed), 4 (skipped), 5 (skipped), 6 (processed)
    assert results[0] is not None
    assert results[1] is None
    assert results[2] is None
    assert results[3] is not None
    assert results[4] is None
    assert results[5] is None
    assert results[6] is not None

    stats = processor.get_stats()
    assert stats["frames_received"] == 7
    assert stats["frames_processed"] == 3
    assert stats["frames_skipped"] == 4
    assert stats["inference_errors"] == 0
    assert mock_detector.detect.call_count == 3


def test_stream_processor_target_fps_sampling():
    """Target FPS sampling skips frames closer together than 1000/fps."""
    mock_detector = MagicMock()
    mock_detector.detect.side_effect = lambda pkt: DetectionResult(
        camera_id=pkt.camera_id,
        pts_ms=pkt.pts_ms,
        detections=[],
    )

    # 10 FPS target => min interval 100 ms
    processor = StreamProcessor(
        detector=mock_detector,
        config=StreamProcessorConfig(target_fps=10.0),
    )

    pts_values = [0.0, 33.33, 66.66, 100.0, 133.33, 200.0]
    processed_pts = []

    for pts in pts_values:
        pkt = _make_packet(pts_ms=pts)
        res = processor.process_packet(pkt)
        if res is not None:
            processed_pts.append(res.pts_ms)

    assert processed_pts == [0.0, 100.0, 200.0]
    assert processor.frames_received == 6
    assert processor.frames_processed == 3
    assert processor.frames_skipped == 3


def test_stream_processor_preserves_metadata():
    """Verifies camera_id and source pts_ms are faithfully preserved."""
    mock_detector = MagicMock()
    mock_detector.detect.side_effect = lambda pkt: DetectionResult(
        camera_id=pkt.camera_id,
        pts_ms=pkt.pts_ms,
        detections=[
            Detection(
                class_id=2,
                class_name="car",
                confidence=0.89,
                x1=10, y1=10, x2=50, y2=50,
                camera_id=pkt.camera_id,
                pts_ms=pkt.pts_ms,
            )
        ],
    )

    processor = StreamProcessor(detector=mock_detector)
    packet = _make_packet(camera_id="JUNCTION-NORTH-CAM", pts_ms=98765.43)

    res = processor.process_packet(packet)
    assert res is not None
    assert res.camera_id == "JUNCTION-NORTH-CAM"
    assert res.pts_ms == 98765.43
    assert len(res.detections) == 1
    assert res.detections[0].camera_id == "JUNCTION-NORTH-CAM"
    assert res.detections[0].pts_ms == 98765.43


def test_stream_processor_propagates_discontinuity():
    """Discontinuity flag on packet is propagated to DetectionResult."""
    mock_detector = MagicMock()
    mock_detector.detect.side_effect = lambda pkt: DetectionResult(
        camera_id=pkt.camera_id,
        pts_ms=pkt.pts_ms,
        detections=[],
    )

    processor = StreamProcessor(detector=mock_detector, config=StreamProcessorConfig(frame_stride=5))

    # Normal frame
    normal_pkt = _make_packet(pts_ms=100.0, is_discontinuity=False)
    res1 = processor.process_packet(normal_pkt)
    assert res1 is not None
    assert res1.is_discontinuity is False

    # Second frame would normally be skipped by stride 5, but if discontinuity occurs, it should process
    disc_pkt = _make_packet(pts_ms=5000.0, is_discontinuity=True)
    res2 = processor.process_packet(disc_pkt)
    assert res2 is not None
    assert res2.is_discontinuity is True


def test_stream_processor_detector_exception_isolation():
    """Detector exceptions do not crash process_packet, are recorded in error telemetry."""
    mock_detector = MagicMock()
    mock_detector.detect.side_effect = RuntimeError("Simulated CUDA OOM or driver crash")

    processor = StreamProcessor(detector=mock_detector)
    packet = _make_packet(camera_id="CRASH-CAM", pts_ms=250.0)

    # Must NOT raise exception
    res = processor.process_packet(packet)

    assert res is None
    assert processor.frames_received == 1
    assert processor.frames_processed == 0
    assert processor.frames_skipped == 0
    assert processor.inference_errors == 1


def test_stream_processor_reset_stats():
    """Verifies stats counters and state can be reset cleanly."""
    mock_detector = MagicMock()
    mock_detector.detect.side_effect = lambda pkt: DetectionResult(
        camera_id=pkt.camera_id,
        pts_ms=pkt.pts_ms,
    )

    processor = StreamProcessor(detector=mock_detector, config=StreamProcessorConfig(frame_stride=2))
    processor.process_packet(_make_packet(pts_ms=10.0))  # processed
    processor.process_packet(_make_packet(pts_ms=20.0))  # skipped

    assert processor.frames_received == 2
    assert processor.frames_processed == 1
    assert processor.frames_skipped == 1

    processor.reset_stats()

    stats = processor.get_stats()
    assert stats["frames_received"] == 0
    assert stats["frames_processed"] == 0
    assert stats["frames_skipped"] == 0
    assert stats["inference_errors"] == 0
    assert processor._last_processed_pts_ms is None
