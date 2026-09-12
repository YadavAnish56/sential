"""
Automated tests for Sentinel Phase 13A — GIS Backend Foundation.

Covers:
1. /api/cameras/map returns cameras with valid coordinates
2. cameras without coordinates are handled safely (no fake coords, no crash)
3. latitude validation (-90 to 90)
4. longitude validation (-180 to 180)
5. map endpoint preserves camera identity/status information and filtering
6. vehicle timeline returns geographic camera information when coordinates exist
7. vehicle timeline handles cameras without coordinates safely (mixed routes)
8. existing camera APIs remain compatible
"""

import sys
from pathlib import Path
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure project root and backend root are on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
for p in [str(ROOT_DIR), str(BACKEND_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from app.main import app
from app.database.connection import Base
from app.database.dependencies import get_db
from app.models.camera import Camera
from app.models.vehicle import Vehicle
from app.models.event import Event
from app.schemas.camera import CameraBase, CameraMapMarker, sanitize_coordinates
from app.schemas.vehicle import VehicleTimelineEntry


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite database for GIS testing."""
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


@pytest.fixture
def client(db_session):
    """FastAPI TestClient with overridden get_db dependency."""
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# -------------------------------------------------------------------------
# 1. /api/cameras/map returns cameras with valid coordinates
# -------------------------------------------------------------------------
def test_cameras_map_returns_valid_coordinates(client, db_session):
    cam = Camera(
        camera_code="CAM-SURAT-01",
        name="Surat Ring Road Entry",
        location="Ring Road Junction 4",
        latitude=21.1702,
        longitude=72.8311,
        stream_url="rtsp://admin:supersecret@192.168.1.100:554/stream1",
        status="online",
        vendor="Hikvision",
    )
    db_session.add(cam)
    db_session.commit()

    resp = client.get("/api/cameras/map")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1

    marker = data[0]
    assert marker["id"] == cam.id
    assert marker["camera_code"] == "CAM-SURAT-01"
    assert marker["name"] == "Surat Ring Road Entry"
    assert marker["location"] == "Ring Road Junction 4"
    assert marker["latitude"] == pytest.approx(21.1702, rel=1e-4)
    assert marker["longitude"] == pytest.approx(72.8311, rel=1e-4)
    assert marker["status"] == "online"

    # Security requirement: secrets and RTSP URLs MUST NOT be exposed
    assert "stream_url" not in marker
    assert "supersecret" not in str(data)
    assert "vendor" not in marker


# -------------------------------------------------------------------------
# 2. Cameras without coordinates are handled safely
# -------------------------------------------------------------------------
def test_cameras_without_coordinates_handled_safely(client, db_session):
    # Camera with valid coordinates
    cam_valid = Camera(
        camera_code="CAM-VALID",
        name="Valid Camera",
        latitude=23.0225,
        longitude=72.5714,
        status="online",
    )
    # Camera with None coordinates
    cam_unmapped = Camera(
        camera_code="CAM-UNMAPPED",
        name="Unmapped Camera",
        latitude=None,
        longitude=None,
        status="offline",
    )
    # Camera with legacy corrupt/out-of-range coordinates in DB
    cam_corrupt = Camera(
        camera_code="CAM-CORRUPT",
        name="Corrupt Coords Camera",
        latitude=999.0,
        longitude=-500.0,
        status="online",
    )
    db_session.add_all([cam_valid, cam_unmapped, cam_corrupt])
    db_session.commit()

    # Query without filter: all cameras returned, unmapped/corrupt have null coordinates (never fake 0,0)
    resp = client.get("/api/cameras/map")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3

    valid_entry = next(c for c in data if c["camera_code"] == "CAM-VALID")
    assert valid_entry["latitude"] == pytest.approx(23.0225, rel=1e-4)
    assert valid_entry["longitude"] == pytest.approx(72.5714, rel=1e-4)

    unmapped_entry = next(c for c in data if c["camera_code"] == "CAM-UNMAPPED")
    assert unmapped_entry["latitude"] is None
    assert unmapped_entry["longitude"] is None

    corrupt_entry = next(c for c in data if c["camera_code"] == "CAM-CORRUPT")
    # Must NOT produce 999.0 or invalid map coordinates
    assert corrupt_entry["latitude"] is None
    assert corrupt_entry["longitude"] is None

    # Query with only_mapped=true: returns only cameras with valid coordinates
    resp_filtered = client.get("/api/cameras/map?only_mapped=true")
    assert resp_filtered.status_code == 200
    filtered_data = resp_filtered.json()
    assert len(filtered_data) == 1
    assert filtered_data[0]["camera_code"] == "CAM-VALID"


# -------------------------------------------------------------------------
# 3. Latitude validation
# -------------------------------------------------------------------------
def test_latitude_validation(client):
    # Model-level validation boundaries
    valid_cam = CameraBase(camera_code="C1", name="C1", latitude=90.0, longitude=0.0)
    assert valid_cam.latitude == 90.0
    valid_cam_neg = CameraBase(camera_code="C2", name="C2", latitude=-90.0, longitude=0.0)
    assert valid_cam_neg.latitude == -90.0

    # Model-level validation out of bounds
    with pytest.raises(ValidationError):
        CameraBase(camera_code="C3", name="C3", latitude=90.001)

    with pytest.raises(ValidationError):
        CameraBase(camera_code="C4", name="C4", latitude=-90.001)

    # API POST validation
    bad_post = client.post(
        "/api/cameras",
        json={
            "camera_code": "CAM-BAD-LAT",
            "name": "Bad Latitude",
            "latitude": 95.5,
            "longitude": 72.0,
        },
    )
    assert bad_post.status_code == 422

    # Map marker schema validation
    with pytest.raises(ValidationError):
        CameraMapMarker(
            id=1,
            camera_code="C5",
            name="C5",
            latitude=120.0,
            longitude=0.0,
            status="online",
        )


# -------------------------------------------------------------------------
# 4. Longitude validation
# -------------------------------------------------------------------------
def test_longitude_validation(client):
    # Model-level validation boundaries
    valid_cam = CameraBase(camera_code="C1", name="C1", latitude=0.0, longitude=180.0)
    assert valid_cam.longitude == 180.0
    valid_cam_neg = CameraBase(camera_code="C2", name="C2", latitude=0.0, longitude=-180.0)
    assert valid_cam_neg.longitude == -180.0

    # Model-level validation out of bounds
    with pytest.raises(ValidationError):
        CameraBase(camera_code="C3", name="C3", longitude=180.001)

    with pytest.raises(ValidationError):
        CameraBase(camera_code="C4", name="C4", longitude=-180.001)

    # API POST validation
    bad_post = client.post(
        "/api/cameras",
        json={
            "camera_code": "CAM-BAD-LON",
            "name": "Bad Longitude",
            "latitude": 20.0,
            "longitude": -185.0,
        },
    )
    assert bad_post.status_code == 422

    # Map marker schema validation
    with pytest.raises(ValidationError):
        CameraMapMarker(
            id=1,
            camera_code="C5",
            name="C5",
            latitude=0.0,
            longitude=200.0,
            status="online",
        )


# -------------------------------------------------------------------------
# 5. Map endpoint preserves camera identity and status information
# -------------------------------------------------------------------------
def test_map_endpoint_preserves_camera_identity_and_status(client, db_session):
    cams = [
        Camera(
            camera_code="CAM-ONLINE-1",
            name="North Gate Camera",
            location="Sector 1",
            latitude=23.01,
            longitude=72.51,
            status="online",
        ),
        Camera(
            camera_code="CAM-OFFLINE-1",
            name="South Perimeter",
            location="Sector 4",
            latitude=23.02,
            longitude=72.52,
            status="offline",
        ),
        Camera(
            camera_code="CAM-DEGRADED-1",
            name="Warehouse Cam",
            location="Sector 2",
            latitude=23.03,
            longitude=72.53,
            status="degraded",
        ),
    ]
    db_session.add_all(cams)
    db_session.commit()

    resp = client.get("/api/cameras/map")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3

    statuses = {c["camera_code"]: c["status"] for c in data}
    assert statuses["CAM-ONLINE-1"] == "online"
    assert statuses["CAM-OFFLINE-1"] == "offline"
    assert statuses["CAM-DEGRADED-1"] == "degraded"

    # Status filter support
    resp_online = client.get("/api/cameras/map?status=online")
    assert resp_online.status_code == 200
    online_data = resp_online.json()
    assert len(online_data) == 1
    assert online_data[0]["camera_code"] == "CAM-ONLINE-1"


# -------------------------------------------------------------------------
# 6. Vehicle timeline returns geographic camera info when coordinates exist
# -------------------------------------------------------------------------
def test_vehicle_timeline_returns_geographic_info_when_coordinates_exist(client, db_session):
    cam = Camera(
        camera_code="CAM-AHMEDABAD-01",
        name="SG Highway Toll",
        location="SG Highway KM 12",
        latitude=23.0456,
        longitude=72.5123,
        status="online",
    )
    db_session.add(cam)
    db_session.flush()

    vehicle = Vehicle(plate_number="GJ01AB1234", vehicle_type="car")
    db_session.add(vehicle)
    db_session.flush()

    event = Event(
        camera_id=cam.id,
        vehicle_id=vehicle.id,
        event_type="vehicle_detection",
        confidence=0.96,
        timestamp=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.commit()

    resp = client.get("/api/vehicles/GJ01AB1234/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["plate_number"] == "GJ01AB1234"
    assert data["total_detections"] == 1
    assert len(data["timeline"]) == 1

    entry = data["timeline"][0]
    assert entry["camera_id"] == cam.id
    assert entry["camera_code"] == "CAM-AHMEDABAD-01"
    assert entry["camera_name"] == "SG Highway Toll"
    assert entry["location"] == "SG Highway KM 12"
    assert entry["latitude"] == pytest.approx(23.0456, rel=1e-4)
    assert entry["longitude"] == pytest.approx(72.5123, rel=1e-4)
    assert entry["event_type"] == "vehicle_detection"
    assert entry["confidence"] == pytest.approx(0.96, rel=1e-2)


# -------------------------------------------------------------------------
# 7. Vehicle timeline handles cameras without coordinates safely
# -------------------------------------------------------------------------
def test_vehicle_timeline_handles_cameras_without_coordinates_safely(client, db_session):
    cam_with_coords = Camera(
        camera_code="CAM-COORDS",
        name="Camera With Coords",
        latitude=22.3039,
        longitude=70.8022,
        status="online",
    )
    cam_no_coords = Camera(
        camera_code="CAM-NO-COORDS",
        name="Camera Without Coords",
        latitude=None,
        longitude=None,
        status="online",
    )
    db_session.add_all([cam_with_coords, cam_no_coords])
    db_session.flush()

    vehicle = Vehicle(plate_number="GJ03XY9876", vehicle_type="truck")
    db_session.add(vehicle)
    db_session.flush()

    # Detections at both cameras
    t1 = datetime(2026, 9, 11, 8, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 9, 11, 8, 30, 0, tzinfo=timezone.utc)

    e1 = Event(
        camera_id=cam_with_coords.id,
        vehicle_id=vehicle.id,
        event_type="anpr_detection",
        confidence=0.91,
        timestamp=t1,
    )
    e2 = Event(
        camera_id=cam_no_coords.id,
        vehicle_id=vehicle.id,
        event_type="anpr_detection",
        confidence=0.88,
        timestamp=t2,
    )
    db_session.add_all([e1, e2])
    db_session.commit()

    resp = client.get("/api/vehicles/GJ03XY9876/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_detections"] == 2

    # Chronological ordering preserved
    first, second = data["timeline"]
    assert first["camera_code"] == "CAM-COORDS"
    assert first["latitude"] == pytest.approx(22.3039, rel=1e-4)
    assert first["longitude"] == pytest.approx(70.8022, rel=1e-4)

    assert second["camera_code"] == "CAM-NO-COORDS"
    assert second["latitude"] is None
    assert second["longitude"] is None
    assert second["event_type"] == "anpr_detection"


# -------------------------------------------------------------------------
# 8. Existing camera APIs remain compatible
# -------------------------------------------------------------------------
def test_existing_camera_apis_remain_compatible(client, db_session):
    # Test POST /api/cameras
    post_resp = client.post(
        "/api/cameras",
        json={
            "camera_code": "CAM-COMPAT-01",
            "name": "Compatibility Test Camera",
            "location": "Main Entrance",
            "latitude": 23.1000,
            "longitude": 72.6000,
            "status": "online",
        },
    )
    assert post_resp.status_code == 201
    created = post_resp.json()
    assert created["camera_code"] == "CAM-COMPAT-01"
    assert created["latitude"] == pytest.approx(23.1000, rel=1e-4)
    assert created["longitude"] == pytest.approx(72.6000, rel=1e-4)
    cam_id = created["id"]

    # Test GET /api/cameras (list response format: {"cameras": [...], "total": int})
    list_resp = client.get("/api/cameras")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert "cameras" in list_data
    assert "total" in list_data
    assert list_data["total"] >= 1
    matched = next(c for c in list_data["cameras"] if c["id"] == cam_id)
    assert matched["camera_code"] == "CAM-COMPAT-01"

    # Test GET /api/cameras/{id}
    get_resp = client.get(f"/api/cameras/{cam_id}")
    assert get_resp.status_code == 200
    cam_data = get_resp.json()
    assert cam_data["id"] == cam_id
    assert cam_data["camera_code"] == "CAM-COMPAT-01"

    # Test PATCH /api/cameras/{id}
    patch_resp = client.patch(
        f"/api/cameras/{cam_id}",
        json={"name": "Updated Compatibility Cam", "latitude": 23.2000},
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["name"] == "Updated Compatibility Cam"
    assert updated["latitude"] == pytest.approx(23.2000, rel=1e-4)
    assert updated["longitude"] == pytest.approx(72.6000, rel=1e-4)
