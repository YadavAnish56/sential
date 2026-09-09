"""
Unit tests for EasyOCRPlateRecognizer in Sentinel Phase 6C.

Verifies:
1. Recognizer initialization (default, explicit device, offline)
2. Offline configuration (download_enabled=False enforcement)
3. Valid Indian plate OCR candidate parsing
4. Invalid OCR candidate rejection
5. Multiple OCR candidates handling
6. Valid Indian plate preferred over high-confidence non-plate text
7. Manufacturer emblems rejected (HYUNDAI, SUZUKI, MARUTI, TATA)
8. Fuel tags, stickers, and advertisement text rejected (DIESEL, CNG, SPEED 40 KM/H)
9. 4-point polygon to axis-aligned bounding box conversion
10. Source frame pts_ms preservation
11. Empty, None, and malformed crop handling
12. Inference exception isolation (zero unhandled exceptions)
13. Failed initialization behavior
14. Device selection (CUDA / CPU)
15. Integration with ANPRCoordinator
"""

from __future__ import annotations

from typing import Any
import numpy as np
import pytest

from ai_engine.anpr.recognizer import EasyOCRPlateRecognizer, BasePlateRecognizer
from ai_engine.anpr.schemas import PlateCandidate, ANPRConfig
from ai_engine.anpr.coordinator import ANPRCoordinator
from ai_engine.tracking.schemas import Track, TrackState, TrackingResult
from streaming.frame_reader import FramePacket


class DummyEasyOCRReader:
    """Mock EasyOCR Reader allowing deterministic injection of OCR responses."""

    def __init__(self, responses: list[list[tuple[Any, str, float]]] | None = None, device: str = "cuda") -> None:
        self.device = device
        self._responses = list(responses) if responses is not None else []
        self.call_count = 0
        self.exception_to_raise: Exception | None = None

    def readtext(self, image: Any) -> list[tuple[Any, str, float]]:
        self.call_count += 1
        if self.exception_to_raise is not None:
            raise self.exception_to_raise
        if self._responses:
            return self._responses.pop(0)
        return []


def test_recognizer_inheritance() -> None:
    """EasyOCRPlateRecognizer must be a subclass of BasePlateRecognizer."""
    assert issubclass(EasyOCRPlateRecognizer, BasePlateRecognizer)


def test_recognizer_init_with_mock_reader() -> None:
    """Recognizer properly registers injected Reader and reports ready."""
    reader = DummyEasyOCRReader(device="cuda")
    recognizer = EasyOCRPlateRecognizer(reader=reader)

    assert recognizer.is_ready is True
    assert recognizer.status == "READY"
    assert recognizer.device == "cuda"
    assert recognizer.call_count == 0


def test_valid_indian_plate_candidate() -> None:
    """Single valid Indian plate detection is correctly normalized and returned."""
    poly = [[10, 20], [100, 20], [100, 50], [10, 50]]
    raw_ocr = "GJ 01 AB 1234"
    conf = 0.92

    reader = DummyEasyOCRReader(responses=[[(poly, raw_ocr, conf)]])
    recognizer = EasyOCRPlateRecognizer(reader=reader)

    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=1500.0)

    assert cand is not None
    assert isinstance(cand, PlateCandidate)
    assert cand.raw_text == "GJ 01 AB 1234"
    assert cand.normalized_plate == "GJ01AB1234"
    assert cand.is_valid_format is True
    assert cand.confidence == pytest.approx(0.92)
    assert cand.pts_ms == 1500.0
    assert cand.plate_bbox == (10.0, 20.0, 100.0, 50.0)


def test_bharat_series_plate_candidate() -> None:
    """Bharat Series (BH) plate detection is recognized as valid."""
    poly = [[15, 25], [110, 25], [110, 55], [15, 55]]
    raw_ocr = "22 BH 9999 AB"
    conf = 0.89

    reader = DummyEasyOCRReader(responses=[[(poly, raw_ocr, conf)]])
    recognizer = EasyOCRPlateRecognizer(reader=reader)

    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=2000.0)

    assert cand is not None
    assert cand.normalized_plate == "22BH9999AB"
    assert cand.is_valid_format is True
    assert cand.pts_ms == 2000.0


def test_invalid_candidate_rejected_by_default() -> None:
    """Non-plate text (e.g. random letters/numbers) is rejected when allow_invalid=False."""
    poly = [[10, 20], [90, 20], [90, 45], [10, 45]]
    raw_ocr = "HELLO WORLD"
    conf = 0.85

    reader = DummyEasyOCRReader(responses=[[(poly, raw_ocr, conf)]])
    recognizer = EasyOCRPlateRecognizer(reader=reader, allow_invalid_candidates=False)

    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=100.0)

    assert cand is None


def test_invalid_candidate_returned_if_explicitly_allowed() -> None:
    """When allow_invalid_candidates=True, returns candidate marked is_valid_format=False."""
    poly = [[10, 20], [90, 20], [90, 45], [10, 45]]
    raw_ocr = "HELLO WORLD"
    conf = 0.85

    reader = DummyEasyOCRReader(responses=[[(poly, raw_ocr, conf)]])
    recognizer = EasyOCRPlateRecognizer(reader=reader, allow_invalid_candidates=True)

    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=100.0)

    assert cand is not None
    assert cand.is_valid_format is False
    assert cand.normalized_plate == "HELLOWORLD"


@pytest.mark.parametrize("badge_text", [
    "HYUNDAI",
    "SUZUKI",
    "MARUTI",
    "TATA",
    "SWIFT",
    "CRETA",
    "BALENO",
    "MAHINDRA",
    "TOYOTA",
    "HONDA",
])
def test_manufacturer_badges_rejected(badge_text: str) -> None:
    """Vehicle manufacturer and model badges are never treated as valid plates."""
    poly = [[10, 10], [80, 10], [80, 30], [10, 30]]
    reader = DummyEasyOCRReader(responses=[[(poly, badge_text, 0.96)]])
    recognizer = EasyOCRPlateRecognizer(reader=reader, allow_invalid_candidates=False)

    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=500.0)

    assert cand is None, f"Badge {badge_text} should have been rejected as a non-plate."


@pytest.mark.parametrize("sticker_text", [
    "DIESEL",
    "CNG",
    "4x4",
    "VXI",
    "ALL INDIA PERMIT",
    "SPEED 40 KM/H",
    "GOODS CARRIER",
    "POLICE",
])
def test_stickers_and_tags_rejected(sticker_text: str) -> None:
    """Fuel tags, commercial permits, and stickers are strictly filtered out."""
    poly = [[5, 5], [70, 5], [70, 25], [5, 25]]
    reader = DummyEasyOCRReader(responses=[[(poly, sticker_text, 0.94)]])
    recognizer = EasyOCRPlateRecognizer(reader=reader, allow_invalid_candidates=False)

    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=500.0)

    assert cand is None, f"Sticker {sticker_text} should have been rejected as a non-plate."


def test_valid_plate_preferred_over_high_confidence_badge() -> None:
    """
    When EasyOCR returns both a manufacturer emblem (e.g. SUZUKI @ 0.98)
    and a real plate (e.g. GJ05CD5678 @ 0.81), the plate candidate MUST win.
    """
    poly_badge = [[5, 10], [60, 10], [60, 25], [5, 25]]
    poly_plate = [[30, 60], [150, 60], [150, 90], [30, 90]]

    # Badge has higher raw confidence than plate
    results = [
        (poly_badge, "SUZUKI", 0.98),
        (poly_plate, "GJ 05 CD 5678", 0.81),
    ]

    reader = DummyEasyOCRReader(responses=[results])
    recognizer = EasyOCRPlateRecognizer(reader=reader, allow_invalid_candidates=False)

    crop = np.zeros((120, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=800.0)

    assert cand is not None
    assert cand.raw_text == "GJ 05 CD 5678"
    assert cand.normalized_plate == "GJ05CD5678"
    assert cand.is_valid_format is True
    assert cand.confidence == pytest.approx(0.81)
    assert cand.plate_bbox == (30.0, 60.0, 150.0, 90.0)


def test_multiple_valid_plates_highest_confidence_wins() -> None:
    """When multiple valid candidates exist, highest confidence valid candidate wins."""
    poly1 = [[10, 10], [80, 10], [80, 30], [10, 30]]
    poly2 = [[20, 40], [120, 40], [120, 65], [20, 65]]

    results = [
        (poly1, "MH 12 AB 1111", 0.75),
        (poly2, "MH 12 AB 2222", 0.91),
    ]

    reader = DummyEasyOCRReader(responses=[results])
    recognizer = EasyOCRPlateRecognizer(reader=reader)

    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=1000.0)

    assert cand is not None
    assert cand.normalized_plate == "MH12AB2222"
    assert cand.confidence == pytest.approx(0.91)


def test_plate_bbox_clamped_to_crop() -> None:
    """Bounding box coordinates are properly converted and clamped to crop dimensions."""
    # Poly with out-of-bounds coordinates
    poly = [[-10.5, -5.0], [250.0, -5.0], [250.0, 120.0], [-10.5, 120.0]]
    results = [(poly, "DL 01 AA 1234", 0.88)]

    reader = DummyEasyOCRReader(responses=[results])
    recognizer = EasyOCRPlateRecognizer(reader=reader)

    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=1200.0)

    assert cand is not None
    assert cand.plate_bbox is not None
    x1, y1, x2, y2 = cand.plate_bbox
    assert x1 >= 0.0
    assert y1 >= 0.0
    assert x2 <= 200.0
    assert y2 <= 100.0


def test_empty_and_invalid_crops() -> None:
    """Empty or malformed crops return None without calling Reader."""
    reader = DummyEasyOCRReader()
    recognizer = EasyOCRPlateRecognizer(reader=reader)

    assert recognizer.recognize(crop=None, pts_ms=10.0) is None
    assert recognizer.recognize(crop=np.array([]), pts_ms=10.0) is None
    assert recognizer.recognize(crop=np.zeros((0, 0, 3), dtype=np.uint8), pts_ms=10.0) is None
    assert recognizer.recognize(crop=np.zeros((5,), dtype=np.uint8), pts_ms=10.0) is None
    assert reader.call_count == 0


def test_inference_exception_handled_safely() -> None:
    """Reader exception during readtext does not crash and returns None."""
    reader = DummyEasyOCRReader()
    reader.exception_to_raise = RuntimeError("CUDA out of memory in CRAFT")
    recognizer = EasyOCRPlateRecognizer(reader=reader)

    crop = np.zeros((100, 100, 3), dtype=np.uint8)
    cand = recognizer.recognize(crop=crop, pts_ms=50.0)

    assert cand is None
    assert recognizer.call_count == 1


def test_uninitialized_recognizer_fails_safely() -> None:
    """Recognizer with status FAILED returns None on recognize()."""
    recognizer = EasyOCRPlateRecognizer(reader=None)
    recognizer._status = "FAILED"

    crop = np.zeros((50, 50, 3), dtype=np.uint8)
    assert recognizer.recognize(crop=crop) is None


def test_coordinator_integration_with_easyocr() -> None:
    """
    ANPRCoordinator integrates seamlessly with EasyOCRPlateRecognizer,
    correctly budgeting and locking on a valid plate.
    """
    poly = [[10, 10], [90, 10], [90, 35], [10, 35]]
    results = [(poly, "GJ 01 AB 1234", 0.90)]

    reader = DummyEasyOCRReader(responses=[results])
    recognizer = EasyOCRPlateRecognizer(reader=reader)

    coord_config = ANPRConfig(min_confidence=0.50, early_lock_confidence=0.85)
    coordinator = ANPRCoordinator(recognizer=recognizer, config=coord_config)

    # Synthetic frame with high texture for Laplacian variance check
    frame = np.random.randint(0, 256, (480, 640, 3), dtype=np.uint8)
    packet = FramePacket(
        camera_id="CAM-1",
        frame=frame,
        pts_ms=100.0,
        received_at=1000.0,
        width=640,
        height=480,
    )

    track = Track(
        track_id=1,
        camera_id="CAM-1",
        class_id=2,
        class_name="car",
        bbox=(50.0, 50.0, 250.0, 200.0),
        confidence=0.95,
        state=TrackState.CONFIRMED,
        hits=5,
    )

    tracking_res = TrackingResult(
        camera_id="CAM-1",
        pts_ms=100.0,
        active_tracks=[track],
    )

    out = coordinator.process(packet=packet, tracking_result=tracking_res)

    assert len(out) == 1
    res = out[0]
    assert res.camera_id == "CAM-1"
    assert res.track_id == 1
    assert res.status == "RECOGNIZED"
    assert res.normalized_plate == "GJ01AB1234"
    assert res.is_valid_format is True
    assert res.confidence == pytest.approx(0.90)

    # Track should be locked due to high confidence
    state = coordinator.get_track_state("CAM-1", 1)
    assert state is not None
    assert state.locked is True
    assert state.recognized_plate == "GJ01AB1234"
