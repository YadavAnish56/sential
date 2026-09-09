"""
Multi-Camera Isolation and Validation Test Suite for Sentinel (Phase 6D.5).

Proves that independent camera sessions (e.g. CAM-A and CAM-B) can be processed
in an interleaved sequence without state leakage, cross-talk, or contamination across:
- PTS-based sampling state
- Track ID generation and track lifecycle
- Active/lost track dictionaries
- Stream discontinuity handling
- ANPR OCR evaluation and plate locking
- Component and telemetry instances
- Failure isolation
- Absence of hidden global/shared state
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streaming.frame_reader import FramePacket
from ai_engine.schemas import Detection, DetectionResult
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import VehicleTracker, TrackerConfig
from ai_engine.tracking.schemas import TrackState, TrackingResult
from ai_engine.anpr import ANPRCoordinator, ANPRConfig
from ai_engine.anpr.schemas import PlateCandidate, ANPRResult
from ai_engine.anpr.recognizer import MockPlateRecognizer
from ai_engine.pipeline import CameraPipeline, PipelineResult


# ─────────────────────────────────────────────────────────────────────────────
# Test Doubles & Factories
# ─────────────────────────────────────────────────────────────────────────────

class DeterministicDetectorStub:
    """
    Deterministic in-memory vehicle detector test double.
    Produces pre-configured vehicle detections tagged with the frame's metadata.
    """

    def __init__(
        self,
        detections: list[Detection] | None = None,
        should_fail: bool = False,
    ) -> None:
        self.detections = detections or []
        self.should_fail = should_fail
        self.call_count: int = 0

    def detect(self, packet: FramePacket) -> DetectionResult:
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("Synthetic detector hardware error")

        current_detections = [
            Detection(
                class_id=d.class_id,
                class_name=d.class_name,
                confidence=d.confidence,
                x1=d.x1,
                y1=d.y1,
                x2=d.x2,
                y2=d.y2,
                camera_id=packet.camera_id,
                pts_ms=packet.pts_ms,
            )
            for d in self.detections
        ]
        return DetectionResult(
            camera_id=packet.camera_id,
            pts_ms=packet.pts_ms,
            detections=current_detections,
            inference_time_ms=2.0,
            device="cpu",
            model_name="deterministic-stub",
            is_discontinuity=packet.is_discontinuity,
        )


def make_packet(
    camera_id: str,
    pts_ms: float,
    width: int = 1280,
    height: int = 720,
    is_discontinuity: bool = False,
    seed: int = 42,
) -> FramePacket:
    """Create a high-contrast synthetic FramePacket for a specific camera."""
    rng = np.random.RandomState(seed)
    frame = rng.randint(0, 255, (height, width, 3), dtype=np.uint8)
    return FramePacket(
        frame=frame,
        pts_ms=pts_ms,
        received_at=time.time(),
        width=width,
        height=height,
        camera_id=camera_id,
        is_discontinuity=is_discontinuity,
    )


def create_session(
    camera_id: str,
    target_fps: float | None = None,
    detections: list[Detection] | None = None,
    candidate: PlateCandidate | None = None,
    should_fail_detector: bool = False,
    min_hits: int = 1,
) -> tuple[CameraPipeline, DeterministicDetectorStub, MockPlateRecognizer]:
    """Factory creating an isolated per-camera CameraPipeline session with its own components."""
    default_dets = detections if detections is not None else [
        Detection(
            class_id=2,
            class_name="car",
            confidence=0.90,
            x1=200.0,
            y1=200.0,
            x2=600.0,
            y2=500.0,
            camera_id=camera_id,
            pts_ms=1000.0,
        )
    ]
    detector = DeterministicDetectorStub(detections=default_dets, should_fail=should_fail_detector)
    sp_config = StreamProcessorConfig(frame_stride=1, target_fps=target_fps)
    stream_processor = StreamProcessor(detector=detector, config=sp_config)

    vehicle_tracker = VehicleTracker(config=TrackerConfig(min_hits=min_hits))

    default_cand = candidate if candidate is not None else PlateCandidate(
        raw_text=f"GJ05{camera_id[-1]}1234",
        normalized_plate=f"GJ05{camera_id[-1]}1234",
        confidence=0.92,
        is_valid_format=True,
        pts_ms=1000.0,
    )
    recognizer = MockPlateRecognizer(default_candidate=default_cand)
    anpr_coordinator = ANPRCoordinator(
        recognizer=recognizer,
        config=ANPRConfig(min_confidence=0.70, min_crop_quality=0.10),
    )

    pipeline = CameraPipeline(
        camera_id=camera_id,
        stream_processor=stream_processor,
        vehicle_tracker=vehicle_tracker,
        anpr_coordinator=anpr_coordinator,
    )
    return pipeline, detector, recognizer


# ─────────────────────────────────────────────────────────────────────────────
# 1. PTS State Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_pts_sampling_state_isolated_across_interleaved_cameras():
    """
    Processing Camera A frames must not update Camera B's last_processed_pts_ms or alter
    Camera B's target_fps sampling decisions.
    """
    # 5.0 target FPS -> minimum interval between processed frames is 200 ms
    pipeline_a, _, _ = create_session(camera_id="CAM-A", target_fps=5.0)
    pipeline_b, _, _ = create_session(camera_id="CAM-B", target_fps=5.0)

    # Interleaved sequence:
    # A1 at PTS=1000.0 -> processed (first frame)
    res_a1 = pipeline_a.process_frame(make_packet("CAM-A", pts_ms=1000.0))
    assert res_a1.skipped is False

    # B1 at PTS=5000.0 -> processed (first frame for B, completely different PTS timeline)
    res_b1 = pipeline_b.process_frame(make_packet("CAM-B", pts_ms=5000.0))
    assert res_b1.skipped is False

    # A2 at PTS=1100.0 -> delta from A1 is 100 ms < 200 ms -> must be SKIPPED
    # If state leaked from B, delta would be compared to 5000.0 (delta=-3900 ms -> would re-anchor and process!)
    res_a2 = pipeline_a.process_frame(make_packet("CAM-A", pts_ms=1100.0))
    assert res_a2.skipped is True

    # B2 at PTS=5250.0 -> delta from B1 is 250 ms >= 200 ms -> must be PROCESSED
    res_b2 = pipeline_b.process_frame(make_packet("CAM-B", pts_ms=5250.0))
    assert res_b2.skipped is False

    # A3 at PTS=1250.0 -> delta from A1 is 250 ms >= 200 ms -> must be PROCESSED
    res_a3 = pipeline_a.process_frame(make_packet("CAM-A", pts_ms=1250.0))
    assert res_a3.skipped is False

    # Verify telemetry counts are strictly isolated
    assert pipeline_a.get_stats()["frames_total"] == 3
    assert pipeline_a.get_stats()["frames_detected"] == 2
    assert pipeline_a.get_stats()["frames_skipped"] == 1
    assert pipeline_a.stream_processor.frames_processed == 2

    assert pipeline_b.get_stats()["frames_total"] == 2
    assert pipeline_b.get_stats()["frames_detected"] == 2
    assert pipeline_b.get_stats()["frames_skipped"] == 0
    assert pipeline_b.stream_processor.frames_processed == 2


# ─────────────────────────────────────────────────────────────────────────────
# 2. Track ID Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_track_id_counter_isolated_per_camera():
    """
    Each camera tracker starts its monotonic track ID counter at 1 and increments independently.
    Camera A getting tracks 1, 2 does NOT advance Camera B's counter to 3.
    """
    det_a1 = [Detection(class_id=2, class_name="car", confidence=0.9, x1=10, y1=10, x2=50, y2=50, camera_id="CAM-A", pts_ms=100.0)]
    det_a2 = [Detection(class_id=2, class_name="car", confidence=0.9, x1=500, y1=500, x2=600, y2=600, camera_id="CAM-A", pts_ms=100.0)]

    det_b1 = [Detection(class_id=2, class_name="car", confidence=0.9, x1=100, y1=100, x2=150, y2=150, camera_id="CAM-B", pts_ms=100.0)]

    pipeline_a, _, _ = create_session(camera_id="CAM-A", detections=det_a1 + det_a2)
    pipeline_b, _, _ = create_session(camera_id="CAM-B", detections=det_b1)

    # Frame A1 creates tracks 1 and 2 for Camera A
    res_a = pipeline_a.process_frame(make_packet("CAM-A", pts_ms=100.0))
    assert res_a.tracking_result is not None
    track_ids_a = {t.track_id for t in res_a.tracking_result.active_tracks}
    assert track_ids_a == {1, 2}

    # Frame B1 creates track 1 for Camera B (independent monotonic counter)
    res_b = pipeline_b.process_frame(make_packet("CAM-B", pts_ms=100.0))
    assert res_b.tracking_result is not None
    track_ids_b = {t.track_id for t in res_b.tracking_result.active_tracks}
    assert track_ids_b == {1}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Track State Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_track_lifecycle_and_motion_state_isolated_across_cameras():
    """
    Camera B frames (even with empty detections causing misses) must not alter
    Camera A's active tracks, hit counters, miss counters, or predicted bounding boxes.
    """
    det_a = [Detection(class_id=2, class_name="car", confidence=0.9, x1=100, y1=100, x2=200, y2=200, camera_id="CAM-A", pts_ms=100.0)]
    pipeline_a, _, _ = create_session(camera_id="CAM-A", detections=det_a, min_hits=2)
    # Camera B with zero detections
    pipeline_b, _, _ = create_session(camera_id="CAM-B", detections=[], min_hits=1)

    # Frame A1 (hits=1, TENTATIVE)
    pipeline_a.process_frame(make_packet("CAM-A", pts_ms=100.0))

    # Multiple frames on Camera B with no detections (would cause misses if state was shared)
    for pts in [150.0, 200.0, 250.0, 300.0]:
        pipeline_b.process_frame(make_packet("CAM-B", pts_ms=pts))

    # Check Camera A track state
    tracker_a = pipeline_a.vehicle_tracker.get_tracker("CAM-A")
    assert 1 in tracker_a._active_tracks
    track_a = tracker_a._active_tracks[1]
    assert track_a.hits == 1
    assert track_a.misses == 0
    assert track_a.state == TrackState.TENTATIVE

    # Frame A2 confirms track A (hits=2, CONFIRMED)
    res_a2 = pipeline_a.process_frame(make_packet("CAM-A", pts_ms=350.0))
    assert res_a2.tracking_result.active_tracks[0].state == TrackState.CONFIRMED

    # Camera B still has 0 active tracks
    tracker_b = pipeline_b.vehicle_tracker.get_tracker("CAM-B")
    assert tracker_b.active_track_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. Discontinuity Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_discontinuity_on_camera_a_does_not_flush_camera_b():
    """
    Triggering a stream discontinuity on Camera A must flush Camera A's tracks
    while leaving Camera B's active confirmed tracks intact and unchanged.
    """
    pipeline_a, _, _ = create_session(camera_id="CAM-A", min_hits=1)
    pipeline_b, _, _ = create_session(camera_id="CAM-B", min_hits=1)

    # Initialize both cameras with active confirmed tracks
    res_a1 = pipeline_a.process_frame(make_packet("CAM-A", pts_ms=1000.0))
    res_b1 = pipeline_b.process_frame(make_packet("CAM-B", pts_ms=1000.0))

    assert res_a1.tracking_result.active_count == 1
    assert res_b1.tracking_result.active_count == 1

    # Discontinuity on Camera A (e.g. RTSP reconnect / PTS jump)
    packet_disc_a = make_packet("CAM-A", pts_ms=99000.0, is_discontinuity=True)
    res_a_disc = pipeline_a.process_frame(packet_disc_a)

    # Camera A should report discontinuity, terminated old track into lost_tracks
    assert res_a_disc.tracking_result.is_discontinuity is True

    # Camera B frame: verify Camera B's active track is unaffected
    packet_b2 = make_packet("CAM-B", pts_ms=1040.0, is_discontinuity=False)
    res_b2 = pipeline_b.process_frame(packet_b2)

    assert res_b2.tracking_result.is_discontinuity is False
    assert res_b2.tracking_result.active_count == 1
    assert res_b2.tracking_result.active_tracks[0].track_id == 1
    assert res_b2.tracking_result.active_tracks[0].state == TrackState.CONFIRMED


# ─────────────────────────────────────────────────────────────────────────────
# 5. ANPR State Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_anpr_recognition_and_locking_isolated_between_cameras():
    """
    ANPRCoordinator state (locks, candidate history, attempt count) must remain strictly
    isolated between cameras. A plate recognized on Camera A cannot lock Camera B.
    """
    cand_a = PlateCandidate(
        raw_text="GJ05AA1111",
        normalized_plate="GJ05AA1111",
        confidence=0.95,
        is_valid_format=True,
        pts_ms=1000.0,
    )
    cand_b = PlateCandidate(
        raw_text="MH12BB2222",
        normalized_plate="MH12BB2222",
        confidence=0.91,
        is_valid_format=True,
        pts_ms=1000.0,
    )

    pipeline_a, _, _ = create_session(camera_id="CAM-A", candidate=cand_a)
    pipeline_b, _, _ = create_session(camera_id="CAM-B", candidate=cand_b)

    # Process Camera A -> locks track 1 with GJ05AA1111
    res_a = pipeline_a.process_frame(make_packet("CAM-A", pts_ms=1000.0))
    assert len(res_a.recognized_plates) == 1
    assert res_a.recognized_plates[0].normalized_plate == "GJ05AA1111"
    assert res_a.recognized_plates[0].camera_id == "CAM-A"

    state_a = pipeline_a.anpr_coordinator.get_track_state("CAM-A", 1)
    assert state_a is not None
    assert state_a.locked is True
    assert state_a.recognized_plate == "GJ05AA1111"

    # Process Camera B -> recognizes MH12BB2222 independently
    res_b = pipeline_b.process_frame(make_packet("CAM-B", pts_ms=1000.0))
    assert len(res_b.recognized_plates) == 1
    assert res_b.recognized_plates[0].normalized_plate == "MH12BB2222"
    assert res_b.recognized_plates[0].camera_id == "CAM-B"

    state_b = pipeline_b.anpr_coordinator.get_track_state("CAM-B", 1)
    assert state_b is not None
    assert state_b.locked is True
    assert state_b.recognized_plate == "MH12BB2222"

    # Cross-check: Camera A's coordinator has no state for CAM-B
    assert pipeline_a.anpr_coordinator.get_track_state("CAM-B", 1) is None
    assert pipeline_b.anpr_coordinator.get_track_state("CAM-A", 1) is None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Pipeline Metadata Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_interleaved_frames_strictly_preserve_camera_metadata():
    """
    Every output PipelineResult, DetectionResult, TrackingResult, and ANPRResult
    must preserve the exact camera_id and pts_ms of its source FramePacket.
    """
    pipeline_a, _, _ = create_session(camera_id="CAM-A")
    pipeline_b, _, _ = create_session(camera_id="CAM-B")

    timeline = [
        ("CAM-A", pipeline_a, 100.0),
        ("CAM-B", pipeline_b, 105.0),
        ("CAM-A", pipeline_a, 200.0),
        ("CAM-B", pipeline_b, 205.0),
        ("CAM-A", pipeline_a, 300.0),
        ("CAM-B", pipeline_b, 305.0),
    ]

    for expected_cam, pipe, pts in timeline:
        pkt = make_packet(expected_cam, pts_ms=pts)
        res = pipe.process_frame(pkt)

        # Verify PipelineResult level
        assert res.camera_id == expected_cam
        assert res.pts_ms == pts

        # Verify DetectionResult level
        assert res.detection_result.camera_id == expected_cam
        assert res.detection_result.pts_ms == pts

        # Verify TrackingResult level
        assert res.tracking_result.camera_id == expected_cam
        assert res.tracking_result.pts_ms == pts

        # Verify ANPRResult level
        for anpr in res.anpr_results:
            assert anpr.camera_id == expected_cam
            assert anpr.pts_ms == pts


# ─────────────────────────────────────────────────────────────────────────────
# 7. Pipeline Instance Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_pipeline_components_are_distinct_instances():
    """
    Verify the two CameraPipeline instances own completely distinct component
    instances (StreamProcessor, VehicleTracker, ANPRCoordinator, detector, recognizer).
    """
    pipeline_a, det_a, rec_a = create_session(camera_id="CAM-A")
    pipeline_b, det_b, rec_b = create_session(camera_id="CAM-B")

    assert pipeline_a is not pipeline_b
    assert pipeline_a.stream_processor is not pipeline_b.stream_processor
    assert pipeline_a.vehicle_tracker is not pipeline_b.vehicle_tracker
    assert pipeline_a.anpr_coordinator is not pipeline_b.anpr_coordinator
    assert det_a is not det_b
    assert rec_a is not rec_b


# ─────────────────────────────────────────────────────────────────────────────
# 8. Failure Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_camera_a_failure_does_not_affect_camera_b():
    """
    A hardware or detector failure on Camera A must be isolated by Camera A's pipeline
    and must not impact, crash, or disable Camera B's processing session.
    """
    pipeline_a, _, _ = create_session(camera_id="CAM-A", should_fail_detector=True)
    pipeline_b, _, _ = create_session(camera_id="CAM-B", should_fail_detector=False)

    # Process failing frame on Camera A
    pkt_a = make_packet("CAM-A", pts_ms=100.0)
    res_a = pipeline_a.process_frame(pkt_a)

    assert res_a.skipped is True
    assert pipeline_a.stream_processor.inference_errors == 1
    assert len(res_a.anpr_results) == 0

    # Process healthy frame on Camera B
    pkt_b = make_packet("CAM-B", pts_ms=100.0)
    res_b = pipeline_b.process_frame(pkt_b)

    assert res_b.skipped is False
    assert res_b.has_detections is True
    assert res_b.has_tracks is True
    assert res_b.has_plates is True
    assert len(res_b.errors) == 0
    assert pipeline_b.stream_processor.inference_errors == 0


# ─────────────────────────────────────────────────────────────────────────────
# 9. Absence of Hidden Global/Shared State
# ─────────────────────────────────────────────────────────────────────────────

def test_no_hidden_global_state_across_session_lifecycles():
    """
    Instantiating and executing a session, discarding it, and creating a new session
    starts with completely pristine counters, without state lingering in module globals.
    """
    # Session 1
    pipe1, _, _ = create_session(camera_id="CAM-A")
    for pts in [100.0, 200.0, 300.0]:
        pipe1.process_frame(make_packet("CAM-A", pts_ms=pts))

    stats1 = pipe1.get_stats()
    assert stats1["frames_total"] == 3
    assert stats1["stream_processor_stats"]["frames_received"] == 3

    # Session 2 for same camera_id must be completely pristine
    pipe2, _, _ = create_session(camera_id="CAM-A")
    stats2 = pipe2.get_stats()
    assert stats2["frames_total"] == 0
    assert stats2["stream_processor_stats"]["frames_received"] == 0
    assert pipe2.stream_processor._last_processed_pts_ms is None
    assert pipe2.vehicle_tracker.get_tracker("CAM-A").active_track_count == 0
    assert pipe2.vehicle_tracker.get_tracker("CAM-A").next_track_id == 1


# ─────────────────────────────────────────────────────────────────────────────
# 10. Multi-Frame Stress Interleaving Test
# ─────────────────────────────────────────────────────────────────────────────

def test_ten_frame_stress_interleaving():
    """
    Simulate 10 consecutive interleaved frames (A1 B1 A2 B2 ... A10 B10).
    Verify all 20 frames maintain strict camera isolation, correct track continuation,
    and exact telemetry counts.
    """
    cand_a = PlateCandidate(raw_text="GJ01AA1111", normalized_plate="GJ01AA1111", confidence=0.92, is_valid_format=True, pts_ms=1000.0)
    cand_b = PlateCandidate(raw_text="MH12BB2222", normalized_plate="MH12BB2222", confidence=0.94, is_valid_format=True, pts_ms=1000.0)

    # Car A moving linearly across frames
    pipeline_a, _, _ = create_session(camera_id="CAM-A", candidate=cand_a)
    pipeline_b, _, _ = create_session(camera_id="CAM-B", candidate=cand_b)

    for i in range(10):
        pts_a = 1000.0 + i * 40.0  # 25 FPS
        pts_b = 5000.0 + i * 40.0

        pkt_a = make_packet("CAM-A", pts_ms=pts_a, seed=100 + i)
        pkt_b = make_packet("CAM-B", pts_ms=pts_b, seed=200 + i)

        res_a = pipeline_a.process_frame(pkt_a)
        res_b = pipeline_b.process_frame(pkt_b)

        assert res_a.camera_id == "CAM-A"
        assert res_a.pts_ms == pts_a
        assert res_b.camera_id == "CAM-B"
        assert res_b.pts_ms == pts_b

    # Verify telemetry
    stats_a = pipeline_a.get_stats()
    stats_b = pipeline_b.get_stats()

    assert stats_a["frames_total"] == 10
    assert stats_a["frames_detected"] == 10
    assert stats_a["frames_tracked"] == 10
    assert stats_a["total_errors"] == 0

    assert stats_b["frames_total"] == 10
    assert stats_b["frames_detected"] == 10
    assert stats_b["frames_tracked"] == 10
    assert stats_b["total_errors"] == 0
