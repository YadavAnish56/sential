"""
Unit tests for Sentinel AlertService and Watchlist Alert Integration (Phase 7.5 Stage 1).

Tests:
- T8: Recognized valid Indian plate matching watchlist creates an Alert.
- T9: Alert contains correct camera_id and vehicle_id linkages.
- T10: Recognized valid Indian plate NOT matching watchlist creates no Alert.
- Canonicalization of watchlist plates (case-insensitivity, hyphens/spaces handled via normalize_plate).
- Rejection of invalid/non-Indian plates (cannot create a watchlist alert).
- Rejection of low-confidence or non-recognized ANPR statuses (LOW_CONFIDENCE, INVALID_FORMAT, NO_PLATE_DETECTED, FAILED).
- Duplicate watchlist sightings reuse the same Vehicle entity with independent Alerts and Events.
- Atomic transaction behavior: database failure/rollback leaves zero partial Event or Alert state.
- Standalone AlertService operation as well as integrated persistence via ANPRPersistenceService.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

# Ensure Sentinel root and backend root are on sys.path
sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.database.connection import Base
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.services.alert_service import AlertService, canonicalize_watchlist
from app.services.anpr_service import ANPRPersistenceService, persist_anpr_result
from ai_engine.anpr.schemas import ANPRResult


@pytest.fixture
def db_session():
    """In-memory SQLite database session fixture with foreign keys enforced."""
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
        name="Main Highway Gate",
        location="Sector 12",
        status="active",
    )
    db_session.add(camera)
    db_session.commit()
    db_session.refresh(camera)
    return camera


@pytest.fixture
def alert_service(db_session):
    """AlertService fixture."""
    return AlertService(db_session)


@pytest.fixture
def persistence_service(db_session):
    """ANPRPersistenceService fixture."""
    return ANPRPersistenceService(db_session)


def make_anpr_result(
    camera_id: str = "CAM-01",
    track_id: int = 1,
    pts_ms: float = 1200.0,
    raw_text: str = "GJ01AB1234",
    normalized_plate: str = "GJ01AB1234",
    confidence: float = 0.95,
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


# --- Canonicalization Tests ---

def test_canonicalize_watchlist():
    """Watchlist plates are canonicalized using Indian plate rules; invalid entries omitted."""
    raw_watchlist = [
        "gj-01-ab-1234",       # valid Indian, lower with hyphens
        "DL 04 C AA 1111",     # valid Indian with spaces
        "22 BH 9999 AA",       # valid Bharat series
        "NOT_A_PLATE",         # invalid format -> dropped
        "B DB 4068",           # non-Indian European format -> dropped
        "",                    # empty -> dropped
        None,                  # None -> dropped
    ]
    canonical = canonicalize_watchlist(raw_watchlist)

    assert "GJ01AB1234" in canonical
    assert "DL04CAA1111" in canonical
    assert "22BH9999AA" in canonical
    assert len(canonical) == 3
    assert "NOT_A_PLATE" not in canonical
    assert "BDB4068" not in canonical


# --- T8: Recognized valid Indian plate matching watchlist creates an Alert ---

def test_t8_recognized_plate_matching_watchlist_creates_alert(persistence_service, test_camera, db_session):
    """T8: Recognized valid Indian plate in watchlist creates an Alert record."""
    watchlist = ["GJ01AB1234", "MH12DE5678"]
    anpr = make_anpr_result(normalized_plate="GJ01AB1234")

    result = persistence_service.persist_result(anpr, watchlist=watchlist)

    assert result is not None
    assert result.alert_id is not None
    assert result.alert is not None
    assert result.alert.alert_type == "ANPR_WATCHLIST"
    assert result.alert.severity == "high"
    assert result.alert.status == "new"
    assert "GJ01AB1234" in result.alert.message
    assert "CAM-01" in result.alert.message

    # Verify Alert row in database
    alerts_in_db = db_session.query(Alert).all()
    assert len(alerts_in_db) == 1
    assert alerts_in_db[0].id == result.alert_id


# --- T9: Alert contains correct camera_id and vehicle_id ---

def test_t9_alert_contains_correct_camera_and_vehicle_ids(persistence_service, test_camera, db_session):
    """T9: Created alert is correctly linked to camera_id (FK) and vehicle_id (FK)."""
    watchlist = ["DL01AB9999"]
    anpr = make_anpr_result(normalized_plate="DL01AB9999")

    result = persistence_service.persist_result(anpr, watchlist=watchlist)

    assert result is not None
    alert = result.alert
    assert alert is not None
    assert alert.camera_id == test_camera.id
    assert alert.vehicle_id == result.vehicle_id

    # Verify foreign key linkages resolve to real database entities
    db_vehicle = db_session.query(Vehicle).filter(Vehicle.id == alert.vehicle_id).first()
    assert db_vehicle is not None
    assert db_vehicle.plate_number == "DL01AB9999"

    db_camera = db_session.query(Camera).filter(Camera.id == alert.camera_id).first()
    assert db_camera is not None
    assert db_camera.camera_code == "CAM-01"


# --- T10: Recognized valid Indian plate NOT matching watchlist creates no Alert ---

def test_t10_recognized_plate_not_in_watchlist_creates_no_alert(persistence_service, test_camera, db_session):
    """T10: Valid plate not in watchlist creates Vehicle and Event, but NO Alert."""
    watchlist = ["MH01XY9999", "KA05ZZ1234"]
    anpr = make_anpr_result(normalized_plate="GJ01AB1234")

    result = persistence_service.persist_result(anpr, watchlist=watchlist)

    assert result is not None
    assert result.alert_id is None
    assert result.alert is None

    # Event and Vehicle ARE persisted
    assert db_session.query(Vehicle).count() == 1
    assert db_session.query(Event).count() == 1
    # Zero alerts created
    assert db_session.query(Alert).count() == 0


# --- Negative Tests: Non-Indian, Low-Confidence, Invalid Statuses ---

def test_invalid_non_indian_plate_cannot_create_watchlist_alert(persistence_service, test_camera, db_session):
    """Non-Indian / invalid plate format must never create an Alert or Event."""
    # Even if European plate is in watchlist, non-Indian format must be rejected
    watchlist = ["CD98E02", "OB98E02"]
    anpr = make_anpr_result(
        raw_text="CD 98E02",
        normalized_plate="CD98E02",
        is_valid_format=False,
        status="INVALID_FORMAT",
    )

    result = persistence_service.persist_result(anpr, watchlist=watchlist)

    assert result is None
    assert db_session.query(Vehicle).count() == 0
    assert db_session.query(Event).count() == 0
    assert db_session.query(Alert).count() == 0


def test_low_confidence_cannot_create_watchlist_alert(persistence_service, test_camera, db_session):
    """LOW_CONFIDENCE status must never create an Alert even if plate string matches."""
    watchlist = ["GJ01AB1234"]
    anpr = make_anpr_result(
        normalized_plate="GJ01AB1234",
        confidence=0.30,
        status="LOW_CONFIDENCE",
    )

    result = persistence_service.persist_result(anpr, watchlist=watchlist)

    assert result is None
    assert db_session.query(Alert).count() == 0


def test_no_plate_detected_cannot_create_watchlist_alert(persistence_service, test_camera, db_session):
    """NO_PLATE_DETECTED status must never create an Alert."""
    watchlist = ["GJ01AB1234"]
    anpr = make_anpr_result(
        normalized_plate="",
        status="NO_PLATE_DETECTED",
        is_valid_format=False,
    )

    result = persistence_service.persist_result(anpr, watchlist=watchlist)

    assert result is None
    assert db_session.query(Alert).count() == 0


def test_failed_status_cannot_create_watchlist_alert(persistence_service, test_camera, db_session):
    """FAILED status must never create an Alert."""
    watchlist = ["GJ01AB1234"]
    anpr = make_anpr_result(
        normalized_plate="",
        status="FAILED",
        is_valid_format=False,
    )

    result = persistence_service.persist_result(anpr, watchlist=watchlist)

    assert result is None
    assert db_session.query(Alert).count() == 0


# --- Duplicate Watchlist Sightings: Same Vehicle, Multiple Alerts ---

def test_duplicate_watchlist_sightings_reuse_vehicle_and_create_distinct_alerts(
    persistence_service, test_camera, db_session
):
    """Multiple sightings of a watchlist vehicle reuse the same Vehicle entity with distinct Events and Alerts."""
    watchlist = ["GJ01AB1234"]
    anpr1 = make_anpr_result(normalized_plate="GJ01AB1234", track_id=1, confidence=0.88)
    anpr2 = make_anpr_result(normalized_plate="GJ01AB1234", track_id=2, confidence=0.94)

    res1 = persistence_service.persist_result(anpr1, watchlist=watchlist)
    res2 = persistence_service.persist_result(anpr2, watchlist=watchlist)

    assert res1 is not None and res2 is not None
    # Reuses identical vehicle
    assert res1.vehicle_id == res2.vehicle_id
    assert db_session.query(Vehicle).count() == 1

    # Distinct Events
    assert res1.event_id != res2.event_id
    assert db_session.query(Event).count() == 2

    # Distinct Alerts, both referencing the same vehicle and camera
    assert res1.alert_id != res2.alert_id
    alerts = db_session.query(Alert).all()
    assert len(alerts) == 2
    assert alerts[0].vehicle_id == res1.vehicle_id
    assert alerts[1].vehicle_id == res1.vehicle_id
    assert alerts[0].camera_id == test_camera.id
    assert alerts[1].camera_id == test_camera.id


# --- Transaction Rollback Safety ---

def test_database_rollback_leaves_no_partial_alert_or_event(persistence_service, test_camera, db_session):
    """A database error during commit must roll back cleanly, leaving zero partial Event or Alert rows."""
    watchlist = ["GJ01AB1234"]
    anpr = make_anpr_result(normalized_plate="GJ01AB1234")

    # Simulate database failure on commit
    with patch.object(db_session, "commit", side_effect=SQLAlchemyError("Simulated DB connection drop")):
        with pytest.raises(SQLAlchemyError):
            persistence_service.persist_result(anpr, watchlist=watchlist)

    # Verify zero partial alert or event state left behind
    assert db_session.query(Event).count() == 0
    assert db_session.query(Alert).count() == 0


# --- Standalone AlertService Tests ---

def test_standalone_alert_service_direct_call(alert_service, test_camera, db_session):
    """AlertService can be invoked directly to evaluate plates and generate alerts."""
    # Seed vehicle first
    veh = Vehicle(plate_number="GJ01AB1234")
    db_session.add(veh)
    db_session.commit()
    db_session.refresh(veh)

    watchlist = ["GJ01AB1234"]
    alert = alert_service.check_and_create_watchlist_alert(
        plate_number="gj-01-ab-1234",  # lowercase with hyphens
        camera_id=test_camera.id,
        vehicle_id=veh.id,
        camera_code=test_camera.camera_code,
        watchlist=watchlist,
        commit=True,
    )

    assert alert is not None
    assert alert.camera_id == test_camera.id
    assert alert.vehicle_id == veh.id
    assert alert.alert_type == "ANPR_WATCHLIST"
    assert alert.severity == "high"
    assert "GJ01AB1234" in alert.message

    # Test non-match returns None
    no_match = alert_service.check_and_create_watchlist_alert(
        plate_number="MH12AB9999",
        camera_id=test_camera.id,
        vehicle_id=veh.id,
        camera_code=test_camera.camera_code,
        watchlist=watchlist,
        commit=True,
    )
    assert no_match is None
