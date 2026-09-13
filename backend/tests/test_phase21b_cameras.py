import os
import pytest
from unittest.mock import patch, MagicMock
import urllib.error
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database.connection import Base
from app.database.dependencies import get_db
from app.models.camera import Camera
from app.routes.cameras import _resolve_upstream_stream, _get_gateway_base


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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ==============================================================================
# 1. Deterministic Stream Identifier Mapping Tests
# ==============================================================================

def test_stream_resolver_cam_code_variants():
    # Standard 3-digit prefix
    c1 = Camera(id=1, camera_code="CAM-001", name="Cam 1")
    assert _resolve_upstream_stream(c1) == "cam01"

    # Gujarat Highway naming convention
    c2 = Camera(id=2, camera_code="CAM-01-SG-HIGHWAY", name="SG Highway Cam")
    assert _resolve_upstream_stream(c2) == "cam01"

    # Direct cam01..cam30 format
    c3 = Camera(id=3, camera_code="cam03", name="Direct Cam 3")
    assert _resolve_upstream_stream(c3) == "cam03"

    c30 = Camera(id=30, camera_code="cam30", name="Direct Cam 30")
    assert _resolve_upstream_stream(c30) == "cam30"

    # Single digit CAM-7
    c7 = Camera(id=7, camera_code="CAM-7", name="Cam 7")
    assert _resolve_upstream_stream(c7) == "cam07"

    # Direct stream URL with /stream/cam05
    c_url = Camera(id=5, camera_code="CAM-CUSTOM", name="URL Cam", stream_url="rtsp://103.250.160.189:8554/stream/cam05")
    assert _resolve_upstream_stream(c_url) == "cam05"


def test_cam001_maps_to_cam01():
    """Explicit test: CAM-001 → cam01 mapping."""
    cam = Camera(id=1, camera_code="CAM-001", name="Government Camera 01")
    assert _resolve_upstream_stream(cam) == "cam01"


def test_all_30_camera_codes_resolve():
    """Verify all cam01..cam30 codes resolve deterministically."""
    for i in range(1, 31):
        # Direct camXX format
        cam_direct = Camera(id=i, camera_code=f"cam{i:02d}", name=f"Cam {i}")
        assert _resolve_upstream_stream(cam_direct) == f"cam{i:02d}"
        # CAM-0XX format
        cam_code = Camera(id=i, camera_code=f"CAM-{i:03d}", name=f"Cam {i}")
        assert _resolve_upstream_stream(cam_code) == f"cam{i:02d}"


# ==============================================================================
# 2. Government Gateway URL Configuration Tests
# ==============================================================================

def test_gateway_base_default_and_env():
    # Without env override, defaults to government gateway (not localhost)
    with patch.dict(os.environ, {}, clear=True):
        gateway = _get_gateway_base()
        assert "127.0.0.1" not in gateway
        assert "103.250.160.189" in gateway

    # With env override
    with patch.dict(os.environ, {"WHEP_GATEWAY_URL": "http://10.0.0.1:8889"}):
        gateway = _get_gateway_base()
        assert gateway == "http://10.0.0.1:8889"


def test_gateway_strips_trailing_slash():
    with patch.dict(os.environ, {"WHEP_GATEWAY_URL": "http://10.0.0.1:8889/"}):
        gateway = _get_gateway_base()
        assert not gateway.endswith("/")


# ==============================================================================
# 3. Secure WHEP Proxy Upstream Status Tests
# ==============================================================================

@patch("urllib.request.urlopen")
def test_whep_proxy_upstream_401_authentication_required(mock_urlopen, client, db_session):
    cam = Camera(id=1, camera_code="CAM-001", name="Cam 1")
    db_session.add(cam)
    db_session.commit()

    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="http://103.250.160.189:8889/stream/cam01/whep",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=None,
    )

    with patch.dict(os.environ, {"RTSP_USER": "testuser", "RTSP_PASSWORD": "testpass"}):
        response = client.post(
            f"/api/cameras/{cam.id}/whep",
            content="v=0\r\no=sdp-offer\r\n",
            headers={"Content-Type": "application/sdp"}
        )
    assert response.status_code == 502
    data = response.json()
    assert "AUTHENTICATION_REQUIRED" in data["detail"]
    # Verify zero credential leakage
    assert "password" not in str(data).lower()
    assert "secret" not in str(data).lower()


@patch("urllib.request.urlopen")
def test_whep_proxy_upstream_404_not_found(mock_urlopen, client, db_session):
    cam = Camera(id=99, camera_code="CAM-099", name="Cam 99")
    db_session.add(cam)
    db_session.commit()

    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="http://103.250.160.189:8889/stream/cam99/whep",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=None,
    )

    with patch.dict(os.environ, {"RTSP_USER": "testuser", "RTSP_PASSWORD": "testpass"}):
        response = client.post(
            f"/api/cameras/{cam.id}/whep",
            content="v=0\r\no=sdp-offer\r\n",
            headers={"Content-Type": "application/sdp"}
        )
    assert response.status_code == 502
    data = response.json()
    assert "UPSTREAM_NOT_FOUND" in data["detail"]
    assert "cam99" in data["detail"]


@patch("urllib.request.urlopen")
def test_whep_proxy_network_error(mock_urlopen, client, db_session):
    cam = Camera(id=1, camera_code="CAM-001", name="Cam 1")
    db_session.add(cam)
    db_session.commit()

    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

    with patch.dict(os.environ, {"RTSP_USER": "testuser", "RTSP_PASSWORD": "testpass"}):
        response = client.post(
            f"/api/cameras/{cam.id}/whep",
            content="v=0\r\no=sdp-offer\r\n",
            headers={"Content-Type": "application/sdp"}
        )
    assert response.status_code == 504
    data = response.json()
    assert "NETWORK_ERROR" in data["detail"]


# ==============================================================================
# 4. Credential Isolation — No credentials in API responses
# ==============================================================================

def test_credential_isolation_in_camera_list(client, db_session):
    """Camera list API never exposes RTSP credentials."""
    cam = Camera(
        id=1,
        camera_code="CAM-001",
        name="Test Camera",
        stream_url="rtsp://103.250.160.189:8554/stream/cam01",
    )
    db_session.add(cam)
    db_session.commit()

    response = client.get("/api/cameras")
    assert response.status_code == 200
    body_str = response.text
    # Verify credentials never leak even if RTSP_USER/PASSWORD env vars were set
    assert "RTSP_USER" not in body_str
    assert "RTSP_PASSWORD" not in body_str


def test_credential_isolation_in_map_markers(client, db_session):
    """Map marker API never exposes stream URLs or credentials."""
    cam = Camera(
        id=1,
        camera_code="CAM-001",
        name="Test Camera",
        latitude=23.0,
        longitude=72.5,
        stream_url="rtsp://user:password@103.250.160.189:8554/stream/cam01",
    )
    db_session.add(cam)
    db_session.commit()

    response = client.get("/api/cameras/map")
    assert response.status_code == 200
    body_str = response.text
    # Map markers should not include stream_url field at all
    assert "stream_url" not in body_str
    assert "password" not in body_str
    assert "user:" not in body_str


# ==============================================================================
# 5. 30-Camera Registration and Rendering Support
# ==============================================================================

def test_30_camera_registration(client, db_session):
    """Backend supports registering and listing 30 cameras."""
    for i in range(1, 31):
        cam = Camera(
            id=i,
            camera_code=f"CAM-{i:03d}",
            name=f"Junction Camera {i:02d}",
            stream_url=f"rtsp://103.250.160.189:8554/stream/cam{i:02d}",
            status="registered",
        )
        db_session.add(cam)
    db_session.commit()

    response = client.get("/api/cameras?limit=50")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 30
    assert len(data["cameras"]) == 30


# ==============================================================================
# 6. Camera Selection ID Mapping
# ==============================================================================

def test_camera_resolve_by_db_id(client, db_session):
    """Camera can be resolved by database integer ID."""
    cam = Camera(id=42, camera_code="CAM-042", name="Camera 42")
    db_session.add(cam)
    db_session.commit()

    response = client.get("/api/cameras/42")
    assert response.status_code == 200
    assert response.json()["camera_code"] == "CAM-042"


def test_camera_resolve_by_code(client, db_session):
    """Camera can be resolved by camera_code string."""
    cam = Camera(id=1, camera_code="CAM-001", name="Camera 01")
    db_session.add(cam)
    db_session.commit()

    response = client.get("/api/cameras/CAM-001")
    assert response.status_code == 200
    assert response.json()["id"] == 1


@patch("urllib.request.urlopen")
def test_whep_proxy_empty_body_rejected(mock_urlopen, client, db_session):
    """WHEP proxy rejects empty SDP offer body."""
    cam = Camera(id=1, camera_code="CAM-001", name="Camera 01")
    db_session.add(cam)
    db_session.commit()

    response = client.post(
        "/api/cameras/1/whep",
        content="",
        headers={"Content-Type": "application/sdp"},
    )
    assert response.status_code == 400
    assert "SDP offer body" in response.json()["detail"]


@patch("urllib.request.urlopen")
def test_whep_proxy_adds_auth_header(mock_urlopen, client, db_session):
    """WHEP proxy adds Basic Auth when RTSP_USER/RTSP_PASSWORD are set."""
    cam = Camera(id=1, camera_code="CAM-001", name="Camera 01")
    db_session.add(cam)
    db_session.commit()

    mock_resp = MagicMock()
    mock_resp.read.return_value = b"v=0\r\nanswer-sdp"
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_resp

    with patch.dict(os.environ, {"RTSP_USER": "testuser", "RTSP_PASSWORD": "testpass"}):
        response = client.post(
            "/api/cameras/1/whep",
            content="v=0\r\noffer-sdp",
            headers={"Content-Type": "application/sdp"},
        )

    assert response.status_code == 201
    # Verify urlopen was called with auth header
    call_args = mock_urlopen.call_args
    req_obj = call_args[0][0]
    assert "Authorization" in req_obj.headers
    assert req_obj.headers["Authorization"].startswith("Basic ")
    # Verify the response body doesn't contain credentials
    assert "testuser" not in response.text
    assert "testpass" not in response.text


def test_whep_proxy_missing_credentials_reports_auth_required(client, db_session):
    """WHEP proxy reports AUTHENTICATION_REQUIRED when credentials are missing server-side."""
    cam = Camera(id=1, camera_code="CAM-001", name="Camera 01")
    db_session.add(cam)
    db_session.commit()

    from app.core.config import settings
    with patch.dict(os.environ, {"RTSP_USER": "", "RTSP_PASSWORD": ""}):
        with patch.object(settings, "RTSP_USER", None), patch.object(settings, "RTSP_PASSWORD", None):
            response = client.post(
                "/api/cameras/1/whep",
                content="v=0\r\noffer-sdp",
                headers={"Content-Type": "application/sdp"},
            )
            assert response.status_code == 502
            data = response.json()
            assert "AUTHENTICATION_REQUIRED" in data["detail"]


