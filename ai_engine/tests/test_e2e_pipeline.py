"""
Controlled End-to-End Pipeline Validation for Sentinel (Phase 6D.4).

Proves the complete single-camera processing-to-database path:
    Synthetic FramePacket
        ↓
    CameraPipeline (StreamProcessor → VehicleTracker → ANPRCoordinator)
        ↓
    ANPRResult
        ↓
    ANPRPersistenceService
        ↓
    SQLite test database
        ↓
    Vehicle + Event

All models use deterministic in-memory test doubles at the boundary (no RTSP,
no live footage, no EasyOCR weight downloads, no network, no FastAPI server).
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Ensure project root and backend root are on sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
for path_dir in [str(ROOT), str(BACKEND)]:
    if path_dir not in sys.path:
        sys.path.insert(0, path_dir)

from streaming.frame_reader import FramePacket
from ai_engine.schemas import Detection, DetectionResult
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import VehicleTracker, TrackerConfig
from ai_engine.anpr import ANPRCoordinator, ANPRConfig
from ai_engine.anpr.schemas import PlateCandidate, ANPRResult
from ai_engine.anpr.recognizer import MockPlateRecognizer
from ai_engine.pipeline import CameraPipeline, PipelineResult

from app.database.connection import Base
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.services.anpr_service import ANPRPersistenceService


# ─────────────────────────────────────────────────────────────────────────────
# Test Doubles & Factories
# ─────────────────────────────────────────────────────────────────────────────

class StubDetector:
    """
    Deterministic in-memory test double for VehicleDetector.
    Produces pre-configured vehicle detections without external weights.
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

        # Retag detections with current frame metadata
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
            inference_time_ms=2.5,
            device="cpu",
            model_name="stub-detector",
            is_discontinuity=packet.is_discontinuity,
        )


def make_synthetic_packet(
    camera_id: str = "CAM-01",
    pts_ms: float = 1000.0,
    width: int = 1280,
    height: int = 720,
    is_discontinuity: bool = False,
    seed: int = 42,
) -> FramePacket:
    """Generate a high-contrast synthetic FramePacket with deterministic pixel pattern."""
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


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    """In-memory SQLite database session fixture with foreign key constraints."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def test_camera(db_session):
    """Seed test camera CAM-01 in the database."""
    camera = Camera(
        camera_code="CAM-01",
        name="Main Entrance Gate",
        location="North Gate",
        status="active",
    )
    db_session.add(camera)
    db_session.commit()
    db_session.refresh(camera)
    return camera


@pytest.fixture
def e2e_pipeline_factory():
    """
    Factory creating a fully wired CameraPipeline using real components:
    StreamProcessor + VehicleTracker + ANPRCoordinator + CameraPipeline.
    """
    def _create(
        camera_id: str = "CAM-01",
        detections: list[Detection] | None = None,
        candidate: PlateCandidate | None = None,
        should_fail_detector: bool = False,
    ) -> tuple[CameraPipeline, StubDetector, MockPlateRecognizer]:
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
        detector = StubDetector(detections=default_dets, should_fail=should_fail_detector)
        stream_processor = StreamProcessor(
            detector=detector,
            config=StreamProcessorConfig(frame_stride=1),
        )

        # min_hits=1 so track immediately transitions TENTATIVE -> CONFIRMED
        vehicle_tracker = VehicleTracker(config=TrackerConfig(min_hits=1))

        default_cand = candidate if candidate is not None else PlateCandidate(
            raw_text="GJ05AB1234",
            normalized_plate="GJ05AB1234",
            confidence=0.93,
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

    return _create


# ─────────────────────────────────────────────────────────────────────────────
# End-to-End Validation Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_e2e_successful_detection_to_database(e2e_pipeline_factory, test_camera, db_session):
    """
    Full pipeline path:
    Synthetic FramePacket → CameraPipeline → ANPRResult → ANPRPersistenceService → DB (Vehicle + Event).
    """
    pipeline, detector, recognizer = e2e_pipeline_factory(camera_id="CAM-01")
    packet = make_synthetic_packet(camera_id="CAM-01", pts_ms=1000.0)

    # 1. Execute full AI pipeline
    pipeline_result: PipelineResult = pipeline.process_frame(packet)

    assert pipeline_result is not None
    assert pipeline_result.has_detections is True
    assert pipeline_result.has_tracks is True
    assert pipeline_result.has_plates is True
    assert len(pipeline_result.recognized_plates) == 1

    anpr_result: ANPRResult = pipeline_result.recognized_plates[0]
    assert anpr_result.status == "RECOGNIZED"
    assert anpr_result.normalized_plate == "GJ05AB1234"
    assert anpr_result.confidence == 0.93

    # 2. Execute persistence service
    persistence_service = ANPRPersistenceService(db_session)
    persist_outcome = persistence_service.persist_result(
        anpr_result=anpr_result,
        snapshot_path="/snapshots/cam01/frame_1000.jpg",
    )

    assert persist_outcome is not None
    assert persist_outcome.camera_id == test_camera.id
    assert persist_outcome.camera_code == "CAM-01"
    assert persist_outcome.plate_number == "GJ05AB1234"
    assert persist_outcome.confidence == pytest.approx(0.93, rel=1e-3)
    assert persist_outcome.snapshot_path == "/snapshots/cam01/frame_1000.jpg"

    # 3. Verify SQLite database records
    vehicle = db_session.query(Vehicle).filter(Vehicle.id == persist_outcome.vehicle_id).first()
    assert vehicle is not None
    assert vehicle.plate_number == "GJ05AB1234"

    event_row = db_session.query(Event).filter(Event.id == persist_outcome.event_id).first()
    assert event_row is not None
    assert event_row.camera_id == test_camera.id
    assert event_row.vehicle_id == vehicle.id
    assert event_row.event_type == "anpr_detection"
    assert event_row.object_type == "vehicle"
    assert event_row.confidence == pytest.approx(0.93, rel=1e-3)
    assert event_row.snapshot_path == "/snapshots/cam01/frame_1000.jpg"
    assert isinstance(event_row.timestamp, datetime)


def test_e2e_repeated_plate_deduplication(e2e_pipeline_factory, test_camera, db_session):
    """
    Two sequential pipeline passes for the same vehicle create two Events
    linked to exactly one deduplicated Vehicle in the database.
    """
    pipeline, detector, recognizer = e2e_pipeline_factory(camera_id="CAM-01")
    persistence_service = ANPRPersistenceService(db_session)

    # Frame 1
    packet1 = make_synthetic_packet(camera_id="CAM-01", pts_ms=1000.0, seed=1)
    res1 = pipeline.process_frame(packet1)
    outcome1 = persistence_service.persist_result(res1.recognized_plates[0])

    # Frame 2 (subsequent observation of same vehicle, resetting coordinator lock for second read)
    # Clear lock so recognizer evaluates second frame
    track_state = pipeline.anpr_coordinator.get_track_state("CAM-01", 1)
    if track_state:
        track_state.locked = False
        track_state.last_attempt_pts_ms = 0.0

    packet2 = make_synthetic_packet(camera_id="CAM-01", pts_ms=1500.0, seed=2)
    res2 = pipeline.process_frame(packet2)
    outcome2 = persistence_service.persist_result(res2.recognized_plates[0])

    assert outcome1 is not None
    assert outcome2 is not None
    assert outcome1.vehicle_id == outcome2.vehicle_id
    assert outcome1.event_id != outcome2.event_id

    # Database verification: exactly 1 Vehicle, 2 Events
    vehicles = db_session.query(Vehicle).all()
    assert len(vehicles) == 1
    assert vehicles[0].plate_number == "GJ05AB1234"

    events = db_session.query(Event).all()
    assert len(events) == 2
    assert events[0].vehicle_id == vehicles[0].id
    assert events[1].vehicle_id == vehicles[0].id


def test_e2e_non_persistable_anpr_creates_no_database_records(e2e_pipeline_factory, test_camera, db_session):
    """
    When ANPR fails or produces invalid format, CameraPipeline emits unpersistable results
    and ANPRPersistenceService writes zero records to the database.
    """
    invalid_candidate = PlateCandidate(
        raw_text="INVALID123",
        normalized_plate="INVALID123",
        confidence=0.40,
        is_valid_format=False,
        pts_ms=1000.0,
    )
    pipeline, _, _ = e2e_pipeline_factory(camera_id="CAM-01", candidate=invalid_candidate)
    packet = make_synthetic_packet(camera_id="CAM-01", pts_ms=1000.0)

    res = pipeline.process_frame(packet)
    assert len(res.anpr_results) == 1
    anpr_res = res.anpr_results[0]
    assert anpr_res.status != "RECOGNIZED" or not anpr_res.is_valid_format

    persistence_service = ANPRPersistenceService(db_session)
    outcome = persistence_service.persist_result(anpr_res)

    assert outcome is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0


def test_e2e_metadata_preservation(e2e_pipeline_factory, test_camera, db_session):
    """
    Presentation timestamp (pts_ms), camera_id, and confidence are faithfully propagated
    through the pipeline layers, and pts_ms is NOT conflated with database wall-clock time.
    """
    explicit_pts = 4250.0
    pipeline, _, _ = e2e_pipeline_factory(camera_id="CAM-01")
    packet = make_synthetic_packet(camera_id="CAM-01", pts_ms=explicit_pts)

    pipeline_res = pipeline.process_frame(packet)
    assert pipeline_res.pts_ms == explicit_pts
    assert pipeline_res.camera_id == "CAM-01"

    anpr = pipeline_res.recognized_plates[0]
    assert anpr.pts_ms == explicit_pts
    assert anpr.camera_id == "CAM-01"

    persistence_service = ANPRPersistenceService(db_session)
    outcome = persistence_service.persist_result(anpr)

    assert outcome is not None
    # Verify timestamp in database is a real wall-clock year (e.g. 2026), NOT 1970 from pts_ms
    assert outcome.timestamp.year >= 2026
    assert outcome.confidence == pytest.approx(0.93, rel=1e-3)


def test_e2e_discontinuity_handling(e2e_pipeline_factory, test_camera, db_session):
    """
    Discontinuity packet flushes tracker and coordinator states cleanly across the pipeline
    without raising exceptions or corrupting the persistence layer.
    """
    pipeline, _, _ = e2e_pipeline_factory(camera_id="CAM-01")
    persistence_service = ANPRPersistenceService(db_session)

    # Normal frame
    packet1 = make_synthetic_packet(camera_id="CAM-01", pts_ms=1000.0, is_discontinuity=False)
    res1 = pipeline.process_frame(packet1)
    outcome1 = persistence_service.persist_result(res1.recognized_plates[0])
    assert outcome1 is not None

    # Discontinuity frame (e.g. stream dropped and reconnected)
    packet_disc = make_synthetic_packet(camera_id="CAM-01", pts_ms=5000.0, is_discontinuity=True)
    res_disc = pipeline.process_frame(packet_disc)

    assert res_disc is not None
    assert len(res_disc.errors) == 0


def test_e2e_detector_exception_isolation(e2e_pipeline_factory, test_camera, db_session):
    """
    Detector exception is isolated inside CameraPipeline, recorded in telemetry,
    and downstream stages are cleanly skipped so zero database records are created.
    """
    pipeline, detector, _ = e2e_pipeline_factory(camera_id="CAM-01", should_fail_detector=True)
    packet = make_synthetic_packet(camera_id="CAM-01", pts_ms=1000.0)

    res = pipeline.process_frame(packet)

    # StreamProcessor safely caught detector exception and returned None
    assert res.skipped is True
    assert pipeline.stream_processor.inference_errors == 1
    assert len(res.anpr_results) == 0

    # No database modifications
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0


def test_e2e_unregistered_camera_fails_persistence_cleanly(e2e_pipeline_factory, db_session):
    """
    If the pipeline processes a stream from an unknown camera code not present in DB,
    persistence service safely rejects it and writes nothing.
    """
    # Camera 'UNKNOWN_CAM' is not registered in db_session
    pipeline, _, _ = e2e_pipeline_factory(camera_id="UNKNOWN_CAM")
    packet = make_synthetic_packet(camera_id="UNKNOWN_CAM", pts_ms=1000.0)

    res = pipeline.process_frame(packet)
    assert len(res.recognized_plates) == 1

    persistence_service = ANPRPersistenceService(db_session)
    outcome = persistence_service.persist_result(res.recognized_plates[0])

    assert outcome is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0
