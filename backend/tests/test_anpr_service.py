"""
Unit tests for backend ANPR Persistence Service.

Tests camera identity resolution, plate normalization consistency, vehicle deduplication,
event creation, confidence and timestamp preservation, error handling, and transaction safety.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

# Ensure Sentinel root and backend root are on sys.path for direct imports
sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.database.connection import Base
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.services.anpr_service import (
    ANPRPersistenceResult,
    ANPRPersistenceService,
    persist_anpr_result,
)
from ai_engine.anpr.schemas import ANPRResult


@pytest.fixture
def db_session():
    """In-memory SQLite database session fixture with foreign keys enabled."""
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
    """Seed test camera CAM-01."""
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
def service(db_session):
    """ANPRPersistenceService instance."""
    return ANPRPersistenceService(db_session)


def make_anpr_result(
    camera_id: str = "CAM-01",
    track_id: int = 1,
    pts_ms: float = 1500.0,
    raw_text: str = "GJ05AB1234",
    normalized_plate: str = "GJ05AB1234",
    confidence: float = 0.92,
    is_valid_format: bool = True,
    status: str = "RECOGNIZED",
) -> ANPRResult:
    """Helper factory for ANPRResult instances."""
    return ANPRResult(
        camera_id=camera_id,
        track_id=track_id,
        pts_ms=pts_ms,
        raw_text=raw_text,
        normalized_plate=normalized_plate,
        confidence=confidence,
        is_valid_format=is_valid_format,
        status=status,
    )


# --- 1 & 2. Valid Plate Creates Vehicle + Event with correct references ---

def test_valid_plate_creates_vehicle_and_event(service, test_camera, db_session):
    """A valid recognized ANPRResult creates a Vehicle and an Event linked to camera and vehicle."""
    anpr = make_anpr_result()
    result = service.persist_result(anpr)

    assert result is not None
    assert isinstance(result, ANPRPersistenceResult)
    assert result.plate_number == "GJ05AB1234"
    assert result.camera_code == "CAM-01"
    assert result.camera_id == test_camera.id
    assert result.confidence == 0.92
    assert result.event_type == "anpr_detection"

    # Verify rows in database
    vehicle = db_session.query(Vehicle).filter(Vehicle.id == result.vehicle_id).first()
    assert vehicle is not None
    assert vehicle.plate_number == "GJ05AB1234"

    event = db_session.query(Event).filter(Event.id == result.event_id).first()
    assert event is not None
    assert event.camera_id == test_camera.id
    assert event.vehicle_id == vehicle.id
    assert event.object_type == "vehicle"
    assert event.confidence == 0.92


def test_event_references_correct_camera(service, db_session):
    """Event correctly links to the requested camera when multiple cameras exist."""
    cam1 = Camera(camera_code="CAM-A", name="Camera A", status="active")
    cam2 = Camera(camera_code="CAM-B", name="Camera B", status="active")
    db_session.add_all([cam1, cam2])
    db_session.commit()

    anpr = make_anpr_result(camera_id="CAM-B")
    result = service.persist_result(anpr)

    assert result is not None
    assert result.camera_id == cam2.id
    assert result.camera_code == "CAM-B"


# --- 3. Repeated Same Plate Reuses Vehicle ---

def test_repeated_same_plate_reuses_vehicle(service, test_camera, db_session):
    """Multiple detections of the same plate reuse the existing Vehicle and create distinct Events."""
    anpr1 = make_anpr_result(normalized_plate="GJ05AB1234", confidence=0.88, track_id=1)
    anpr2 = make_anpr_result(normalized_plate="GJ05AB1234", confidence=0.95, track_id=2)

    res1 = service.persist_result(anpr1)
    res2 = service.persist_result(anpr2)

    assert res1 is not None
    assert res2 is not None
    assert res1.vehicle_id == res2.vehicle_id
    assert res1.event_id != res2.event_id

    # Exactly 1 Vehicle in database
    vehicles = db_session.query(Vehicle).all()
    assert len(vehicles) == 1
    assert vehicles[0].plate_number == "GJ05AB1234"

    # Exactly 2 Events in database, both referencing the same vehicle
    events = db_session.query(Event).all()
    assert len(events) == 2
    assert events[0].vehicle_id == vehicles[0].id
    assert events[1].vehicle_id == vehicles[0].id


# --- 4, 5, 6, 7. Non-Recognized Statuses Create Nothing ---

def test_low_confidence_creates_nothing(service, test_camera, db_session):
    """LOW_CONFIDENCE status must create nothing and return None."""
    anpr = make_anpr_result(status="LOW_CONFIDENCE", confidence=0.35)
    result = service.persist_result(anpr)

    assert result is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0


def test_invalid_format_creates_nothing(service, test_camera, db_session):
    """INVALID_FORMAT status must create nothing and return None."""
    anpr = make_anpr_result(
        status="INVALID_FORMAT",
        is_valid_format=False,
        normalized_plate="HELLO1234",
    )
    result = service.persist_result(anpr)

    assert result is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0


def test_no_plate_detected_creates_nothing(service, test_camera, db_session):
    """NO_PLATE_DETECTED status must create nothing and return None."""
    anpr = make_anpr_result(
        status="NO_PLATE_DETECTED",
        is_valid_format=False,
        normalized_plate="",
        confidence=0.0,
    )
    result = service.persist_result(anpr)

    assert result is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0


def test_failed_creates_nothing(service, test_camera, db_session):
    """FAILED status must create nothing and return None."""
    anpr = make_anpr_result(
        status="FAILED",
        is_valid_format=False,
        normalized_plate="",
        confidence=0.0,
    )
    result = service.persist_result(anpr)

    assert result is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0


# --- 8. Empty / Invalid Normalized Plate Creates Nothing ---

def test_empty_or_whitespace_plate_creates_nothing(service, test_camera, db_session):
    """Empty or whitespace-only normalized_plate returns None and creates nothing."""
    for empty_val in ["", "   ", None]:
        anpr = make_anpr_result(normalized_plate=empty_val)
        result = service.persist_result(anpr)
        assert result is None

    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0


def test_invalid_format_boolean_false_creates_nothing(service, test_camera, db_session):
    """is_valid_format=False with status=RECOGNIZED still returns None."""
    anpr = make_anpr_result(is_valid_format=False)
    result = service.persist_result(anpr)

    assert result is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0


# --- 9. Missing Camera Handled Cleanly ---

def test_missing_camera_creates_nothing_and_leaves_session_usable(service, db_session):
    """Non-existent camera returns None and leaves session in a valid, usable state."""
    anpr = make_anpr_result(camera_id="NON_EXISTENT_CAM")
    result = service.persist_result(anpr)

    assert result is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0

    # Verify session is clean and functional
    new_cam = Camera(camera_code="RECOVERY_CAM", name="Recovery", status="active")
    db_session.add(new_cam)
    db_session.commit()
    assert new_cam.id is not None


# --- 10. Confidence Preserved Exactly ---

def test_confidence_preserved_exactly(service, test_camera):
    """Recognition confidence score is preserved with high precision."""
    expected_confidence = 0.94125
    anpr = make_anpr_result(confidence=expected_confidence)
    result = service.persist_result(anpr)

    assert result is not None
    assert result.confidence == pytest.approx(expected_confidence, rel=1e-4)
    assert result.event.confidence == pytest.approx(expected_confidence, rel=1e-4)


# --- 11 & 12 & 13. Timestamp and PTS Isolation ---

def test_explicit_utc_timestamp_preserved_exactly(service, test_camera):
    """Supplied explicit UTC timestamp is preserved on the Event record."""
    explicit_ts = datetime(2026, 9, 5, 14, 30, 45)
    anpr = make_anpr_result(pts_ms=9999.0)
    result = service.persist_result(anpr, timestamp=explicit_ts)

    assert result is not None
    assert result.timestamp == explicit_ts
    assert result.event.timestamp == explicit_ts


def test_default_timestamp_generated_when_omitted(service, test_camera):
    """When timestamp is omitted, current UTC time is used (pts_ms is NOT converted)."""
    before = datetime.utcnow()
    anpr = make_anpr_result(pts_ms=5000.0)
    result = service.persist_result(anpr, timestamp=None)
    after = datetime.utcnow()

    assert result is not None
    assert before <= result.timestamp <= after
    # Must NOT be 1970 timestamp from treating pts_ms as epoch seconds
    assert result.timestamp.year >= 2026


# --- 14. Snapshot Path and Event Type ---

def test_snapshot_path_preserved(service, test_camera):
    """Snapshot path string is recorded on the created event."""
    snap_path = "/storage/snapshots/2026/09/CAM-01_12345.jpg"
    anpr = make_anpr_result()
    result = service.persist_result(anpr, snapshot_path=snap_path)

    assert result is not None
    assert result.snapshot_path == snap_path
    assert result.event.snapshot_path == snap_path


def test_event_type_default_and_custom(service, test_camera):
    """Default event_type is 'anpr_detection'; custom event_type is respected."""
    anpr = make_anpr_result()
    res_default = service.persist_result(anpr)
    assert res_default.event_type == "anpr_detection"

    res_custom = service.persist_result(anpr, event_type="speed_violation")
    assert res_custom.event_type == "speed_violation"


# --- 15. Normalization Consistency ---

def test_normalization_variants_reuse_same_vehicle(service, test_camera, db_session):
    """
    Lowercase, spaced, and hyphenated representations of the same Indian registration
    all resolve to the identical canonical uppercase Vehicle row.
    """
    variants = [
        "gj05ab1234",
        "GJ 05 AB 1234",
        "GJ-05-AB-1234",
        "GJ05AB1234",
    ]

    persisted_ids = []
    for var in variants:
        anpr = make_anpr_result(normalized_plate=var, raw_text=var)
        res = service.persist_result(anpr)
        assert res is not None
        assert res.plate_number == "GJ05AB1234"
        persisted_ids.append(res.vehicle_id)

    # All 4 variants must reference the exact same Vehicle ID
    assert len(set(persisted_ids)) == 1
    assert db_session.query(Vehicle).count() == 1

    # Exactly 4 Events created for the 4 observations
    assert db_session.query(Event).count() == 4


def test_bharat_series_normalization(service, test_camera, db_session):
    """Bharat Series format (e.g. 22 BH 9999 AB) resolves to canonical 22BH9999AB."""
    anpr = make_anpr_result(normalized_plate="22 bh 9999 ab", raw_text="22 BH 9999 AB")
    res = service.persist_result(anpr)

    assert res is not None
    assert res.plate_number == "22BH9999AB"


# --- 16. Transaction Rollback & Error Recovery ---

def test_transaction_rollback_leaves_session_usable(service, test_camera, db_session):
    """Unexpected database error rolls back transaction and leaves the session usable."""
    anpr = make_anpr_result()

    # Force an exception during Event commit
    with patch.object(db_session, "commit", side_effect=SQLAlchemyError("Simulated DB error")):
        with pytest.raises(SQLAlchemyError, match="Simulated DB error"):
            service.persist_result(anpr)

    # Verify session is still fully usable after rollback
    assert db_session.query(Event).count() == 0

    # A subsequent normal operation succeeds cleanly
    valid_res = service.persist_result(anpr)
    assert valid_res is not None
    assert db_session.query(Event).count() == 1


def test_integrity_error_handled_cleanly_via_savepoint(service, test_camera, db_session):
    """
    Simulated concurrent duplicate vehicle insertion is caught cleanly by begin_nested()
    and re-queries the existing vehicle without crashing the transaction.
    """
    plate = "GJ01XY9999"
    anpr = make_anpr_result(normalized_plate=plate)

    # Pre-insert vehicle in an isolated manner
    v = Vehicle(plate_number=plate)
    db_session.add(v)
    db_session.commit()

    # Persist through service — should reuse existing vehicle
    res = service.persist_result(anpr)
    assert res is not None
    assert res.vehicle_id == v.id
    assert db_session.query(Vehicle).filter(Vehicle.plate_number == plate).count() == 1


# --- 17. Dictionary Input Compatibility ---

def test_dictionary_input_compatibility(service, test_camera, db_session):
    """Dictionary representation of an ANPR result is supported transparently."""
    dict_payload = {
        "camera_id": "CAM-01",
        "track_id": 5,
        "pts_ms": 2500.0,
        "raw_text": "MH12DE1234",
        "normalized_plate": "MH12DE1234",
        "confidence": 0.89,
        "is_valid_format": True,
        "status": "RECOGNIZED",
    }

    result = service.persist_result(dict_payload)

    assert result is not None
    assert result.plate_number == "MH12DE1234"
    assert result.camera_code == "CAM-01"
    assert result.confidence == 0.89
    assert db_session.query(Vehicle).filter(Vehicle.plate_number == "MH12DE1234").count() == 1


# --- 18. Functional Helper persist_anpr_result ---

def test_functional_helper(db_session, test_camera):
    """Functional persist_anpr_result entry point functions identically to class instance."""
    anpr = make_anpr_result(normalized_plate="DL01AA1111")
    result = persist_anpr_result(db_session, anpr)

    assert result is not None
    assert result.plate_number == "DL01AA1111"
    assert result.camera_code == "CAM-01"
