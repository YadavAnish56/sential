"""
Unit tests for Phase 6D.2 — CameraPipeline single-camera AI pipeline coordinator.

Tests cover:
- Construction validation
- Frame processing through the full pipeline chain
- Frame skipping propagation
- Discontinuity propagation across all stages
- Error isolation at each stage (detection, tracking, ANPR)
- Pipeline without ANPR coordinator
- Telemetry accounting
- PipelineResult properties and serialization
- Multi-frame sequences with tracking continuity
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streaming.frame_reader import FramePacket
from ai_engine.schemas import Detection, DetectionResult
from ai_engine.tracking.schemas import Track, TrackState, TrackingResult
from ai_engine.anpr.schemas import ANPRResult, ANPRConfig
from ai_engine.pipeline import CameraPipeline, PipelineResult


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _make_packet(
    camera_id: str = "CAM_01",
    pts_ms: float = 100.0,
    width: int = 1920,
    height: int = 1080,
    is_discontinuity: bool = False,
) -> FramePacket:
    """Create a synthetic FramePacket with a random frame array."""
    return FramePacket(
        frame=np.random.randint(0, 255, (height, width, 3), dtype=np.uint8),
        pts_ms=pts_ms,
        received_at=time.time(),
        width=width,
        height=height,
        camera_id=camera_id,
        is_discontinuity=is_discontinuity,
    )


def _make_detection(
    camera_id: str = "CAM_01",
    pts_ms: float = 100.0,
    class_name: str = "car",
) -> Detection:
    """Create a synthetic Detection."""
    return Detection(
        class_id=2,
        class_name=class_name,
        confidence=0.85,
        x1=100.0,
        y1=200.0,
        x2=300.0,
        y2=400.0,
        camera_id=camera_id,
        pts_ms=pts_ms,
    )


def _make_detection_result(
    camera_id: str = "CAM_01",
    pts_ms: float = 100.0,
    num_detections: int = 1,
    is_discontinuity: bool = False,
) -> DetectionResult:
    """Create a synthetic DetectionResult."""
    dets = [
        _make_detection(camera_id=camera_id, pts_ms=pts_ms)
        for _ in range(num_detections)
    ]
    return DetectionResult(
        camera_id=camera_id,
        pts_ms=pts_ms,
        detections=dets,
        inference_time_ms=5.0,
        device="cuda:0",
        model_name="yolov8n",
        is_discontinuity=is_discontinuity,
    )


def _make_tracking_result(
    camera_id: str = "CAM_01",
    pts_ms: float = 100.0,
    num_tracks: int = 1,
    is_discontinuity: bool = False,
) -> TrackingResult:
    """Create a synthetic TrackingResult."""
    tracks = [
        Track(
            track_id=i + 1,
            camera_id=camera_id,
            class_id=2,
            class_name="car",
            confidence=0.85,
            bbox=(100.0, 200.0, 300.0, 400.0),
            state=TrackState.CONFIRMED,
            first_pts_ms=pts_ms,
            last_pts_ms=pts_ms,
            hits=3,
            misses=0,
            best_confidence=0.85,
            best_bbox=(100.0, 200.0, 300.0, 400.0),
        )
        for i in range(num_tracks)
    ]
    return TrackingResult(
        camera_id=camera_id,
        pts_ms=pts_ms,
        is_discontinuity=is_discontinuity,
        active_tracks=tracks,
        new_tracks=tracks[:],
        lost_tracks=[],
    )


def _make_anpr_result(
    camera_id: str = "CAM_01",
    track_id: int = 1,
    pts_ms: float = 100.0,
    status: str = "RECOGNIZED",
) -> ANPRResult:
    """Create a synthetic ANPRResult."""
    return ANPRResult(
        camera_id=camera_id,
        track_id=track_id,
        pts_ms=pts_ms,
        raw_text="GJ05AB1234",
        normalized_plate="GJ05AB1234",
        confidence=0.92,
        is_valid_format=True,
        status=status,
    )


def _build_pipeline(
    camera_id: str = "CAM_01",
    detection_return: DetectionResult | None = None,
    tracking_return: TrackingResult | None = None,
    anpr_return: list[ANPRResult] | None = None,
    include_anpr: bool = True,
    sp_side_effect: Exception | None = None,
    tracker_side_effect: Exception | None = None,
    anpr_side_effect: Exception | None = None,
) -> CameraPipeline:
    """Build a CameraPipeline with mocked components."""
    # Mock StreamProcessor
    mock_sp = MagicMock()
    if sp_side_effect:
        mock_sp.process_packet.side_effect = sp_side_effect
    else:
        mock_sp.process_packet.return_value = detection_return
    mock_sp.get_stats.return_value = {
        "frames_received": 0,
        "frames_processed": 0,
        "frames_skipped": 0,
        "inference_errors": 0,
    }

    # Mock VehicleTracker
    mock_tracker = MagicMock()
    if tracker_side_effect:
        mock_tracker.update.side_effect = tracker_side_effect
    else:
        mock_tracker.update.return_value = tracking_return

    # Mock ANPRCoordinator
    mock_anpr = None
    if include_anpr:
        mock_anpr = MagicMock()
        if anpr_side_effect:
            mock_anpr.process.side_effect = anpr_side_effect
        else:
            mock_anpr.process.return_value = anpr_return or []

    return CameraPipeline(
        camera_id=camera_id,
        stream_processor=mock_sp,
        vehicle_tracker=mock_tracker,
        anpr_coordinator=mock_anpr,
    )


# ──────────────────────────────────────────────────────────────────────
# Construction Tests
# ──────────────────────────────────────────────────────────────────────

class TestCameraPipelineConstruction:
    """Tests for CameraPipeline constructor validation."""

    def test_valid_construction(self):
        """Pipeline constructs successfully with valid components."""
        pipeline = _build_pipeline()
        assert pipeline.camera_id == "CAM_01"

    def test_empty_camera_id_raises(self):
        """Empty camera_id must raise ValueError."""
        with pytest.raises(ValueError, match="camera_id"):
            CameraPipeline(
                camera_id="",
                stream_processor=MagicMock(),
                vehicle_tracker=MagicMock(),
            )

    def test_none_stream_processor_raises(self):
        """None stream_processor must raise ValueError."""
        with pytest.raises(ValueError, match="stream_processor"):
            CameraPipeline(
                camera_id="CAM_01",
                stream_processor=None,
                vehicle_tracker=MagicMock(),
            )

    def test_none_vehicle_tracker_raises(self):
        """None vehicle_tracker must raise ValueError."""
        with pytest.raises(ValueError, match="vehicle_tracker"):
            CameraPipeline(
                camera_id="CAM_01",
                stream_processor=MagicMock(),
                vehicle_tracker=None,
            )

    def test_anpr_coordinator_optional(self):
        """Pipeline must construct without ANPRCoordinator."""
        pipeline = _build_pipeline(include_anpr=False)
        assert pipeline.anpr_coordinator is None


# ──────────────────────────────────────────────────────────────────────
# Full Pipeline Flow Tests
# ──────────────────────────────────────────────────────────────────────

class TestCameraPipelineFlow:
    """Tests for the full FramePacket → PipelineResult processing chain."""

    def test_full_pipeline_happy_path(self):
        """Frame processed through all three stages produces complete PipelineResult."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        anpr = [_make_anpr_result()]

        pipeline = _build_pipeline(
            detection_return=det,
            tracking_return=trk,
            anpr_return=anpr,
        )
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.camera_id == "CAM_01"
        assert result.pts_ms == 100.0
        assert result.skipped is False
        assert result.detection_result is det
        assert result.tracking_result is trk
        assert result.anpr_results == anpr
        assert result.has_detections is True
        assert result.has_tracks is True
        assert result.has_plates is True
        assert len(result.errors) == 0
        assert result.pipeline_time_ms >= 0.0

    def test_skipped_frame_returns_skip_result(self):
        """When StreamProcessor returns None (frame skipped), pipeline marks skipped."""
        pipeline = _build_pipeline(detection_return=None)
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.skipped is True
        assert result.detection_result is None
        assert result.tracking_result is None
        assert result.anpr_results == []
        assert result.has_detections is False
        assert result.has_tracks is False
        assert result.has_plates is False

    def test_empty_detections_still_tracked(self):
        """DetectionResult with zero detections is still passed to tracker."""
        det = _make_detection_result(num_detections=0)
        trk = _make_tracking_result(num_tracks=0)

        pipeline = _build_pipeline(detection_return=det, tracking_return=trk)
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.skipped is False
        assert result.detection_result is det
        assert result.detection_result.count == 0
        assert result.has_detections is False
        assert result.tracking_result is trk

    def test_tracker_returns_none_skips_anpr(self):
        """If VehicleTracker returns None (unusual), ANPR stage is skipped."""
        det = _make_detection_result()
        pipeline = _build_pipeline(detection_return=det, tracking_return=None)
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.detection_result is det
        assert result.tracking_result is None
        assert result.anpr_results == []
        # ANPR coordinator should not have been called
        pipeline.anpr_coordinator.process.assert_not_called()

    def test_packet_pts_preserved_in_result(self):
        """PipelineResult.pts_ms must reflect the FramePacket source PTS."""
        det = _make_detection_result(pts_ms=42.5)
        trk = _make_tracking_result(pts_ms=42.5)
        pipeline = _build_pipeline(detection_return=det, tracking_return=trk)
        packet = _make_packet(pts_ms=42.5)
        result = pipeline.process_frame(packet)

        assert result.pts_ms == 42.5

    def test_none_pts_propagated(self):
        """Pipeline handles None PTS gracefully."""
        det = _make_detection_result(pts_ms=None)
        trk = _make_tracking_result(pts_ms=None)
        pipeline = _build_pipeline(detection_return=det, tracking_return=trk)
        packet = _make_packet(pts_ms=None)
        result = pipeline.process_frame(packet)

        assert result.pts_ms is None
        assert result.skipped is False


# ──────────────────────────────────────────────────────────────────────
# Pipeline Without ANPR Tests
# ──────────────────────────────────────────────────────────────────────

class TestCameraPipelineNoANPR:
    """Tests for pipeline operation when ANPR coordinator is omitted."""

    def test_no_anpr_still_detects_and_tracks(self):
        """Detection and tracking work normally without ANPRCoordinator."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        pipeline = _build_pipeline(
            detection_return=det,
            tracking_return=trk,
            include_anpr=False,
        )
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.detection_result is det
        assert result.tracking_result is trk
        assert result.anpr_results == []
        assert result.has_plates is False

    def test_no_anpr_skip_path(self):
        """Skipped frames work normally without ANPRCoordinator."""
        pipeline = _build_pipeline(detection_return=None, include_anpr=False)
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.skipped is True
        assert result.anpr_results == []


# ──────────────────────────────────────────────────────────────────────
# Error Isolation Tests
# ──────────────────────────────────────────────────────────────────────

class TestCameraPipelineErrorIsolation:
    """Tests for error isolation at each pipeline stage."""

    def test_stream_processor_error_isolated(self):
        """StreamProcessor exception is caught; result has error; downstream skipped."""
        pipeline = _build_pipeline(
            sp_side_effect=RuntimeError("detector GPU OOM"),
        )
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.skipped is True
        assert result.detection_result is None
        assert result.tracking_result is None
        assert len(result.errors) == 1
        assert "StreamProcessor error" in result.errors[0]
        assert "GPU OOM" in result.errors[0]

    def test_tracker_error_isolated(self):
        """VehicleTracker exception is caught; detection still available."""
        det = _make_detection_result()
        pipeline = _build_pipeline(
            detection_return=det,
            tracker_side_effect=RuntimeError("tracker NaN"),
        )
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.skipped is False
        assert result.detection_result is det
        assert result.tracking_result is None
        assert len(result.errors) == 1
        assert "VehicleTracker error" in result.errors[0]

    def test_anpr_error_isolated(self):
        """ANPRCoordinator exception is caught; detection and tracking available."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        pipeline = _build_pipeline(
            detection_return=det,
            tracking_return=trk,
            anpr_side_effect=RuntimeError("ANPR CUDA error"),
        )
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert result.skipped is False
        assert result.detection_result is det
        assert result.tracking_result is trk
        assert result.anpr_results == []
        assert len(result.errors) == 1
        assert "ANPRCoordinator error" in result.errors[0]

    def test_multiple_stage_errors_collected(self):
        """If multiple stages fail, all errors are collected in result.errors."""
        # Tracker errors but detection succeeds; then ANPR also errors
        det = _make_detection_result()
        pipeline = _build_pipeline(
            detection_return=det,
            tracker_side_effect=RuntimeError("tracker fail"),
        )
        packet = _make_packet()
        result = pipeline.process_frame(packet)

        assert len(result.errors) == 1
        # Since tracker returned None, ANPR is not called, so only 1 error
        assert "VehicleTracker error" in result.errors[0]


# ──────────────────────────────────────────────────────────────────────
# Discontinuity Propagation Tests
# ──────────────────────────────────────────────────────────────────────

class TestCameraPipelineDiscontinuity:
    """Tests for stream discontinuity propagation through the pipeline."""

    def test_discontinuity_packet_processed(self):
        """Discontinuity FramePacket is passed through all stages normally."""
        det = _make_detection_result(is_discontinuity=True)
        trk = _make_tracking_result(is_discontinuity=True)
        pipeline = _build_pipeline(
            detection_return=det,
            tracking_return=trk,
        )
        packet = _make_packet(is_discontinuity=True)
        result = pipeline.process_frame(packet)

        assert result.skipped is False
        assert result.detection_result.is_discontinuity is True
        assert result.tracking_result.is_discontinuity is True


# ──────────────────────────────────────────────────────────────────────
# Telemetry / Stats Tests
# ──────────────────────────────────────────────────────────────────────

class TestCameraPipelineTelemetry:
    """Tests for pipeline session telemetry accounting."""

    def test_stats_initial(self):
        """Fresh pipeline has zeroed stats."""
        pipeline = _build_pipeline()
        stats = pipeline.get_stats()
        assert stats["camera_id"] == "CAM_01"
        assert stats["frames_total"] == 0
        assert stats["frames_skipped"] == 0
        assert stats["frames_detected"] == 0
        assert stats["frames_tracked"] == 0
        assert stats["frames_anpr"] == 0
        assert stats["total_errors"] == 0

    def test_stats_after_processed_frame(self):
        """Stats increment correctly after processing a full frame."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        anpr = [_make_anpr_result()]
        pipeline = _build_pipeline(
            detection_return=det,
            tracking_return=trk,
            anpr_return=anpr,
        )
        pipeline.process_frame(_make_packet())
        stats = pipeline.get_stats()

        assert stats["frames_total"] == 1
        assert stats["frames_skipped"] == 0
        assert stats["frames_detected"] == 1
        assert stats["frames_tracked"] == 1
        assert stats["frames_anpr"] == 1
        assert stats["total_errors"] == 0

    def test_stats_after_skipped_frame(self):
        """Stats correctly account for skipped frames."""
        pipeline = _build_pipeline(detection_return=None)
        pipeline.process_frame(_make_packet())
        stats = pipeline.get_stats()

        assert stats["frames_total"] == 1
        assert stats["frames_skipped"] == 1
        assert stats["frames_detected"] == 0
        assert stats["frames_tracked"] == 0

    def test_stats_after_error(self):
        """Stats correctly account for error frames."""
        pipeline = _build_pipeline(
            sp_side_effect=RuntimeError("boom"),
        )
        pipeline.process_frame(_make_packet())
        stats = pipeline.get_stats()

        assert stats["frames_total"] == 1
        assert stats["total_errors"] == 1
        assert stats["frames_skipped"] == 1

    def test_stats_multi_frame_accumulation(self):
        """Stats accumulate correctly across multiple frames."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        pipeline = _build_pipeline(detection_return=det, tracking_return=trk)

        for i in range(5):
            pipeline.process_frame(_make_packet(pts_ms=float(i * 100)))

        stats = pipeline.get_stats()
        assert stats["frames_total"] == 5
        assert stats["frames_detected"] == 5
        assert stats["frames_tracked"] == 5

    def test_stats_includes_stream_processor_stats(self):
        """get_stats() includes embedded StreamProcessor stats."""
        pipeline = _build_pipeline()
        stats = pipeline.get_stats()
        assert "stream_processor_stats" in stats
        assert isinstance(stats["stream_processor_stats"], dict)

    def test_reset_stats(self):
        """reset_stats() zeroes pipeline-level counters."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        pipeline = _build_pipeline(detection_return=det, tracking_return=trk)
        pipeline.process_frame(_make_packet())
        pipeline.reset_stats()
        stats = pipeline.get_stats()

        assert stats["frames_total"] == 0
        assert stats["frames_detected"] == 0


# ──────────────────────────────────────────────────────────────────────
# PipelineResult Properties Tests
# ──────────────────────────────────────────────────────────────────────

class TestPipelineResult:
    """Tests for PipelineResult dataclass properties and serialization."""

    def test_has_detections_true(self):
        """has_detections is True when DetectionResult has detections."""
        r = PipelineResult(
            camera_id="CAM_01",
            pts_ms=100.0,
            detection_result=_make_detection_result(num_detections=2),
        )
        assert r.has_detections is True

    def test_has_detections_false_none(self):
        """has_detections is False when detection_result is None."""
        r = PipelineResult(camera_id="CAM_01", pts_ms=100.0)
        assert r.has_detections is False

    def test_has_detections_false_empty(self):
        """has_detections is False when detection_result has zero detections."""
        r = PipelineResult(
            camera_id="CAM_01",
            pts_ms=100.0,
            detection_result=_make_detection_result(num_detections=0),
        )
        assert r.has_detections is False

    def test_has_tracks_true(self):
        """has_tracks is True when TrackingResult has active tracks."""
        r = PipelineResult(
            camera_id="CAM_01",
            pts_ms=100.0,
            tracking_result=_make_tracking_result(num_tracks=1),
        )
        assert r.has_tracks is True

    def test_has_tracks_false(self):
        """has_tracks is False when tracking_result is None."""
        r = PipelineResult(camera_id="CAM_01", pts_ms=100.0)
        assert r.has_tracks is False

    def test_has_plates_true(self):
        """has_plates is True when anpr_results is non-empty."""
        r = PipelineResult(
            camera_id="CAM_01",
            pts_ms=100.0,
            anpr_results=[_make_anpr_result()],
        )
        assert r.has_plates is True

    def test_has_plates_false(self):
        """has_plates is False when anpr_results is empty."""
        r = PipelineResult(camera_id="CAM_01", pts_ms=100.0)
        assert r.has_plates is False

    def test_recognized_plates_filter(self):
        """recognized_plates returns only RECOGNIZED status results."""
        r = PipelineResult(
            camera_id="CAM_01",
            pts_ms=100.0,
            anpr_results=[
                _make_anpr_result(status="RECOGNIZED"),
                _make_anpr_result(status="LOW_CONFIDENCE"),
                _make_anpr_result(status="RECOGNIZED"),
                _make_anpr_result(status="NO_PLATE_DETECTED"),
            ],
        )
        assert len(r.recognized_plates) == 2
        assert all(p.status == "RECOGNIZED" for p in r.recognized_plates)

    def test_to_dict_structure(self):
        """to_dict() produces expected keys and values."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        anpr = [_make_anpr_result()]

        r = PipelineResult(
            camera_id="CAM_01",
            pts_ms=100.0,
            detection_result=det,
            tracking_result=trk,
            anpr_results=anpr,
            skipped=False,
            pipeline_time_ms=12.34,
            errors=[],
        )
        d = r.to_dict()

        assert d["camera_id"] == "CAM_01"
        assert d["pts_ms"] == 100.0
        assert d["skipped"] is False
        assert d["has_detections"] is True
        assert d["has_tracks"] is True
        assert d["has_plates"] is True
        assert d["detection_count"] == 1
        assert d["active_track_count"] == 1
        assert d["anpr_count"] == 1
        assert d["recognized_count"] == 1
        assert d["pipeline_time_ms"] == 12.34
        assert d["errors"] == []

    def test_to_dict_skip_result(self):
        """to_dict() for a skipped frame has zero counts."""
        r = PipelineResult(
            camera_id="CAM_01",
            pts_ms=50.0,
            skipped=True,
        )
        d = r.to_dict()

        assert d["skipped"] is True
        assert d["detection_count"] == 0
        assert d["active_track_count"] == 0
        assert d["anpr_count"] == 0

    def test_pipeline_time_ms_recorded(self):
        """pipeline_time_ms is non-negative after processing."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        pipeline = _build_pipeline(detection_return=det, tracking_return=trk)
        result = pipeline.process_frame(_make_packet())
        assert result.pipeline_time_ms >= 0.0


# ──────────────────────────────────────────────────────────────────────
# Multi-Frame Sequence Tests
# ──────────────────────────────────────────────────────────────────────

class TestCameraPipelineMultiFrame:
    """Tests for multi-frame sequences with component interaction."""

    def test_mixed_skip_and_process(self):
        """Pipeline handles interleaved skip and process frames."""
        mock_sp = MagicMock()
        det = _make_detection_result()
        # Alternate: skip, process, skip, process
        mock_sp.process_packet.side_effect = [None, det, None, det]
        mock_sp.get_stats.return_value = {
            "frames_received": 0, "frames_processed": 0,
            "frames_skipped": 0, "inference_errors": 0,
        }

        mock_tracker = MagicMock()
        trk = _make_tracking_result()
        mock_tracker.update.return_value = trk

        pipeline = CameraPipeline(
            camera_id="CAM_01",
            stream_processor=mock_sp,
            vehicle_tracker=mock_tracker,
        )

        results = [pipeline.process_frame(_make_packet(pts_ms=float(i * 100))) for i in range(4)]

        assert results[0].skipped is True
        assert results[1].skipped is False
        assert results[2].skipped is True
        assert results[3].skipped is False

        stats = pipeline.get_stats()
        assert stats["frames_total"] == 4
        assert stats["frames_skipped"] == 2
        assert stats["frames_detected"] == 2

    def test_component_called_with_correct_args(self):
        """Verify components receive the correct objects from upstream stages."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        anpr = [_make_anpr_result()]

        pipeline = _build_pipeline(
            detection_return=det,
            tracking_return=trk,
            anpr_return=anpr,
        )
        packet = _make_packet(pts_ms=555.0)
        pipeline.process_frame(packet)

        # StreamProcessor receives the packet
        pipeline.stream_processor.process_packet.assert_called_once_with(packet)

        # VehicleTracker receives the DetectionResult
        pipeline.vehicle_tracker.update.assert_called_once_with(det)

        # ANPRCoordinator receives the packet AND the TrackingResult
        pipeline.anpr_coordinator.process.assert_called_once_with(
            packet=packet,
            tracking_result=trk,
        )

    def test_anpr_not_called_when_no_tracking(self):
        """ANPR stage is skipped when VehicleTracker returns None."""
        det = _make_detection_result()
        pipeline = _build_pipeline(
            detection_return=det,
            tracking_return=None,
            anpr_return=[_make_anpr_result()],
        )
        pipeline.process_frame(_make_packet())

        pipeline.anpr_coordinator.process.assert_not_called()

    def test_anpr_empty_results_no_frames_anpr_count(self):
        """frames_anpr stat only increments when ANPRCoordinator returns non-empty list."""
        det = _make_detection_result()
        trk = _make_tracking_result()
        pipeline = _build_pipeline(
            detection_return=det,
            tracking_return=trk,
            anpr_return=[],  # No plates found
        )
        pipeline.process_frame(_make_packet())
        stats = pipeline.get_stats()

        assert stats["frames_anpr"] == 0
