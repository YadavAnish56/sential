"""
Unit tests for Sentinel Phase 5B Multi-Object Vehicle Tracking.

Tests cover:
- Track creation and lifecycle state machine (TENTATIVE -> CONFIRMED -> LOST -> TERMINATED)
- IoU association and deterministic matching
- Multiple concurrent vehicle tracking
- Missed detections, coasting, and recovery
- Stale track pruning
- Multi-camera state isolation
- Stream discontinuity flushing and monotonic ID retention
- Source PTS timestamp preservation
- Class-aware matching constraints
- Bounding box linear motion prediction
- Empty/malformed detections and dictionary serialization
"""

from __future__ import annotations

import pytest

from ai_engine.schemas import Detection, DetectionResult
from ai_engine.tracking import (
    TrackState,
    Track,
    TrackingResult,
    TrackerConfig,
    CameraTracker,
    VehicleTracker,
)
from ai_engine.tracking.matching import box_iou_batch, associate_detections_to_tracks


def _make_det(
    x1: float = 10.0,
    y1: float = 10.0,
    x2: float = 50.0,
    y2: float = 50.0,
    confidence: float = 0.85,
    class_id: int = 2,
    class_name: str = "car",
    camera_id: str = "CAM-01",
    pts_ms: float | None = 100.0,
) -> Detection:
    """Helper to create a Detection instance."""
    return Detection(
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        camera_id=camera_id,
        pts_ms=pts_ms,
    )


def _make_result(
    detections: list[Detection],
    camera_id: str = "CAM-01",
    pts_ms: float | None = 100.0,
    is_discontinuity: bool = False,
) -> DetectionResult:
    """Helper to create a DetectionResult instance."""
    return DetectionResult(
        camera_id=camera_id,
        pts_ms=pts_ms,
        detections=detections,
        inference_time_ms=8.5,
        device="cuda",
        model_name="yolov8n.pt",
        is_discontinuity=is_discontinuity,
    )


# 1. New Track Creation
def test_new_track_creation():
    tracker = CameraTracker(camera_id="CAM-01")
    det = _make_det(x1=10, y1=10, x2=50, y2=50, confidence=0.88, pts_ms=100.0)
    res = tracker.update(_make_result([det], pts_ms=100.0))

    assert len(res.active_tracks) == 1
    assert len(res.new_tracks) == 1
    assert len(res.lost_tracks) == 0

    trk = res.active_tracks[0]
    assert trk.track_id == 1
    assert trk.camera_id == "CAM-01"
    assert trk.state == TrackState.TENTATIVE
    assert trk.hits == 1
    assert trk.misses == 0
    assert trk.confidence == 0.88
    assert trk.best_confidence == 0.88
    assert trk.best_bbox == (10, 10, 50, 50)
    assert trk.first_pts_ms == 100.0
    assert trk.last_pts_ms == 100.0


# 2. Track Confirmation
def test_track_confirmation():
    tracker = CameraTracker(camera_id="CAM-01", config=TrackerConfig(min_hits=2))

    # Frame 1: Tentative
    res1 = tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50, pts_ms=100.0)], pts_ms=100.0))
    assert res1.active_tracks[0].state == TrackState.TENTATIVE

    # Frame 2: Confirmed
    res2 = tracker.update(_make_result([_make_det(x1=12, y1=11, x2=52, y2=51, pts_ms=133.3)], pts_ms=133.3))
    assert res2.active_tracks[0].state == TrackState.CONFIRMED
    assert res2.active_tracks[0].hits == 2
    assert res2.active_tracks[0].track_id == 1
    assert res2.active_tracks[0].is_confirmed is True


# 3. Track Matching IoU
def test_track_matching_iou():
    tracker = CameraTracker(camera_id="CAM-01")

    # Move vehicle slightly across 3 frames
    tracker.update(_make_result([_make_det(x1=100, y1=100, x2=200, y2=200, pts_ms=100.0)], pts_ms=100.0))
    tracker.update(_make_result([_make_det(x1=105, y1=105, x2=205, y2=205, pts_ms=133.3)], pts_ms=133.3))
    res3 = tracker.update(_make_result([_make_det(x1=110, y1=110, x2=210, y2=210, pts_ms=166.6)], pts_ms=166.6))

    assert len(res3.active_tracks) == 1
    trk = res3.active_tracks[0]
    assert trk.track_id == 1
    assert trk.hits == 3
    assert trk.bbox == (110, 110, 210, 210)


# 4. Multiple Vehicles Isolated Tracks
def test_multiple_vehicles_isolated_tracks():
    tracker = CameraTracker(camera_id="CAM-01")

    # 3 distinct vehicles
    d1 = _make_det(x1=10, y1=10, x2=50, y2=50, class_id=2, class_name="car")
    d2 = _make_det(x1=150, y1=150, x2=250, y2=250, class_id=3, class_name="motorcycle")
    d3 = _make_det(x1=400, y1=400, x2=600, y2=600, class_id=7, class_name="truck")

    res = tracker.update(_make_result([d1, d2, d3], pts_ms=100.0))

    assert len(res.active_tracks) == 3
    track_ids = {t.track_id for t in res.active_tracks}
    assert track_ids == {1, 2, 3}


# 5. Missed Detection Coasting
def test_missed_detection_coasting():
    tracker = CameraTracker(camera_id="CAM-01", config=TrackerConfig(min_hits=1))

    # Frame 1: Create and confirm track
    tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50)], pts_ms=100.0))

    # Frame 2: Empty detection (missed)
    res2 = tracker.update(_make_result([], pts_ms=133.3))

    assert len(res2.active_tracks) == 1
    trk = res2.active_tracks[0]
    assert trk.state == TrackState.LOST
    assert trk.misses == 1
    assert len(res2.lost_tracks) == 0  # Not terminated yet


# 6. Track Recovery from Lost
def test_track_recovery_from_lost():
    tracker = CameraTracker(camera_id="CAM-01", config=TrackerConfig(min_hits=1))

    # Active
    tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50)], pts_ms=100.0))
    # Lost
    tracker.update(_make_result([], pts_ms=133.3))
    # Recovered
    res3 = tracker.update(_make_result([_make_det(x1=12, y1=12, x2=52, y2=52)], pts_ms=166.6))

    assert len(res3.active_tracks) == 1
    trk = res3.active_tracks[0]
    assert trk.track_id == 1
    assert trk.state == TrackState.CONFIRMED
    assert trk.misses == 0
    assert trk.hits == 2


# 7. Stale Track Expiration
def test_stale_track_expiration():
    tracker = CameraTracker(camera_id="CAM-01", config=TrackerConfig(min_hits=1, max_misses=3))

    tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50)], pts_ms=100.0))

    # Miss 1, 2, 3: coasting
    tracker.update(_make_result([], pts_ms=133.3))
    tracker.update(_make_result([], pts_ms=166.6))
    tracker.update(_make_result([], pts_ms=200.0))

    # Miss 4: Exceeds max_misses=3 -> Terminated
    res5 = tracker.update(_make_result([], pts_ms=233.3))

    assert len(res5.active_tracks) == 0
    assert len(res5.lost_tracks) == 1
    assert res5.lost_tracks[0].track_id == 1
    assert res5.lost_tracks[0].state == TrackState.TERMINATED


# 8. Multi-Camera Isolation
def test_multi_camera_isolation():
    vt = VehicleTracker()

    # Camera 1 detection
    res_cam1 = vt.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50, camera_id="CAM-NORTH")], camera_id="CAM-NORTH", pts_ms=100.0))
    # Camera 2 detection
    res_cam2 = vt.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50, camera_id="CAM-SOUTH")], camera_id="CAM-SOUTH", pts_ms=100.0))

    # Both start with track ID 1 locally in their own camera tracker
    assert res_cam1.active_tracks[0].camera_id == "CAM-NORTH"
    assert res_cam1.active_tracks[0].track_id == 1

    assert res_cam2.active_tracks[0].camera_id == "CAM-SOUTH"
    assert res_cam2.active_tracks[0].track_id == 1

    # Update Cam 1 only: Cam 2 remains unaffected
    res_cam1_v2 = vt.update(_make_result([_make_det(x1=15, y1=15, x2=55, y2=55, camera_id="CAM-NORTH")], camera_id="CAM-NORTH", pts_ms=133.3))
    assert res_cam1_v2.active_tracks[0].hits == 2

    cam2_tracker = vt.get_tracker("CAM-SOUTH")
    assert cam2_tracker.active_track_count == 1
    assert list(cam2_tracker._active_tracks.values())[0].hits == 1


# 9. Discontinuity Flushes Tracks
def test_discontinuity_flushes_tracks():
    tracker = CameraTracker(camera_id="CAM-01")

    # Frame 1: Normal track 1
    tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50)], pts_ms=1000.0))
    assert tracker.active_track_count == 1

    # Frame 2: Stream loop or jump with is_discontinuity=True
    new_det = _make_det(x1=200, y1=200, x2=300, y2=300, pts_ms=100.0)
    disc_res = tracker.update(_make_result([new_det], pts_ms=100.0, is_discontinuity=True))

    # Old track should be in lost_tracks and TERMINATED
    assert len(disc_res.lost_tracks) == 1
    assert disc_res.lost_tracks[0].track_id == 1
    assert disc_res.lost_tracks[0].state == TrackState.TERMINATED

    # New track should be created with track_id = 2 (NOT reset to 1)
    assert len(disc_res.active_tracks) == 1
    assert disc_res.active_tracks[0].track_id == 2
    assert disc_res.is_discontinuity is True


# 10. PTS Timestamp Preservation
def test_pts_timestamp_preservation():
    tracker = CameraTracker(camera_id="CAM-01")

    tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50, pts_ms=5000.5)], pts_ms=5000.5))
    res = tracker.update(_make_result([_make_det(x1=12, y1=12, x2=52, y2=52, pts_ms=5040.0)], pts_ms=5040.0))

    trk = res.active_tracks[0]
    assert trk.first_pts_ms == 5000.5
    assert trk.last_pts_ms == 5040.0


# 11. Empty and Malformed Detections
def test_empty_and_malformed_detections():
    tracker = CameraTracker(camera_id="CAM-01")

    # Zero-area box detection
    malformed_det = _make_det(x1=50, y1=50, x2=50, y2=50)  # width=0, height=0
    res1 = tracker.update(_make_result([malformed_det]))
    assert len(res1.active_tracks) == 1

    # None pts_ms
    res2 = tracker.update(_make_result([], pts_ms=None))
    assert res2.pts_ms is None

    # Completely empty result on fresh tracker
    fresh_tracker = CameraTracker(camera_id="CAM-EMPTY")
    empty_res = fresh_tracker.update(_make_result([]))
    assert len(empty_res.active_tracks) == 0
    assert len(empty_res.new_tracks) == 0
    assert len(empty_res.lost_tracks) == 0


# 12. Class-Aware Matching
def test_class_aware_matching():
    tracker = CameraTracker(camera_id="CAM-01", config=TrackerConfig(match_class=True))

    # Frame 1: Car at (10, 10, 50, 50)
    tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50, class_id=2, class_name="car")]))

    # Frame 2: Bus at exact same coordinates (class_id=5)
    res2 = tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50, class_id=5, class_name="bus")]))

    # Must NOT associate with track 1 because classes differ!
    # Track 1 (car) becomes LOST, and Bus becomes new Track 2
    active_class_names = {t.class_name for t in res2.active_tracks}
    assert "car" in active_class_names
    assert "bus" in active_class_names
    assert len(res2.active_tracks) == 2


# 13. Linear BBox Motion Prediction
def test_linear_bbox_prediction():
    tracker = CameraTracker(camera_id="CAM-01", config=TrackerConfig(use_motion_prediction=True))

    # Vehicle moving 20px horizontally per 100ms
    tracker.update(_make_result([_make_det(x1=100, y1=100, x2=200, y2=200)], pts_ms=100.0))
    tracker.update(_make_result([_make_det(x1=120, y1=100, x2=220, y2=200)], pts_ms=200.0))

    # Now on frame 3 at pts=300ms, predicted box should be around x1=140
    trk = list(tracker._active_tracks.values())[0]
    pred = tracker._predict_box(trk, current_pts_ms=300.0)
    assert pred[0] == pytest.approx(140.0, abs=1.0)
    assert pred[2] == pytest.approx(240.0, abs=1.0)


# 14. Best BBox Update on Higher Confidence
def test_best_bbox_confidence_update():
    tracker = CameraTracker(camera_id="CAM-01")

    # Initial detection confidence 0.70
    tracker.update(_make_result([_make_det(x1=10, y1=10, x2=50, y2=50, confidence=0.70)]))
    trk = list(tracker._active_tracks.values())[0]
    assert trk.best_confidence == 0.70
    assert trk.best_bbox == (10, 10, 50, 50)

    # Subsequent detection confidence 0.95 (better angle/crop)
    tracker.update(_make_result([_make_det(x1=12, y1=12, x2=52, y2=52, confidence=0.95)]))
    assert trk.best_confidence == 0.95
    assert trk.best_bbox == (12, 12, 52, 52)

    # Lower confidence 0.60 does not overwrite best
    tracker.update(_make_result([_make_det(x1=14, y1=14, x2=54, y2=54, confidence=0.60)]))
    assert trk.best_confidence == 0.95
    assert trk.best_bbox == (12, 12, 52, 52)


# 15. Tracking Result Serialization
def test_tracking_result_serialization():
    tracker = CameraTracker(camera_id="CAM-TEST")
    res = tracker.update(_make_result([_make_det(camera_id="CAM-TEST")], camera_id="CAM-TEST"))

    d = res.to_dict()
    assert d["camera_id"] == "CAM-TEST"
    assert d["active_count"] == 1
    assert len(d["active_tracks"]) == 1
    assert "bbox" in d["active_tracks"][0]
    assert "best_bbox" in d["active_tracks"][0]
    assert "state" in d["active_tracks"][0]
