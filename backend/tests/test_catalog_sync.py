"""
Targeted tests for Government CCTV Catalogue Synchronization & Multi-Camera Onboarding.
Tests all 11 required scenarios:
1. valid catalogue parse
2. malformed catalogue entry
3. duplicate camera handling
4. create/upsert behavior
5. update behavior
6. unchanged camera behavior
7. missing coordinates
8. authentication failure
9. network timeout
10. credential isolation
11. correct camera/stream identifier mapping & endpoint integration
"""

import json
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models.camera import Camera
from app.database.connection import Base
from app.database.dependencies import get_db
from app.schemas.camera import sanitize_stream_url
from app.services.catalog_sync import CameraCatalogSyncService
from streaming.camera_catalog import CameraCatalog, CameraCatalogItem, CatalogFetchResult


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- 1. Valid Catalogue Parse ---
def test_1_valid_catalogue_parse():
    sample_json = json.dumps([
        {
            "camera_id": "CAM-01-SG-HIGHWAY",
            "name": "Pakwan Crossroad Junction",
            "department": "Gujarat Police Traffic Branch",
            "location": "SG Highway intersection",
            "latitude": 23.0489,
            "longitude": 72.5054,
            "codec": "H264",
            "stream_url": "rtsp://103.250.160.189:8554/stream/1",
            "whep_url": "http://103.250.160.189:8889/stream/1/whep",
        }
    ])

    items = CameraCatalog.parse_catalog_json(sample_json)
    assert len(items) == 1
    cam = items[0]
    assert cam.camera_id == "CAM-01-SG-HIGHWAY"
    assert cam.name == "Pakwan Crossroad Junction"
    assert cam.department == "Gujarat Police Traffic Branch"
    assert cam.location == "SG Highway intersection"
    assert cam.latitude == 23.0489
    assert cam.longitude == 72.5054
    assert cam.codec == "H264"
    assert cam.rtsp_url == "rtsp://103.250.160.189:8554/stream/1"
    assert cam.webrtc_url == "http://103.250.160.189:8889/stream/1/whep"


# --- 2. Malformed Catalogue Entry ---
def test_2_malformed_catalogue_entry(db_session):
    items = [
        CameraCatalogItem(camera_id="", name="Missing ID"),
        CameraCatalogItem(camera_id="   ", name="Blank ID"),
        CameraCatalogItem(camera_id="CAM-VALID-1", name="Valid Camera 1"),
    ]

    service = CameraCatalogSyncService(db=db_session)
    result = service.sync(raw_items=items)

    assert result.success is True
    assert result.stats.total_received == 3
    assert result.stats.created == 1
    assert result.stats.skipped == 2

    cam = db_session.query(Camera).filter(Camera.camera_code == "CAM-VALID-1").first()
    assert cam is not None
    assert cam.name == "Valid Camera 1"


# --- 3. Duplicate Camera Handling ---
def test_3_duplicate_camera_handling(db_session):
    # Two items with the same camera code in the same batch
    items = [
        CameraCatalogItem(camera_id="CAM-DUP-01", name="First Instance", location="Loc A"),
        CameraCatalogItem(camera_id="cam-dup-01", name="Duplicate Instance", location="Loc B"),
    ]

    service = CameraCatalogSyncService(db=db_session)
    result = service.sync(raw_items=items)

    assert result.success is True
    assert result.stats.total_received == 2
    assert result.stats.created == 1
    assert result.stats.skipped == 1

    # Exactly one record in the database
    cams = db_session.query(Camera).filter(Camera.camera_code.ilike("CAM-DUP-01")).all()
    assert len(cams) == 1
    assert cams[0].name == "First Instance"


# --- 4. Create / Upsert Behavior ---
def test_4_create_upsert_behavior(db_session):
    items = [
        CameraCatalogItem(
            camera_id="CAM-NEW-01",
            name="New Node 01",
            location="Ahmedabad Ring Road",
            latitude=23.0225,
            longitude=72.5714,
            department="Gujarat Police",
            rtsp_url="rtsp://103.250.160.189:8554/stream/cam01",
            webrtc_url="http://103.250.160.189:8889/stream/cam01/whep",
        )
    ]

    service = CameraCatalogSyncService(db=db_session)
    result = service.sync(raw_items=items)

    assert result.success is True
    assert result.stats.created == 1
    assert result.stats.with_coordinates == 1
    assert result.stats.with_rtsp == 1
    assert result.stats.with_whep == 1

    cam = db_session.query(Camera).filter(Camera.camera_code == "CAM-NEW-01").first()
    assert cam is not None
    assert cam.name == "New Node 01"
    assert cam.location == "Ahmedabad Ring Road"
    assert cam.latitude == 23.0225
    assert cam.longitude == 72.5714
    assert cam.status == "registered"  # Honest registered state
    assert cam.vendor == "Gujarat Police"


# --- 5. Update Behavior ---
def test_5_update_behavior(db_session):
    # Pre-existing camera
    existing_cam = Camera(
        camera_code="CAM-UPD-01",
        name="Old Name",
        location="Old Location",
        latitude=21.0,
        longitude=72.0,
        status="registered",
    )
    db_session.add(existing_cam)
    db_session.commit()
    initial_id = existing_cam.id

    # Updated catalogue payload
    items = [
        CameraCatalogItem(
            camera_id="CAM-UPD-01",
            name="New Updated Name",
            location="New Updated Location",
            latitude=21.1764,
            longitude=72.8223,
        )
    ]

    service = CameraCatalogSyncService(db=db_session)
    result = service.sync(raw_items=items)

    assert result.success is True
    assert result.stats.created == 0
    assert result.stats.updated == 1
    assert result.stats.unchanged == 0

    # Primary key must remain strictly preserved
    db_session.refresh(existing_cam)
    assert existing_cam.id == initial_id
    assert existing_cam.name == "New Updated Name"
    assert existing_cam.location == "New Updated Location"
    assert existing_cam.latitude == 21.1764
    assert existing_cam.longitude == 72.8223


# --- 6. Unchanged Camera Behavior ---
def test_6_unchanged_camera_behavior(db_session):
    cam = Camera(
        camera_code="CAM-UNCHANGED",
        name="Fixed Node",
        location="Fixed Location",
        latitude=23.0,
        longitude=72.5,
        status="registered",
    )
    db_session.add(cam)
    db_session.commit()

    # Exact same information
    items = [
        CameraCatalogItem(
            camera_id="CAM-UNCHANGED",
            name="Fixed Node",
            location="Fixed Location",
            latitude=23.0,
            longitude=72.5,
        )
    ]

    service = CameraCatalogSyncService(db=db_session)
    result = service.sync(raw_items=items)

    assert result.success is True
    assert result.stats.created == 0
    assert result.stats.updated == 0
    assert result.stats.unchanged == 1


# --- 7. Missing Coordinates Handled Safely ---
def test_7_missing_coordinates(db_session):
    # A. New camera without coordinates
    items_no_coords = [
        CameraCatalogItem(
            camera_id="CAM-NO-GPS",
            name="No GPS Camera",
            location="Underground Tunnel",
            latitude=None,
            longitude=None,
        )
    ]

    service = CameraCatalogSyncService(db=db_session)
    result = service.sync(raw_items=items_no_coords)
    assert result.success is True

    cam1 = db_session.query(Camera).filter(Camera.camera_code == "CAM-NO-GPS").first()
    assert cam1.latitude is None
    assert cam1.longitude is None  # Never fabricated

    # B. Existing camera has coordinates; incoming update omits coordinates -> MUST PRESERVE
    cam_with_coords = Camera(
        camera_code="CAM-KEEP-GPS",
        name="Has GPS",
        latitude=22.3,
        longitude=73.2,
        status="registered",
    )
    db_session.add(cam_with_coords)
    db_session.commit()

    items_omit_coords = [
        CameraCatalogItem(
            camera_id="CAM-KEEP-GPS",
            name="Has GPS Renamed",
            latitude=None,
            longitude=None,
        )
    ]
    result2 = service.sync(raw_items=items_omit_coords)
    assert result2.success is True

    db_session.refresh(cam_with_coords)
    assert cam_with_coords.name == "Has GPS Renamed"
    assert cam_with_coords.latitude == 22.3  # Preserved
    assert cam_with_coords.longitude == 73.2  # Preserved


# --- 8. Authentication Failure Handling ---
def test_8_authentication_failure(db_session):
    service = CameraCatalogSyncService(db=db_session)

    # Simulate upstream catalogue redirect to login page
    with patch("streaming.camera_catalog.CameraCatalog.fetch") as mock_fetch:
        mock_fetch.return_value = CatalogFetchResult(
            success=False,
            status_code=200,
            requires_auth=True,
            redirect_url="https://cctv.corp8.cloud/auth/login",
            error_message="Authentication required (redirected to login page)",
        )

        initial_count = db_session.query(Camera).count()
        result = service.sync()

        assert result.success is False
        assert result.requires_auth is True
        assert result.redirect_url == "https://cctv.corp8.cloud/auth/login"
        assert "Authentication required" in result.message

        # Database must not be corrupted or modified
        assert db_session.query(Camera).count() == initial_count


# --- 9. Network Timeout Handling ---
def test_9_network_timeout(db_session):
    service = CameraCatalogSyncService(db=db_session)

    with patch("streaming.camera_catalog.CameraCatalog.fetch") as mock_fetch:
        mock_fetch.return_value = CatalogFetchResult(
            success=False,
            error_message="Network timeout: timed out",
        )

        initial_count = db_session.query(Camera).count()
        result = service.sync()

        assert result.success is False
        assert result.requires_auth is False
        assert "Network timeout" in result.message

        # Zero DB changes
        assert db_session.query(Camera).count() == initial_count


# --- 10. Credential Isolation ---
def test_10_credential_isolation(db_session):
    raw_rtsp = "rtsp://admin:SecretPassword123@103.250.160.189:8554/stream/cam10"
    sanitized = sanitize_stream_url(raw_rtsp)
    assert sanitized == "rtsp://103.250.160.189:8554/stream/cam10"
    assert "SecretPassword123" not in sanitized
    assert "admin" not in sanitized

    items = [
        CameraCatalogItem(
            camera_id="CAM-SEC-01",
            name="Secured Node",
            rtsp_url=raw_rtsp,
        )
    ]

    service = CameraCatalogSyncService(db=db_session)
    result = service.sync(raw_items=items)
    assert result.success is True

    cam = db_session.query(Camera).filter(Camera.camera_code == "CAM-SEC-01").first()
    assert cam.stream_url == "rtsp://103.250.160.189:8554/stream/cam10"
    assert "SecretPassword123" not in cam.stream_url


# --- 11. Camera/Stream Identifier Mapping & Endpoint Integration ---
def test_11_camera_stream_identifier_mapping_and_route(client, db_session):
    # Test POST /api/cameras/sync-catalogue route
    with patch("streaming.camera_catalog.CameraCatalog.fetch") as mock_fetch:
        mock_fetch.return_value = CatalogFetchResult(
            success=True,
            status_code=200,
            cameras=[
                CameraCatalogItem(
                    camera_id="CAM-ROUTE-01",
                    name="Endpoint Synced Camera",
                    location="GIDC Industrial Zone",
                    latitude=22.9644,
                    longitude=72.6321,
                    rtsp_url="rtsp://103.250.160.189:8554/stream/15",
                    webrtc_url="http://103.250.160.189:8889/stream/15/whep",
                )
            ]
        )

        response = client.post("/api/cameras/sync-catalogue")
        assert response.status_code == 200
        data = response.json()

        assert data["success"] is True
        assert data["stats"]["created"] == 1
        assert data["stats"]["total_received"] == 1

        # Check that camera is in /api/cameras
        list_resp = client.get("/api/cameras")
        assert list_resp.status_code == 200
        codes = [c["camera_code"] for c in list_resp.json()["cameras"]]
        assert "CAM-ROUTE-01" in codes

        # Check that camera appears on the GIS map route
        map_resp = client.get("/api/cameras/map?only_mapped=true")
        assert map_resp.status_code == 200
        map_codes = [m["camera_code"] for m in map_resp.json()]
        assert "CAM-ROUTE-01" in map_codes
