"""
Unit and integration tests for Phase 29D Calibrated ANPR Consensus Threshold.

Validates the calibrated empirical policy:
- candidate admission floor = 0.20 (confidence < 0.20 excluded, >= 0.20 admitted)
- minimum agreeing observations = 3
- minimum agreement ratio = 60%
- high-confidence FAST_LOCK preserved (>= 0.85)
- strict Indian plate validation maintained
- confirmed vehicle track required
- persistence and watchlist safeguards preserved

Covers the 10 mandatory Phase 29D test scenarios:
1. confidence 0.19: excluded from candidate buffer
2. confidence 0.20: admitted to candidate buffer
3. 3 agreeing observations >= 0.20: eligible for recognition when all rules pass
4. 2 agreeing observations: NOT recognized
5. 3 observations with agreement below 60%: NOT recognized
6. low-confidence random/noisy observations: do not form recognition
7. existing high-confidence FAST_LOCK behavior remains unchanged
8. existing valid plates remain unchanged
9. legitimate numeric 6 in non-state positions remains unchanged
10. watchlist alert only occurs after genuine recognition/persistence
"""

import time
import numpy as np
import pytest
from unittest.mock import MagicMock

from streaming.frame_reader import FramePacket
from ai_engine.tracking.schemas import Track, TrackState, TrackingResult
from ai_engine.anpr.schemas import PlateCandidate, ANPRResult, ANPRConfig
from ai_engine.anpr.normalization import normalize_plate, validate_indian_plate_format
from ai_engine.anpr.recognizer import MockPlateRecognizer
from ai_engine.anpr.coordinator import (
    ANPRCoordinator,
    TrackPlateObservation,
    resolve_temporal_consensus,
)
from ai_engine.persistence_dispatcher import (
    PersistenceDispatcher,
    is_eligible_for_persistence,
)


def _make_frame(w: int = 640, h: int = 480) -> np.ndarray:
    frame = np.full((h, w, 3), 128, dtype=np.uint8)
    frame[50:200, 50:250] = 220
    frame[80:160, 80:200] = 40
    return frame


def _make_packet(frame: np.ndarray, pts_ms: float = 1000.0, camera_id: str = "CAM-006") -> FramePacket:
    h, w = frame.shape[:2]
    return FramePacket(
        frame=frame,
        pts_ms=pts_ms,
        received_at=time.time(),
        width=w,
        height=h,
        camera_id=camera_id,
    )


def _make_track(
    track_id: int = 1,
    camera_id: str = "CAM-006",
    state: TrackState = TrackState.CONFIRMED,
    pts_ms: float = 1000.0,
) -> Track:
    return Track(
        track_id=track_id,
        camera_id=camera_id,
        class_id=2,
        class_name="car",
        confidence=0.88,
        bbox=(50.0, 50.0, 250.0, 200.0),
        state=state,
        first_pts_ms=pts_ms,
        last_pts_ms=pts_ms,
        hits=5 if state == TrackState.CONFIRMED else 1,
        misses=0,
        best_confidence=0.88,
        best_bbox=(50.0, 50.0, 250.0, 200.0),
    )


# 1. confidence 0.19: excluded from candidate buffer
def test_1_confidence_019_excluded_from_candidate_buffer():
    frame = _make_frame()
    track = _make_track(track_id=10, pts_ms=100.0)
    packet = _make_packet(frame, pts_ms=100.0)

    # Candidate confidence 0.19 < 0.20 admission floor
    cand = PlateCandidate(
        raw_text="GJ05AB1234",
        normalized_plate="GJ05AB1234",
        confidence=0.19,
        is_valid_format=True,
        pts_ms=100.0,
    )
    recognizer = MockPlateRecognizer(responses=[cand])
    coordinator = ANPRCoordinator(recognizer=recognizer)

    results = coordinator.process(
        packet,
        TrackingResult(camera_id="CAM-006", pts_ms=100.0, active_tracks=[track]),
    )

    assert len(results) == 1
    assert results[0].status == "LOW_CONFIDENCE"
    assert results[0].confidence == pytest.approx(0.19)

    state = coordinator.get_track_state("CAM-006", 10)
    assert state is not None
    # CRITICAL: Observation buffer MUST be empty because 0.19 < 0.20
    assert len(state.observations) == 0
    assert state.locked is False


# 2. confidence 0.20: admitted to candidate buffer
def test_2_confidence_020_admitted_to_candidate_buffer():
    frame = _make_frame()
    track = _make_track(track_id=20, pts_ms=100.0)
    packet = _make_packet(frame, pts_ms=100.0)

    # Candidate confidence 0.20 == 0.20 admission floor
    cand = PlateCandidate(
        raw_text="GJ05AB1234",
        normalized_plate="GJ05AB1234",
        confidence=0.20,
        is_valid_format=True,
        pts_ms=100.0,
    )
    recognizer = MockPlateRecognizer(responses=[cand])
    coordinator = ANPRCoordinator(recognizer=recognizer)

    results = coordinator.process(
        packet,
        TrackingResult(camera_id="CAM-006", pts_ms=100.0, active_tracks=[track]),
    )

    assert len(results) == 1
    # Single observation at 0.20 is not yet recognized (needs 3 agreeing observations)
    assert results[0].status == "LOW_CONFIDENCE"

    state = coordinator.get_track_state("CAM-006", 20)
    assert state is not None
    # CRITICAL: Observation buffer admitted the candidate
    assert len(state.observations) == 1
    assert state.observations[0].ocr_confidence == pytest.approx(0.20)
    assert state.observations[0].normalized_plate == "GJ05AB1234"
    assert state.locked is False


# 3. 3 agreeing observations >= 0.20: eligible for recognition when all other existing rules pass
def test_3_three_agreeing_observations_at_020_eligible_for_recognition():
    frame = _make_frame()
    track = _make_track(track_id=30, pts_ms=100.0)

    # 3 sequential agreeing observations at calibrated floor 0.20
    cand1 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.20, is_valid_format=True, pts_ms=100.0)
    cand2 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.21, is_valid_format=True, pts_ms=300.0)
    cand3 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.20, is_valid_format=True, pts_ms=500.0)

    recognizer = MockPlateRecognizer(responses=[cand1, cand2, cand3])
    coordinator = ANPRCoordinator(
        recognizer=recognizer,
        config=ANPRConfig(min_attempt_spacing_ms=100.0),
    )

    # Frame 1: Observation 1
    r1 = coordinator.process(
        _make_packet(frame, pts_ms=100.0),
        TrackingResult(camera_id="CAM-006", pts_ms=100.0, active_tracks=[track]),
    )
    assert len(r1) == 1
    assert r1[0].status == "LOW_CONFIDENCE"

    # Frame 2: Observation 2
    r2 = coordinator.process(
        _make_packet(frame, pts_ms=300.0),
        TrackingResult(camera_id="CAM-006", pts_ms=300.0, active_tracks=[track]),
    )
    assert len(r2) == 1
    assert r2[0].status == "LOW_CONFIDENCE"

    # Frame 3: Observation 3 -> 3 agreeing observations reached!
    r3 = coordinator.process(
        _make_packet(frame, pts_ms=500.0),
        TrackingResult(camera_id="CAM-006", pts_ms=500.0, active_tracks=[track]),
    )
    assert len(r3) == 1
    assert r3[0].status == "RECOGNIZED"
    assert r3[0].normalized_plate == "GJ05AB1234"

    state = coordinator.get_track_state("CAM-006", 30)
    assert state is not None
    assert state.locked is True
    assert state.lock_method == "CONSENSUS_LOCK"
    assert state.recognized_plate == "GJ05AB1234"
    assert len(state.observations) == 3


# 4. 2 agreeing observations: NOT recognized
def test_4_two_agreeing_observations_not_recognized():
    frame = _make_frame()
    track = _make_track(track_id=40, pts_ms=100.0)

    cand1 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.25, is_valid_format=True, pts_ms=100.0)
    cand2 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.28, is_valid_format=True, pts_ms=300.0)

    recognizer = MockPlateRecognizer(responses=[cand1, cand2])
    coordinator = ANPRCoordinator(
        recognizer=recognizer,
        config=ANPRConfig(min_attempt_spacing_ms=100.0),
    )

    r1 = coordinator.process(_make_packet(frame, pts_ms=100.0), TrackingResult(camera_id="CAM-006", pts_ms=100.0, active_tracks=[track]))
    r2 = coordinator.process(_make_packet(frame, pts_ms=300.0), TrackingResult(camera_id="CAM-006", pts_ms=300.0, active_tracks=[track]))

    assert len(r1) == 1
    assert r1[0].status != "RECOGNIZED"
    assert r1[0].status == "LOW_CONFIDENCE"

    assert len(r2) == 1
    assert r2[0].status != "RECOGNIZED"
    assert r2[0].status == "LOW_CONFIDENCE"

    state = coordinator.get_track_state("CAM-006", 40)
    assert state is not None
    assert len(state.observations) == 2
    assert state.locked is False
    assert state.recognized_plate is None


# 5. 3 observations with agreement below 60%: NOT recognized
def test_5_three_observations_agreement_below_60_percent_not_recognized():
    frame = _make_frame()
    track = _make_track(track_id=50, pts_ms=100.0)

    # 3 distinct plates (33.3% agreement each, which is < 60%)
    cand1 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.25, is_valid_format=True, pts_ms=100.0)
    cand2 = PlateCandidate(raw_text="MH12CD5678", normalized_plate="MH12CD5678", confidence=0.25, is_valid_format=True, pts_ms=300.0)
    cand3 = PlateCandidate(raw_text="DL01EF9999", normalized_plate="DL01EF9999", confidence=0.25, is_valid_format=True, pts_ms=500.0)

    recognizer = MockPlateRecognizer(responses=[cand1, cand2, cand3])
    coordinator = ANPRCoordinator(
        recognizer=recognizer,
        config=ANPRConfig(min_attempt_spacing_ms=100.0),
    )

    r1 = coordinator.process(_make_packet(frame, pts_ms=100.0), TrackingResult(camera_id="CAM-006", pts_ms=100.0, active_tracks=[track]))
    r2 = coordinator.process(_make_packet(frame, pts_ms=300.0), TrackingResult(camera_id="CAM-006", pts_ms=300.0, active_tracks=[track]))
    r3 = coordinator.process(_make_packet(frame, pts_ms=500.0), TrackingResult(camera_id="CAM-006", pts_ms=500.0, active_tracks=[track]))

    assert r1[0].status != "RECOGNIZED"
    assert r2[0].status != "RECOGNIZED"
    assert r3[0].status != "RECOGNIZED"
    assert r3[0].status == "LOW_CONFIDENCE"

    state = coordinator.get_track_state("CAM-006", 50)
    assert state is not None
    assert len(state.observations) == 3
    assert state.locked is False
    assert state.recognized_plate is None


# 6. low-confidence random/noisy observations: do not form recognition
def test_6_low_confidence_random_noisy_observations_do_not_form_recognition():
    frame = _make_frame()
    track = _make_track(track_id=60, pts_ms=100.0)

    # Noise text and sub-0.20 valid text
    noisy_cands = [
        PlateCandidate(raw_text="SUZUKI", normalized_plate="", confidence=0.18, is_valid_format=False, pts_ms=100.0),
        PlateCandidate(raw_text="DIESEL", normalized_plate="", confidence=0.19, is_valid_format=False, pts_ms=300.0),
        PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.15, is_valid_format=True, pts_ms=500.0),
        PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.19, is_valid_format=True, pts_ms=700.0),
    ]

    recognizer = MockPlateRecognizer(responses=noisy_cands)
    coordinator = ANPRCoordinator(
        recognizer=recognizer,
        config=ANPRConfig(min_attempt_spacing_ms=100.0),
    )

    for i, pts in enumerate([100.0, 300.0, 500.0, 700.0]):
        res = coordinator.process(
            _make_packet(frame, pts_ms=pts),
            TrackingResult(camera_id="CAM-006", pts_ms=pts, active_tracks=[track]),
        )
        assert len(res) == 1
        assert res[0].status != "RECOGNIZED"

    state = coordinator.get_track_state("CAM-006", 60)
    assert state is not None
    # All were < 0.20 so none entered candidate buffer
    assert len(state.observations) == 0
    assert state.locked is False


# 7. existing high-confidence FAST_LOCK behavior remains unchanged
def test_7_existing_high_confidence_fast_lock_remains_unchanged():
    frame = _make_frame()
    track = _make_track(track_id=70, pts_ms=100.0)

    # Genuine high confidence >= 0.85
    cand = PlateCandidate(
        raw_text="GJ01AB1234",
        normalized_plate="GJ01AB1234",
        confidence=0.91,
        is_valid_format=True,
        pts_ms=100.0,
    )
    recognizer = MockPlateRecognizer(responses=[cand])
    coordinator = ANPRCoordinator(
        recognizer=recognizer,
        config=ANPRConfig(early_lock_confidence=0.85),
    )

    results = coordinator.process(
        _make_packet(frame, pts_ms=100.0),
        TrackingResult(camera_id="CAM-006", pts_ms=100.0, active_tracks=[track]),
    )

    assert len(results) == 1
    assert results[0].status == "RECOGNIZED"
    assert results[0].normalized_plate == "GJ01AB1234"

    state = coordinator.get_track_state("CAM-006", 70)
    assert state is not None
    assert state.locked is True
    assert state.lock_method == "FAST_LOCK"
    assert state.recognized_plate == "GJ01AB1234"


# 8. existing valid plates remain unchanged
def test_8_existing_valid_plates_remain_unchanged():
    plates_to_test = [
        "GJ01AB1234",
        "MH12DE5678",
        "DL08CAA1234",
        "22BH1234AA",
    ]

    for idx, plate_str in enumerate(plates_to_test):
        norm, is_valid = normalize_plate(plate_str)
        assert is_valid is True
        assert norm == plate_str

        # 3 agreeing observations at 0.22 form consensus
        obs = [
            TrackPlateObservation(
                pts_ms=100.0 * (i + 1),
                plate_bbox_crop=(0.0, 0.0, 50.0, 20.0),
                plate_bbox_frame=(0.0, 0.0, 50.0, 20.0),
                raw_text=plate_str,
                normalized_plate=norm,
                ocr_confidence=0.22,
                plate_detector_confidence=0.8,
                sharpness=30.0,
                is_valid_format=True,
                aspect_ratio=3.0,
            )
            for i in range(3)
        ]

        resolved, lock_method = resolve_temporal_consensus(
            obs,
            min_observations=3,
            agreement_ratio=0.60,
            fast_lock_confidence=0.85,
            min_candidate_confidence=0.20,
        )
        assert resolved == plate_str
        assert lock_method == "CONSENSUS_LOCK"


# 9. legitimate numeric 6 in non-state positions remains unchanged
def test_9_legitimate_numeric_6_in_non_state_positions_remains_unchanged():
    # '6' in RTO position: GJ06AB1234 (Vadodara)
    # '6' in number position: MH12AB6789
    # '6' in Bharat Series year/number: 26BH6789AA
    test_cases = [
        ("GJ06AB1234", "GJ06AB1234"),
        ("MH12AB6789", "MH12AB6789"),
        ("26BH6789AA", "26BH6789AA"),
    ]

    for raw, expected in test_cases:
        norm, is_valid = normalize_plate(raw)
        assert is_valid is True
        assert norm == expected
        assert norm[2] == expected[2]  # Non-state '6' remains numeric '6'


# 10. watchlist alert only occurs after genuine recognition/persistence
def test_10_watchlist_alert_only_occurs_after_genuine_recognition():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = "ACCEPTED"

    dispatcher = PersistenceDispatcher(
        worker=mock_worker,
        default_watchlist=["GJ05AB1234"],
    )

    frame = _make_frame()
    track = _make_track(track_id=100, pts_ms=100.0)

    # Observation 1 at 0.22 (LOW_CONFIDENCE, not yet consensus)
    cand1 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.22, is_valid_format=True, pts_ms=100.0)
    cand2 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.22, is_valid_format=True, pts_ms=300.0)
    cand3 = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.22, is_valid_format=True, pts_ms=500.0)

    recognizer = MockPlateRecognizer(responses=[cand1, cand2, cand3])
    coordinator = ANPRCoordinator(
        recognizer=recognizer,
        config=ANPRConfig(min_attempt_spacing_ms=100.0),
    )

    # Step 1: Observation 1
    r1 = coordinator.process(_make_packet(frame, pts_ms=100.0), TrackingResult(camera_id="CAM-006", pts_ms=100.0, active_tracks=[track]))
    assert r1[0].status == "LOW_CONFIDENCE"
    d1 = dispatcher.dispatch(anpr_results=r1, camera_code="CAM-006")
    assert d1.eligible == 0
    assert d1.skipped == 1
    assert mock_worker.enqueue.call_count == 0  # NO persistence, NO alert

    # Step 2: Observation 2
    r2 = coordinator.process(_make_packet(frame, pts_ms=300.0), TrackingResult(camera_id="CAM-006", pts_ms=300.0, active_tracks=[track]))
    assert r2[0].status == "LOW_CONFIDENCE"
    d2 = dispatcher.dispatch(anpr_results=r2, camera_code="CAM-006")
    assert d2.eligible == 0
    assert d2.skipped == 1
    assert mock_worker.enqueue.call_count == 0  # Still NO persistence, NO alert

    # Step 3: Observation 3 -> Reaches CONSENSUS_LOCK!
    r3 = coordinator.process(_make_packet(frame, pts_ms=500.0), TrackingResult(camera_id="CAM-006", pts_ms=500.0, active_tracks=[track]))
    assert r3[0].status == "RECOGNIZED"
    assert r3[0].normalized_plate == "GJ05AB1234"
    d3 = dispatcher.dispatch(anpr_results=r3, camera_code="CAM-006")
    assert d3.eligible == 1
    assert d3.enqueued == 1
    assert mock_worker.enqueue.call_count == 1  # Genuine persistence & watchlist alert triggered!
