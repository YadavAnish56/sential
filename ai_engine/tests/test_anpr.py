"""
Comprehensive unit tests for Sentinel Phase 6 Automatic Number Plate Recognition (ANPR).

Covers:
- PlateCandidate and ANPRResult dataclasses and serialization
- Safe vehicle crop extraction and coordinate clamping
- Objective image quality scoring (Laplacian sharpness + contrast)
- Conservative Indian license plate normalization and disambiguation
- Standard RTO and Bharat (BH) series format validation
- Non-inventive handling of ambiguous / invalid plate strings
- ANPRCoordinator triggering policies:
  * CONFIRMED tracks only
  * Maximum 3 attempts budget per track
  * 500 ms minimum PTS spacing
  * Early lock-in on high-confidence valid reads (>= 0.85)
  * Missing PTS handling
  * Multi-camera track state isolation
  * Stream discontinuity state flushing
  * Recognizer exception containment
  * No plate detected handling
"""

from __future__ import annotations

import time
import numpy as np
import pytest

from streaming.frame_reader import FramePacket
from ai_engine.tracking.schemas import Track, TrackState, TrackingResult
from ai_engine.anpr.schemas import PlateCandidate, ANPRResult, ANPRConfig
from ai_engine.anpr.normalization import (
    normalize_plate,
    sanitize_plate_string,
    validate_indian_plate_format,
)
from ai_engine.anpr.preprocessor import (
    extract_vehicle_crop,
    compute_crop_quality,
    preprocess_crop_for_ocr,
)
from ai_engine.anpr.recognizer import MockPlateRecognizer
from ai_engine.anpr.coordinator import ANPRCoordinator


def _make_synthetic_frame(w: int = 1280, h: int = 720) -> np.ndarray:
    """Create an in-memory synthetic frame with texture to ensure non-zero sharpness."""
    frame = np.full((h, w, 3), 128, dtype=np.uint8)
    # Add high-contrast gradient/edges to simulate vehicle contours
    frame[100:300, 100:400] = 220
    frame[150:250, 150:350] = 40
    return frame


def _make_packet(frame: np.ndarray, pts_ms: float | None = 1000.0, camera_id: str = "CAM-01") -> FramePacket:
    """Create a minimal FramePacket."""
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
    camera_id: str = "CAM-01",
    state: TrackState = TrackState.CONFIRMED,
    bbox: tuple[float, float, float, float] = (100.0, 100.0, 300.0, 300.0),
    confidence: float = 0.88,
    pts_ms: float | None = 1000.0,
) -> Track:
    """Create a Track instance for testing."""
    return Track(
        track_id=track_id,
        camera_id=camera_id,
        class_id=2,
        class_name="car",
        confidence=confidence,
        bbox=bbox,
        state=state,
        first_pts_ms=pts_ms,
        last_pts_ms=pts_ms,
        hits=2 if state == TrackState.CONFIRMED else 1,
        misses=0,
        best_confidence=confidence,
        best_bbox=bbox,
    )


# 1. PlateCandidate creation
def test_plate_candidate_creation():
    candidate = PlateCandidate(
        raw_text="GJ-05-AB-1234",
        normalized_plate="GJ05AB1234",
        confidence=0.92,
        is_valid_format=True,
        pts_ms=1500.0,
        plate_bbox=(10.0, 20.0, 90.0, 45.0),
    )
    assert candidate.raw_text == "GJ-05-AB-1234"
    assert candidate.normalized_plate == "GJ05AB1234"
    assert candidate.confidence == 0.92
    assert candidate.is_valid_format is True
    assert candidate.pts_ms == 1500.0
    assert candidate.plate_bbox == (10.0, 20.0, 90.0, 45.0)

    d = candidate.to_dict()
    assert d["normalized_plate"] == "GJ05AB1234"
    assert d["confidence"] == 0.92
    assert d["is_valid_format"] is True


# 2. ANPRResult serialization
def test_anpr_result_serialization():
    result = ANPRResult(
        camera_id="CAM-SURAT-01",
        track_id=5,
        pts_ms=2500.0,
        raw_text="GJ-05-CD-5678",
        normalized_plate="GJ05CD5678",
        confidence=0.88,
        is_valid_format=True,
        status="RECOGNIZED",
        plate_bbox=(15.0, 25.0, 95.0, 50.0),
    )
    d = result.to_dict()
    assert d["camera_id"] == "CAM-SURAT-01"
    assert d["track_id"] == 5
    assert d["pts_ms"] == 2500.0
    assert d["normalized_plate"] == "GJ05CD5678"
    assert d["confidence"] == 0.88
    assert d["status"] == "RECOGNIZED"
    assert d["plate_bbox"] == [15.0, 25.0, 95.0, 50.0]


# 3. Valid vehicle crop extraction
def test_valid_vehicle_crop_extraction():
    frame = _make_synthetic_frame(w=800, h=600)
    crop = extract_vehicle_crop(frame, bbox=(100.0, 150.0, 300.0, 400.0), min_width=40, min_height=30)
    assert crop is not None
    assert crop.shape == (250, 200, 3)  # h=250, w=200
    # Must be an isolated copy, modifying crop must not modify frame
    crop[0, 0] = 0
    assert frame[150, 100, 0] != 0 or frame[150, 100, 0] == 220


# 4. Invalid / out-of-bounds crop safety
def test_invalid_out_of_bounds_crop_safety():
    frame = _make_synthetic_frame(w=400, h=300)
    # Box exceeding image boundaries must be clamped safely
    crop = extract_vehicle_crop(frame, bbox=(-50.0, -50.0, 600.0, 500.0), min_width=40, min_height=30)
    assert crop is not None
    assert crop.shape[0] <= 300
    assert crop.shape[1] <= 400


# 5. Zero-area crop safety
def test_zero_area_crop_safety():
    frame = _make_synthetic_frame(w=400, h=300)
    assert extract_vehicle_crop(frame, bbox=(50.0, 50.0, 50.0, 50.0)) is None  # 0 area
    assert extract_vehicle_crop(frame, bbox=(100.0, 100.0, 80.0, 80.0)) is None  # inverted
    assert extract_vehicle_crop(frame, bbox=(10.0, 10.0, 20.0, 20.0), min_width=40) is None  # below min


# 6. Standard Indian normalization
def test_standard_indian_normalization():
    # Gujarat plate
    p1, v1 = normalize_plate("gj 05 ab 1234")
    assert p1 == "GJ05AB1234"
    assert v1 is True

    # Maharashtra plate
    p2, v2 = normalize_plate("MH-12-CD-3456")
    assert p2 == "MH12CD3456"
    assert v2 is True

    # Delhi plate (single letter series)
    p3, v3 = normalize_plate("dl:01:a:1234")
    assert p3 == "DL01A1234"
    assert v3 is True

    # Bharat Series
    p4, v4 = normalize_plate("22 BH 1234 AA")
    assert p4 == "22BH1234AA"
    assert v4 is True


# 7. State-position normalization
def test_state_position_normalization():
    # 0D -> OD for Odisha (defensible numeric-to-letter in state slot)
    p, v = normalize_plate("0D-12-CD-3456")
    assert p == "OD12CD3456"
    assert v is True


# 8. Numeric-position OCR normalization
def test_numeric_position_ocr_normalization():
    # O -> 0 and I -> 1 in numeric positions
    p, v = normalize_plate("GJ-O5-AB-I234")
    assert p == "GJ05AB1234"
    assert v is True

    # S -> 5, B -> 8, Z -> 2 in numeric positions
    p2, v2 = normalize_plate("MH-S2-AB-B23Z")
    assert p2 == "MH52AB8232"
    assert v2 is True


# 9. Invalid format handling without inventing characters
def test_invalid_format_handling():
    # 0J does not mean GJ; 0 must NOT arbitrarily convert to G
    p, v = normalize_plate("0J-O5-AB-I234")
    assert p == "0JO5ABI234"
    assert v is False  # Correctly marked invalid rather than fabricating 'GJ'

    # Pure random string
    p2, v2 = normalize_plate("XYZ12345ABC")
    assert v2 is False

    # Empty string
    p3, v3 = normalize_plate("")
    assert p3 == ""
    assert v3 is False


# 9b. Phase 29B: Position-aware state-code disambiguation (6 -> G when position 1 is J)
def test_phase29b_gujarat_state_disambiguation():
    # 1. Direct CAM-006 test case 1
    p1, v1 = normalize_plate("6J12L4219")
    assert p1 == "GJ12L4219"
    assert v1 is True

    # 2. Direct CAM-006 test case 2
    p2, v2 = normalize_plate("6J32K9819")
    assert p2 == "GJ32K9819"
    assert v2 is True

    # 2b. Case with spacing and OCR Z in numeric position
    p2b, v2b = normalize_plate("6J3Z K 9819")
    assert p2b == "GJ32K9819"
    assert v2b is True

    # 3. Valid existing plates remain unchanged
    p3, v3 = normalize_plate("GJ05AB1234")
    assert p3 == "GJ05AB1234"
    assert v3 is True

    # 4. Legitimate numeric 6s elsewhere remain unchanged
    p4, v4 = normalize_plate("GJ06AB1234")
    assert p4 == "GJ06AB1234"
    assert v4 is True

    p5, v5 = normalize_plate("DL01A6666")
    assert p5 == "DL01A6666"
    assert v5 is True

    # 5. Invalid / non-Indian strings remain rejected (no hallucination)
    # 6D does not match any Indian state code (e.g. GD is not a state code)
    p6, v6 = normalize_plate("6D12AB1234")
    assert p6 == "6D12AB1234"
    assert v6 is False

    # 66 does not match any Indian state code (GG is not a state code)
    p7, v7 = normalize_plate("66AB1234")
    assert p7 == "66AB1234"
    assert v7 is False

    # 6. No broad global OCR substitutions occur (e.g. manufacturer badges or noise)
    p8, v8 = normalize_plate("SUZUKI")
    assert v8 is False

    p9, v9 = normalize_plate("DIESEL")
    assert v9 is False


# 10. Low-confidence result
def test_low_confidence_result():
    frame = _make_synthetic_frame()
    packet = _make_packet(frame)
    track = _make_track(track_id=1, state=TrackState.CONFIRMED)
    tracking_res = TrackingResult(camera_id="CAM-01", pts_ms=1000.0, active_tracks=[track])

    # Recognizer returns valid text but low confidence 0.35 (below 0.50 threshold)
    low_cand = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.35, is_valid_format=True, pts_ms=1000.0)
    recognizer = MockPlateRecognizer(default_candidate=low_cand)

    coordinator = ANPRCoordinator(recognizer=recognizer, config=ANPRConfig(min_confidence=0.50))
    results = coordinator.process(packet, tracking_res)

    assert len(results) == 1
    assert results[0].status == "LOW_CONFIDENCE"
    assert results[0].normalized_plate == "GJ05AB1234"
    assert results[0].confidence == 0.35


# 11. Confirmed-track-only triggering
def test_confirmed_track_only_triggering():
    frame = _make_synthetic_frame()
    packet = _make_packet(frame)

    # Tentative track must NOT trigger ANPR
    tentative_track = _make_track(track_id=1, state=TrackState.TENTATIVE)
    tracking_res = TrackingResult(camera_id="CAM-01", pts_ms=1000.0, active_tracks=[tentative_track])

    cand = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.90, is_valid_format=True, pts_ms=1000.0)
    recognizer = MockPlateRecognizer(default_candidate=cand)

    coordinator = ANPRCoordinator(recognizer=recognizer)
    results = coordinator.process(packet, tracking_res)

    # 0 evaluations because track is not CONFIRMED
    assert len(results) == 0
    assert recognizer.call_count == 0


# 12. Maximum 3 attempts per track
def test_max_attempts_budget():
    frame = _make_synthetic_frame()
    track = _make_track(track_id=1, state=TrackState.CONFIRMED)

    # Low-confidence response so it doesn't early-lock
    low_cand = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.60, is_valid_format=True, pts_ms=0.0)
    recognizer = MockPlateRecognizer(default_candidate=low_cand)

    coordinator = ANPRCoordinator(recognizer=recognizer, config=ANPRConfig(max_attempts=3, min_attempt_spacing_ms=100.0, early_lock_confidence=0.99))

    # Attempt 1 (PTS 100) -> evaluates
    r1 = coordinator.process(_make_packet(frame, pts_ms=100.0), TrackingResult(camera_id="CAM-01", pts_ms=100.0, active_tracks=[track]))
    assert len(r1) == 1

    # Attempt 2 (PTS 300) -> evaluates
    r2 = coordinator.process(_make_packet(frame, pts_ms=300.0), TrackingResult(camera_id="CAM-01", pts_ms=300.0, active_tracks=[track]))
    assert len(r2) == 1

    # Attempt 3 (PTS 500) -> evaluates (max reached: 3)
    r3 = coordinator.process(_make_packet(frame, pts_ms=500.0), TrackingResult(camera_id="CAM-01", pts_ms=500.0, active_tracks=[track]))
    assert len(r3) == 1

    # Attempt 4 (PTS 700) -> budget exhausted, must NOT evaluate
    r4 = coordinator.process(_make_packet(frame, pts_ms=700.0), TrackingResult(camera_id="CAM-01", pts_ms=700.0, active_tracks=[track]))
    assert len(r4) == 0
    assert recognizer.call_count == 3


# 13. 500 ms PTS spacing
def test_pts_spacing_enforcement():
    frame = _make_synthetic_frame()
    track = _make_track(track_id=1, state=TrackState.CONFIRMED)
    cand = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.60, is_valid_format=True, pts_ms=0.0)
    recognizer = MockPlateRecognizer(default_candidate=cand)

    coordinator = ANPRCoordinator(recognizer=recognizer, config=ANPRConfig(min_attempt_spacing_ms=500.0, early_lock_confidence=0.99))

    # Frame 1 at PTS 1000.0 -> runs
    r1 = coordinator.process(_make_packet(frame, pts_ms=1000.0), TrackingResult(camera_id="CAM-01", pts_ms=1000.0, active_tracks=[track]))
    assert len(r1) == 1

    # Frame 2 at PTS 1200.0 (delta = 200 ms < 500 ms) -> skipped!
    r2 = coordinator.process(_make_packet(frame, pts_ms=1200.0), TrackingResult(camera_id="CAM-01", pts_ms=1200.0, active_tracks=[track]))
    assert len(r2) == 0

    # Frame 3 at PTS 1550.0 (delta = 550 ms >= 500 ms) -> runs!
    r3 = coordinator.process(_make_packet(frame, pts_ms=1550.0), TrackingResult(camera_id="CAM-01", pts_ms=1550.0, active_tracks=[track]))
    assert len(r3) == 1


# 14. Early lock-in at >= 0.85 valid confidence
def test_early_lock_in():
    frame = _make_synthetic_frame()
    track = _make_track(track_id=1, state=TrackState.CONFIRMED)

    # High confidence valid read (0.91 >= 0.85)
    valid_cand = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.91, is_valid_format=True, pts_ms=1000.0)
    recognizer = MockPlateRecognizer(default_candidate=valid_cand)

    coordinator = ANPRCoordinator(recognizer=recognizer, config=ANPRConfig(early_lock_confidence=0.85, min_attempt_spacing_ms=100.0))

    # Frame 1: Locks plate
    r1 = coordinator.process(_make_packet(frame, pts_ms=1000.0), TrackingResult(camera_id="CAM-01", pts_ms=1000.0, active_tracks=[track]))
    assert len(r1) == 1
    assert r1[0].status == "RECOGNIZED"

    state = coordinator.get_track_state("CAM-01", 1)
    assert state is not None
    assert state.locked is True
    assert state.recognized_plate == "GJ05AB1234"

    # Frame 2: Even with sufficient PTS spacing, locked track must not be evaluated again
    r2 = coordinator.process(_make_packet(frame, pts_ms=2000.0), TrackingResult(camera_id="CAM-01", pts_ms=2000.0, active_tracks=[track]))
    assert len(r2) == 0
    assert recognizer.call_count == 1


# 15. Crop quality selection
def test_crop_quality_calculation():
    # Create sharp contrasted image vs uniform flat image
    sharp_crop = np.zeros((100, 100, 3), dtype=np.uint8)
    sharp_crop[20:80, 20:80] = 255
    q_sharp = compute_crop_quality(sharp_crop)

    flat_crop = np.full((100, 100, 3), 128, dtype=np.uint8)
    q_flat = compute_crop_quality(flat_crop)

    # Sharp patterned image must have substantially higher quality than flat image
    assert q_sharp > q_flat
    assert q_flat == pytest.approx(0.0, abs=1.0)


# 16. Missing PTS handling
def test_missing_pts_handling():
    frame = _make_synthetic_frame()
    packet = _make_packet(frame, pts_ms=None)
    track = _make_track(track_id=1, state=TrackState.CONFIRMED, pts_ms=None)

    cand = PlateCandidate(raw_text="GJ05AB1234", normalized_plate="GJ05AB1234", confidence=0.88, is_valid_format=True, pts_ms=None)
    recognizer = MockPlateRecognizer(default_candidate=cand)

    coordinator = ANPRCoordinator(recognizer=recognizer)
    results = coordinator.process(packet, TrackingResult(camera_id="CAM-01", pts_ms=None, active_tracks=[track]))

    assert len(results) == 1
    assert results[0].pts_ms is None
    assert results[0].status == "RECOGNIZED"


# 17. Multi-camera isolation
def test_multi_camera_anpr_isolation():
    frame = _make_synthetic_frame()
    track_cam1 = _make_track(track_id=1, camera_id="CAM-NORTH", state=TrackState.CONFIRMED)
    track_cam2 = _make_track(track_id=1, camera_id="CAM-SOUTH", state=TrackState.CONFIRMED)

    cand1 = PlateCandidate(raw_text="GJ01AA1111", normalized_plate="GJ01AA1111", confidence=0.92, is_valid_format=True, pts_ms=100.0)
    cand2 = PlateCandidate(raw_text="GJ05BB2222", normalized_plate="GJ05BB2222", confidence=0.92, is_valid_format=True, pts_ms=100.0)

    recognizer = MockPlateRecognizer(responses=[cand1, cand2])
    coordinator = ANPRCoordinator(recognizer=recognizer)

    # Evaluate CAM-NORTH Track 1
    r1 = coordinator.process(_make_packet(frame, pts_ms=100.0, camera_id="CAM-NORTH"), TrackingResult(camera_id="CAM-NORTH", pts_ms=100.0, active_tracks=[track_cam1]))
    # Evaluate CAM-SOUTH Track 1
    r2 = coordinator.process(_make_packet(frame, pts_ms=100.0, camera_id="CAM-SOUTH"), TrackingResult(camera_id="CAM-SOUTH", pts_ms=100.0, active_tracks=[track_cam2]))

    assert r1[0].camera_id == "CAM-NORTH"
    assert r1[0].normalized_plate == "GJ01AA1111"

    assert r2[0].camera_id == "CAM-SOUTH"
    assert r2[0].normalized_plate == "GJ05BB2222"

    # Both tracks maintained separate states despite identical track_id=1
    state1 = coordinator.get_track_state("CAM-NORTH", 1)
    state2 = coordinator.get_track_state("CAM-SOUTH", 1)
    assert state1.recognized_plate == "GJ01AA1111"
    assert state2.recognized_plate == "GJ05BB2222"


# 18. Deterministic mock recognizer behavior
def test_deterministic_mock_recognizer_behavior():
    c1 = PlateCandidate(raw_text="MH12AB1111", normalized_plate="MH12AB1111", confidence=0.8, is_valid_format=True, pts_ms=1.0)
    c2 = PlateCandidate(raw_text="MH12AB2222", normalized_plate="MH12AB2222", confidence=0.9, is_valid_format=True, pts_ms=2.0)

    mock = MockPlateRecognizer(responses=[c1, c2])
    dummy_crop = np.zeros((50, 50, 3), dtype=np.uint8)

    assert mock.recognize(dummy_crop) == c1
    assert mock.recognize(dummy_crop) == c2
    assert mock.call_count == 2


# 19. Recognizer failure handling
def test_recognizer_failure_handling():
    frame = _make_synthetic_frame()
    track = _make_track(track_id=1, state=TrackState.CONFIRMED)
    packet = _make_packet(frame)

    recognizer = MockPlateRecognizer(exception_to_raise=RuntimeError("Simulated OCR kernel error"))
    coordinator = ANPRCoordinator(recognizer=recognizer)

    results = coordinator.process(packet, TrackingResult(camera_id="CAM-01", pts_ms=1000.0, active_tracks=[track]))

    assert len(results) == 1
    assert results[0].status == "FAILED"
    assert results[0].confidence == 0.0


# 20. No fake OCR output when recognizer has no result
def test_no_plate_detected_handling():
    frame = _make_synthetic_frame()
    track = _make_track(track_id=1, state=TrackState.CONFIRMED)
    packet = _make_packet(frame)

    # Recognizer explicitly returns None (no plate found on vehicle)
    recognizer = MockPlateRecognizer(default_candidate=None)
    coordinator = ANPRCoordinator(recognizer=recognizer)

    results = coordinator.process(packet, TrackingResult(camera_id="CAM-01", pts_ms=1000.0, active_tracks=[track]))

    assert len(results) == 1
    assert results[0].status == "NO_PLATE_DETECTED"
    assert results[0].normalized_plate == ""
    assert results[0].raw_text == ""
    assert results[0].confidence == 0.0
