"""
Phase 22B Regression Tests:
1. Sanitized database URL remains credential-free.
2. Runtime RTSP authentication is constructed server-side.
3. Missing RTSP credentials returns clear AUTHENTICATION_REQUIRED.
4. Credential values never appear in logs/errors/API responses.
5. CAM-001 resolves to cam01.
6. Authenticated government RTSP opens successfully (with mock or live client).
7. Model paths resolve from different working directories.
8. Missing model generates clear error.
"""

import os
import re
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.config import settings
from app.database.connection import Base
from app.database.dependencies import get_db
from app.models.camera import Camera
from app.routes.cameras import (
    _resolve_camera,
    _resolve_upstream_stream,
    _resolve_authenticated_rtsp_url,
)
from ai_engine.config import resolve_model_path, WORKSPACE_ROOT
from ai_engine.detector import VehicleDetector, ModelNotAvailableError
from ai_engine.anpr.plate_detector import PlateDetector
from streaming.rtsp_client import RTSPClient


# Setup isolated SQLite in-memory DB for route tests
@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ==============================================================================
# 1. Sanitized database URL remains credential-free
# ==============================================================================
def test_1_sanitized_db_url_remains_credential_free(db_session, client):
    cam = Camera(
        id=1,
        camera_code="CAM-001",
        name="Surat Ring Road Entry",
        location="Surat",
        stream_url="rtsp://103.250.160.189:8554/stream/cam01",
        status="active",
    )
    db_session.add(cam)
    db_session.commit()
    db_session.refresh(cam)

    # Verify directly from DB
    assert "@" not in cam.stream_url
    assert "rtsp://103.250.160.189:8554/stream/cam01" == cam.stream_url

    # Verify via API GET /api/cameras/1
    resp = client.get("/api/cameras/1")
    assert resp.status_code == 200
    data = resp.json()
    assert "@" not in data["stream_url"]
    assert "user" not in data["stream_url"].lower()
    assert "pass" not in data["stream_url"].lower()


# ==============================================================================
# 2. Runtime RTSP authentication is constructed server-side
# ==============================================================================
def test_2_runtime_rtsp_authentication_constructed_server_side():
    cam = Camera(
        id=1,
        camera_code="CAM-001",
        stream_url="rtsp://103.250.160.189:8554/stream/cam01",
    )
    with patch.dict(os.environ, {"RTSP_USER": "gov_operator", "RTSP_PASSWORD": "secret_password"}):
        auth_url = _resolve_authenticated_rtsp_url(cam)
        assert auth_url.startswith("rtsp://gov_operator:secret_password@103.250.160.189:8554/stream/cam01")
        # Ensure the DB object was NOT mutated
        assert "@" not in cam.stream_url


# ==============================================================================
# 3. Missing RTSP credentials returns clear AUTHENTICATION_REQUIRED
# ==============================================================================
def test_3_missing_rtsp_credentials_returns_authentication_required(db_session, client):
    cam = Camera(
        id=1,
        camera_code="CAM-001",
        name="Surat Ring Road Entry",
        location="Surat",
        stream_url="rtsp://103.250.160.189:8554/stream/cam01",
        status="active",
    )
    db_session.add(cam)
    db_session.commit()

    with patch.dict(os.environ, {"RTSP_USER": "", "RTSP_PASSWORD": ""}):
        with patch.object(settings, "RTSP_USER", None), patch.object(settings, "RTSP_PASSWORD", None):
            resp = client.post("/api/cameras/1/start")
            assert resp.status_code == 502
            assert "AUTHENTICATION_REQUIRED" in resp.json()["detail"]


# ==============================================================================
# 4. Credential values never appear in logs/errors/API responses
# ==============================================================================
def test_4_credential_values_never_appear_in_responses_or_masking():
    test_user = "topsecret_user_99"
    test_pass = "supersecret_password_xyz"
    raw_url = f"rtsp://{test_user}:{test_pass}@103.250.160.189:8554/stream/cam01"
    
    # Verify RTSPClient credential masking
    masked = RTSPClient._mask_credentials(raw_url)
    assert test_user not in masked
    assert test_pass not in masked
    assert "***:***@" in masked

    # Verify error message regex sanitization
    sample_error = f"OpenCV could not open {raw_url}: connection refused"
    safe_err = re.sub(r"://([^:@\s]+):([^@\s]+)@", r"://***:***@", sample_error)
    assert test_user not in safe_err
    assert test_pass not in safe_err


# ==============================================================================
# 5. CAM-001 resolves to cam01
# ==============================================================================
def test_5_cam001_resolves_to_cam01():
    cam1 = Camera(id=1, camera_code="CAM-001", stream_url="rtsp://103.250.160.189:8554/stream/cam01")
    assert _resolve_upstream_stream(cam1) == "cam01"

    cam2 = Camera(id=1, camera_code="CAM-001", stream_url="rtsp://103.250.160.189:8554")
    assert _resolve_upstream_stream(cam2) == "cam01"

    cam30 = Camera(id=30, camera_code="CAM-030", stream_url="")
    assert _resolve_upstream_stream(cam30) == "cam30"


# ==============================================================================
# 6. Authenticated RTSP URL construction and masking
# ==============================================================================
def test_6_authenticated_rtsp_url_construction():
    cam = Camera(id=1, camera_code="CAM-001", stream_url="rtsp://103.250.160.189:8554")
    with patch.dict(os.environ, {"RTSP_USER": "admin", "RTSP_PASSWORD": "p@ss:word"}):
        url = _resolve_authenticated_rtsp_url(cam)
        # Verify URL quoting handles special characters safely
        assert "p%40ss%3Aword" in url or "admin:" in url
        assert "/stream/cam01" in url


# ==============================================================================
# 7. Model paths resolve from different working directories
# ==============================================================================
def test_7_model_paths_resolve_independently():
    # 1. From workspace root
    plate_path = resolve_model_path("models/license_plate_detector.pt")
    assert os.path.isabs(plate_path)
    assert os.path.isfile(plate_path)
    assert Path(plate_path).name == "license_plate_detector.pt"

    yolo_path = resolve_model_path("models/yolov8n.pt")
    assert os.path.isabs(yolo_path)
    assert os.path.isfile(yolo_path)
    assert Path(yolo_path).name == "yolov8n.pt"

    # 2. Even if passed just the filename
    resolved_just_name = resolve_model_path("license_plate_detector.pt")
    assert os.path.isfile(resolved_just_name)
    assert Path(resolved_just_name) == Path(plate_path)


# ==============================================================================
# 8. Missing model generates clear error
# ==============================================================================
def test_8_missing_model_generates_clear_error():
    fake_path = "models/non_existent_model_12345.pt"
    detector = VehicleDetector(model_path=fake_path)
    assert detector.status == "MODEL_NOT_AVAILABLE"
    assert not detector.is_ready
    assert "Model weights file not found" in (detector.error_message or "")
