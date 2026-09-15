"""
Tests for Sentinel Phase 13 (GIS / Mapping) backend surface.

Phase 13 adds no tables and no columns: a detection event inherits the
geographic position of the camera that recorded it. These tests cover:

- VehicleTimelineEntry carrying camera latitude/longitude.
- Cameras without survey coordinates yielding None (never 0.0).
- Cross-camera ordering being preserved alongside coordinates.
- The pre-existing timeline response contract remaining unchanged.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure Sentinel root and backend root are on sys.path
sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.database.connection import Base
from app.database.dependencies import get_db
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.routes import vehicles as vehicles_routes
from app.schemas.camera import CameraResponse
from app.schemas.vehicle import VehicleTimelineEntry

# Surat, Gujarat — matches the coordinates used by backend/scripts/seed_camera.py
SURAT_LAT, SURAT_LON = 21.1702, 72.8311


@pytest.fixture
def db_session():
    """
    In-memory SQLite session fixture with foreign keys enforced.

    StaticPool keeps every thread on the same in-memory database, so rows created
    here are visible to requests served on TestClient's worker thread.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

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
def client(db_session):
    """
    TestClient over a minimal app exposing only the vehicles router.

    Mounting just this router keeps the GIS tests independent of the AI engine
    and streaming stacks, which the full app imports.
    """
    app = FastAPI()
    app.include_router(vehicles_routes.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client


def make_camera(db, code, name, lat=None, lon=None, location=None):
    camera = Camera(
        camera_code=code,
        name=name,
        location=location,
        latitude=lat,
        longitude=lon,
        status="active",
    )
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return camera


def make_detection(db, camera, vehicle, when, event_type="anpr_detection", confidence=0.9):
    detection = Event(
        camera_id=camera.id,
        vehicle_id=vehicle.id,
        event_type=event_type,
        object_type="vehicle",
        confidence=confidence,
        timestamp=when,
    )
    db.add(detection)
    db.commit()
    db.refresh(detection)
    return detection


@pytest.fixture
def vehicle(db_session):
    veh = Vehicle(plate_number="GJ05AB1234")
    db_session.add(veh)
    db_session.commit()
    db_session.refresh(veh)
    return veh


# --- Schema unit tests -------------------------------------------------------


def test_timeline_entry_round_trips_coordinates():
    """T1: VehicleTimelineEntry accepts and preserves latitude/longitude."""
    entry = VehicleTimelineEntry(
        event_id=1,
        camera_id=2,
        camera_code="CAM-001",
        camera_name="Ring Road Gate",
        location="Surat",
        latitude=SURAT_LAT,
        longitude=SURAT_LON,
        event_type="anpr_detection",
        confidence=0.87,
        timestamp=datetime(2026, 9, 10, 12, 0, 0),
    )
    assert entry.latitude == SURAT_LAT
    assert entry.longitude == SURAT_LON
    assert entry.model_dump()["latitude"] == SURAT_LAT


def test_timeline_entry_coordinates_default_to_none():
    """T2: Coordinates are optional and default to None, never 0.0."""
    entry = VehicleTimelineEntry(
        event_id=1,
        camera_id=2,
        camera_code="CAM-001",
        camera_name="Unsurveyed Camera",
        location=None,
        event_type="anpr_detection",
        confidence=None,
        timestamp=datetime(2026, 9, 10, 12, 0, 0),
    )
    assert entry.latitude is None
    assert entry.longitude is None


@pytest.mark.parametrize("lat,lon", [(-90.0, -180.0), (90.0, 180.0), (0.0, 0.0)])
def test_timeline_entry_accepts_boundary_coordinates(lat, lon):
    """T3: Boundary WGS84 values survive unchanged."""
    entry = VehicleTimelineEntry(
        event_id=1,
        camera_id=1,
        camera_code="CAM-EDGE",
        camera_name="Edge",
        location=None,
        latitude=lat,
        longitude=lon,
        event_type="anpr_detection",
        confidence=None,
        timestamp=datetime(2026, 9, 10, 12, 0, 0),
    )
    assert (entry.latitude, entry.longitude) == (lat, lon)


def test_camera_response_still_exposes_coordinates(db_session):
    """T4: Regression — the camera contract keeps exposing latitude/longitude."""
    camera = make_camera(db_session, "CAM-001", "Ring Road", SURAT_LAT, SURAT_LON, "Surat")
    payload = CameraResponse.model_validate(camera).model_dump()
    assert payload["latitude"] == SURAT_LAT
    assert payload["longitude"] == SURAT_LON


# --- API tests ---------------------------------------------------------------


def test_timeline_entry_inherits_camera_coordinates(client, db_session, vehicle):
    """T5: An event reports the exact coordinates of its recording camera."""
    camera = make_camera(db_session, "CAM-001", "Ring Road", SURAT_LAT, SURAT_LON, "Surat")
    make_detection(db_session, camera, vehicle, datetime(2026, 9, 10, 9, 0, 0))

    response = client.get(f"/api/vehicles/{vehicle.plate_number}/timeline")
    assert response.status_code == 200

    entry = response.json()["timeline"][0]
    assert entry["latitude"] == SURAT_LAT
    assert entry["longitude"] == SURAT_LON
    assert entry["camera_code"] == "CAM-001"


def test_camera_without_coordinates_yields_null_not_zero(client, db_session, vehicle):
    """T6: A camera with no survey coordinates must not be plotted at (0, 0)."""
    camera = make_camera(db_session, "CAM-NOGEO", "Unsurveyed")
    make_detection(db_session, camera, vehicle, datetime(2026, 9, 10, 9, 0, 0))

    response = client.get(f"/api/vehicles/{vehicle.plate_number}/timeline")
    entry = response.json()["timeline"][0]
    assert entry["latitude"] is None
    assert entry["longitude"] is None


def test_multi_camera_timeline_is_ordered_and_geolocated(client, db_session, vehicle):
    """T7: Cross-camera movement is chronological with coordinates attached."""
    cam_a = make_camera(db_session, "CAM-A", "Adajan", 21.1959, 72.7933, "Adajan")
    cam_b = make_camera(db_session, "CAM-B", "Athwa", 21.1702, 72.8311, "Athwa")
    cam_c = make_camera(db_session, "CAM-C", "Varachha", 21.2049, 72.8757, "Varachha")

    # Inserted out of order on purpose — the API must sort by timestamp.
    make_detection(db_session, cam_c, vehicle, datetime(2026, 9, 10, 9, 20, 0))
    make_detection(db_session, cam_a, vehicle, datetime(2026, 9, 10, 9, 0, 0))
    make_detection(db_session, cam_b, vehicle, datetime(2026, 9, 10, 9, 10, 0))

    timeline = client.get(f"/api/vehicles/{vehicle.plate_number}/timeline").json()["timeline"]

    assert [e["camera_code"] for e in timeline] == ["CAM-A", "CAM-B", "CAM-C"]
    assert [e["latitude"] for e in timeline] == [21.1959, 21.1702, 21.2049]
    assert [e["longitude"] for e in timeline] == [72.7933, 72.8311, 72.8757]

    timestamps = [e["timestamp"] for e in timeline]
    assert timestamps == sorted(timestamps)


def test_mixed_coordinate_availability_does_not_error(client, db_session, vehicle):
    """T8: A timeline mixing geolocated and non-geolocated cameras still returns 200."""
    cam_geo = make_camera(db_session, "CAM-GEO", "Geolocated", SURAT_LAT, SURAT_LON)
    cam_plain = make_camera(db_session, "CAM-PLAIN", "Not surveyed")

    make_detection(db_session, cam_geo, vehicle, datetime(2026, 9, 10, 9, 0, 0))
    make_detection(db_session, cam_plain, vehicle, datetime(2026, 9, 10, 9, 5, 0))

    response = client.get(f"/api/vehicles/{vehicle.plate_number}/timeline")
    assert response.status_code == 200

    timeline = response.json()["timeline"]
    assert timeline[0]["latitude"] == SURAT_LAT
    assert timeline[1]["latitude"] is None
    assert response.json()["total_detections"] == 2


def test_partial_coordinates_are_reported_as_stored(client, db_session, vehicle):
    """T9: A half-populated camera reports exactly what is stored, uninvented."""
    camera = make_camera(db_session, "CAM-HALF", "Half surveyed", lat=SURAT_LAT, lon=None)
    make_detection(db_session, camera, vehicle, datetime(2026, 9, 10, 9, 0, 0))

    entry = client.get(f"/api/vehicles/{vehicle.plate_number}/timeline").json()["timeline"][0]
    assert entry["latitude"] == SURAT_LAT
    assert entry["longitude"] is None


def test_timeline_response_contract_is_unchanged(client, db_session, vehicle):
    """T10: Regression — the response gains only latitude/longitude, loses nothing."""
    camera = make_camera(db_session, "CAM-001", "Ring Road", SURAT_LAT, SURAT_LON, "Surat")
    make_detection(db_session, camera, vehicle, datetime(2026, 9, 10, 9, 0, 0))

    body = client.get(f"/api/vehicles/{vehicle.plate_number}/timeline").json()

    assert set(body) == {"plate_number", "vehicle", "timeline", "total_detections"}
    assert set(body["timeline"][0]) == {
        "event_id",
        "camera_id",
        "camera_code",
        "camera_name",
        "location",
        "latitude",
        "longitude",
        "event_type",
        "confidence",
        "timestamp",
        "snapshot_path",
    }


def test_vehicle_without_detections_returns_empty_timeline(client, vehicle):
    """T11: A known vehicle with no sightings yields an empty, plottable-safe path."""
    body = client.get(f"/api/vehicles/{vehicle.plate_number}/timeline").json()
    assert body["timeline"] == []
    assert body["total_detections"] == 0


def test_unknown_plate_still_returns_404(client):
    """T12: Regression — unknown plates keep their existing 404 behaviour."""
    assert client.get("/api/vehicles/GJ99ZZ9999/timeline").status_code == 404
