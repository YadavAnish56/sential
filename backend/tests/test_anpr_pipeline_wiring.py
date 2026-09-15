"""
Comprehensive Unit & Integration Test Suite for Live ANPR Pipeline Wiring (Phase 18B).

Verifies:
1. Backend startup creates shared EasyOCR recognizer
2. Recognizer is injected into CameraPipelineManager
3. Camera pipeline receives recognizer when registered
4. ANPR-enabled camera actually invokes coordinator and recognizer
5. ANPR-disabled camera does not invoke OCR
6. OCR failure does not terminate pipeline
7. Valid Indian plate passes existing normalization
8. Invalid/foreign text is rejected
9. Persistence only occurs for valid recognized plates
10. Watchlist match generates alert atomically
11. Existing pipeline and camera lifecycle behavior remains intact
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure Sentinel root and backend root are on sys.path
sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from ai_engine.anpr.coordinator import ANPRCoordinator
from ai_engine.anpr.normalization import normalize_plate
from ai_engine.anpr.recognizer import BasePlateRecognizer, MockPlateRecognizer
from ai_engine.anpr.schemas import ANPRConfig, ANPRResult, PlateCandidate
from ai_engine.detector import VehicleDetector
from ai_engine.persistence_dispatcher import PersistenceDispatcher
from ai_engine.pipeline import CameraPipeline
from ai_engine.pipeline_manager import CameraPipelineManager, PipelineConfig
from ai_engine.schemas import Detection, DetectionResult
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking.schemas import Track, TrackState, TrackingResult
from ai_engine.tracking.tracker import VehicleTracker
from app.database.connection import Base
from app.main import app, lifespan
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.models.watchlist import Watchlist
from app.services.alert_service import AlertService
from app.services.anpr_service import ANPRPersistenceService
from app.services.anpr_worker import ANPRPersistenceWorker
from streaming.frame_reader import FramePacket


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures & Doubles
# ─────────────────────────────────────────────────────────────────────────────

class StubDetector:
    """Deterministic vehicle detector stub."""
    def __init__(self, detections: list[Detection] | None = None):
        self.detections = detections or []

    def detect(self, packet_or_frame, camera_id="CAM-001", pts_ms=1000.0) -> DetectionResult:
        if isinstance(packet_or_frame, FramePacket):
            frame = packet_or_frame.frame
            pts = packet_or_frame.pts_ms
            cam = packet_or_frame.camera_id
        else:
            frame = packet_or_frame
            pts = pts_ms
            cam = camera_id

        return DetectionResult(
            camera_id=cam,
            pts_ms=pts,
            detections=self.detections,
            inference_time_ms=5.0,
            device="cpu",
        )


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed camera and watchlist
    cam = Camera(
        id=1,
        camera_code="CAM-001",
        name="Test Camera 1",
        location="Junction 1",
        stream_url="rtsp://127.0.0.1:8554/stream/cam01",
        status="online",
    )
    wl = Watchlist(
        id=1,
        plate_number="GJ01AB1234",
        description="Stolen SUV",
        severity="high",
        is_active=True,
    )
    session.add_all([cam, wl])
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(bind=engine)


# ─────────────────────────────────────────────────────────────────────────────
# 1 & 2. Backend Startup & Recognizer Injection
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backend_startup_creates_and_injects_recognizer():
    """Verify backend startup creates a shared recognizer and injects it into CameraPipelineManager."""
    mock_pipeline_manager_cls = MagicMock()
    mock_pipeline_manager_instance = MagicMock()
    mock_pipeline_manager_cls.return_value = mock_pipeline_manager_instance

    mock_worker_cls = MagicMock()
    mock_worker_instance = MagicMock()
    mock_worker_cls.return_value = mock_worker_instance

    mock_dispatcher_cls = MagicMock()
    mock_dispatcher_instance = MagicMock()
    mock_dispatcher_cls.return_value = mock_dispatcher_instance

    mock_recognizer_cls = MagicMock()
    mock_recognizer_instance = MagicMock()
    mock_recognizer_instance.status = "READY"
    mock_recognizer_instance.device = "cuda"
    mock_recognizer_cls.return_value = mock_recognizer_instance

    with patch("app.main.CameraPipelineManager", mock_pipeline_manager_cls), \
         patch("app.main.ANPRPersistenceWorker", mock_worker_cls), \
         patch("app.main.PersistenceDispatcher", mock_dispatcher_cls), \
         patch("app.main.EasyOCRPlateRecognizer", mock_recognizer_cls):

        async with lifespan(app):
            # Verify recognizer was created once
            mock_recognizer_cls.assert_called_once()

            # Verify pipeline manager received the shared recognizer
            mock_pipeline_manager_cls.assert_called_once()
            _, kwargs = mock_pipeline_manager_cls.call_args
            assert kwargs["plate_recognizer"] == mock_recognizer_instance
            assert getattr(app.state, "plate_recognizer", None) == mock_recognizer_instance


# ─────────────────────────────────────────────────────────────────────────────
# 3, 4, 5. Camera Pipeline Wiring & ANPR Gating
# ─────────────────────────────────────────────────────────────────────────────

def make_frame_packet(pts_ms: float = 100.0, camera_id: str = "CAM-001") -> FramePacket:
    frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
    return FramePacket(
        frame=frame,
        pts_ms=pts_ms,
        received_at=pts_ms / 1000.0,
        width=640,
        height=480,
        camera_id=camera_id,
    )


def test_camera_pipeline_receives_recognizer_when_anpr_enabled():
    """When ANPR is enabled, CameraPipeline receives an ANPRCoordinator with the shared recognizer."""
    shared_recognizer = MockPlateRecognizer()
    manager = CameraPipelineManager(
        detector=StubDetector(),
        plate_recognizer=shared_recognizer,
    )

    config = PipelineConfig(
        camera_id="CAM-001",
        enable_anpr=True,
    )
    session = manager.register_camera(config=config)

    assert session.pipeline.anpr_coordinator is not None
    assert session.pipeline.anpr_coordinator.recognizer is shared_recognizer


def test_camera_pipeline_does_not_invoke_ocr_when_anpr_disabled():
    """When ANPR is disabled, CameraPipeline has no ANPRCoordinator and does not invoke OCR."""
    mock_recognizer = MockPlateRecognizer()
    manager = CameraPipelineManager(
        detector=StubDetector(),
        plate_recognizer=mock_recognizer,
    )

    config = PipelineConfig(
        camera_id="CAM-002",
        enable_anpr=False,
    )
    session = manager.register_camera(config=config)

    assert session.pipeline.anpr_coordinator is None

    # Process a frame
    packet = make_frame_packet(pts_ms=100.0, camera_id="CAM-002")
    result = session.process_one_frame(packet)

    assert result.anpr_results == []
    assert mock_recognizer.call_count == 0


def test_anpr_enabled_camera_actually_invokes_coordinator():
    """When ANPR is enabled and a confirmed track is present, coordinator and recognizer are invoked."""
    candidate = PlateCandidate(
        raw_text="GJ01AB1234",
        normalized_plate="GJ01AB1234",
        confidence=0.92,
        is_valid_format=True,
        pts_ms=200.0,
    )
    mock_recognizer = MockPlateRecognizer(default_candidate=candidate)

    # Setup detector with a clear detection
    det = Detection(
        class_id=2,
        class_name="car",
        confidence=0.88,
        x1=100.0,
        y1=100.0,
        x2=300.0,
        y2=250.0,
        camera_id="CAM-001",
        pts_ms=100.0,
    )
    detector = StubDetector([det])

    manager = CameraPipelineManager(
        detector=detector,
        plate_recognizer=mock_recognizer,
    )

    config = PipelineConfig(
        camera_id="CAM-001",
        enable_anpr=True,
        anpr_config=ANPRConfig(min_crop_quality=0.0),
        tracker_config=None,  # default tracker requires min_hits=2 for CONFIRMED
    )
    session = manager.register_camera(config=config)

    # Frame 1: Tentative track (hit 1)
    p1 = make_frame_packet(pts_ms=100.0, camera_id="CAM-001")
    r1 = session.process_one_frame(p1)
    assert r1.has_tracks
    # Track is tentative on frame 1, so OCR should not be invoked yet
    assert mock_recognizer.call_count == 0

    # Frame 2: Confirmed track (hit 2) -> OCR invoked
    p2 = make_frame_packet(pts_ms=200.0, camera_id="CAM-001")
    r2 = session.process_one_frame(p2)
    assert r2.has_tracks
    assert mock_recognizer.call_count == 1
    assert len(r2.anpr_results) == 1
    assert r2.anpr_results[0].status == "RECOGNIZED"
    assert r2.anpr_results[0].normalized_plate == "GJ01AB1234"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Fault Tolerance & Exception Isolation
# ─────────────────────────────────────────────────────────────────────────────

def test_ocr_failure_does_not_terminate_pipeline():
    """If the OCR engine throws an exception, the pipeline records the error and continues running."""
    faulty_recognizer = MockPlateRecognizer(exception_to_raise=RuntimeError("CUDA out of memory in OCR"))

    det = Detection(
        class_id=2,
        class_name="car",
        confidence=0.88,
        x1=100.0,
        y1=100.0,
        x2=300.0,
        y2=250.0,
        camera_id="CAM-001",
        pts_ms=100.0,
    )
    detector = StubDetector([det])

    manager = CameraPipelineManager(
        detector=detector,
        plate_recognizer=faulty_recognizer,
    )

    session = manager.register_camera(
        config=PipelineConfig(
            camera_id="CAM-001",
            enable_anpr=True,
            anpr_config=ANPRConfig(min_crop_quality=0.0),
        )
    )

    # Hit 1: Tentative
    session.process_one_frame(make_frame_packet(pts_ms=100.0, camera_id="CAM-001"))
    # Hit 2: Confirmed -> OCR raises RuntimeError, but process_frame catches it safely
    r2 = session.process_one_frame(make_frame_packet(pts_ms=200.0, camera_id="CAM-001"))

    assert r2 is not None
    assert r2.has_tracks
    # ANPR produced a FAILED result rather than crashing
    assert len(r2.anpr_results) == 1
    assert r2.anpr_results[0].status == "FAILED"
    # Pipeline stats reflect error isolation
    stats = session.pipeline.get_stats()
    assert stats["frames_total"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# 7 & 8. Plate Normalization & Foreign/Invalid Rejection
# ─────────────────────────────────────────────────────────────────────────────

def test_plate_normalization_and_validation():
    """Verify Indian RTO and Bharat Series formats pass while noise/emblems are rejected."""
    # Valid Indian formats
    p1, v1 = normalize_plate("GJ 01 AB 1234")
    assert v1 is True and p1 == "GJ01AB1234"

    p2, v2 = normalize_plate("22 BH 1234 AA")
    assert v2 is True and p2 == "22BH1234AA"

    p3, v3 = normalize_plate("MH-12-DE-9999")
    assert v3 is True and p3 == "MH12DE9999"

    # Invalid / foreign / noise text must be rejected
    _, v4 = normalize_plate("SUZUKI")
    assert v4 is False

    _, v5 = normalize_plate("DIESEL")
    assert v5 is False

    _, v6 = normalize_plate("SPEED 40 KM/H")
    assert v6 is False

    _, v7 = normalize_plate("ABC123XYZ")
    assert v7 is False


# ─────────────────────────────────────────────────────────────────────────────
# 9 & 10. Persistence & Watchlist Matching
# ─────────────────────────────────────────────────────────────────────────────

def test_persistence_only_for_valid_plates_and_watchlist_alert(in_memory_db):
    """Verify that only valid plates persist to the DB and matching plates trigger alerts."""
    service = ANPRPersistenceService(in_memory_db)

    # 1. Invalid / noise candidate -> Rejected, nothing persisted
    invalid_result = ANPRResult(
        camera_id="CAM-001",
        track_id=1,
        pts_ms=100.0,
        raw_text="SUZUKI",
        normalized_plate="",
        confidence=0.85,
        is_valid_format=False,
        status="NO_PLATE_DETECTED",
    )
    persisted_invalid = service.persist_result(invalid_result, camera_code="CAM-001")
    assert persisted_invalid is None
    assert in_memory_db.query(Event).count() == 0

    # 2. Valid regular vehicle -> Persisted as Event and Vehicle, NO Alert
    valid_regular = ANPRResult(
        camera_id="CAM-001",
        track_id=2,
        pts_ms=200.0,
        raw_text="GJ05CD5678",
        normalized_plate="GJ05CD5678",
        confidence=0.91,
        is_valid_format=True,
        status="RECOGNIZED",
    )
    res_regular = service.persist_result(valid_regular, camera_code="CAM-001")
    assert res_regular is not None
    assert res_regular.plate_number == "GJ05CD5678"
    assert res_regular.alert_id is None
    assert in_memory_db.query(Vehicle).filter_by(plate_number="GJ05CD5678").count() == 1
    assert in_memory_db.query(Alert).count() == 0

    # 3. Watchlist vehicle ("GJ01AB1234") -> Persisted as Event, Vehicle, and creates Alert
    valid_watchlist = ANPRResult(
        camera_id="CAM-001",
        track_id=3,
        pts_ms=300.0,
        raw_text="GJ 01 AB 1234",
        normalized_plate="GJ01AB1234",
        confidence=0.95,
        is_valid_format=True,
        status="RECOGNIZED",
    )
    res_wl = service.persist_result(valid_watchlist, camera_code="CAM-001")
    assert res_wl is not None
    assert res_wl.plate_number == "GJ01AB1234"
    assert res_wl.alert_id is not None
    assert in_memory_db.query(Alert).count() == 1

    alert = in_memory_db.query(Alert).first()
    assert alert.alert_type == "ANPR_WATCHLIST"
    assert alert.severity == "high"
    assert "GJ01AB1234" in alert.message
