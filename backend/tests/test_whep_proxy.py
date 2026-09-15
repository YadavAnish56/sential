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


def test_whep_proxy_camera_not_found(client):
    response = client.post("/api/cameras/9999/whep", content="v=0\r\no=...")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_whep_proxy_empty_body(client, db_session):
    cam = Camera(camera_code="CAM-001", name="Cam 1", status="online")
    db_session.add(cam)
    db_session.commit()

    response = client.post(f"/api/cameras/{cam.id}/whep", content="")
    assert response.status_code == 400
    assert "required" in response.json()["detail"].lower()


@patch("urllib.request.urlopen")
def test_whep_proxy_success(mock_urlopen, client, db_session):
    cam = Camera(camera_code="cam01", name="Camera 1", stream_url="rtsp://internal/stream/cam01")
    db_session.add(cam)
    db_session.commit()

    mock_resp = MagicMock()
    mock_resp.status = 201
    mock_resp.read.return_value = b"v=0\r\no=mediamtx-answer\r\ns=-\r\n"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    response = client.post(
        f"/api/cameras/{cam.id}/whep",
        content="v=0\r\no=browser-offer\r\ns=-\r\n",
        headers={"Content-Type": "application/sdp"}
    )
    assert response.status_code == 201
    assert "application/sdp" in response.headers["Content-Type"]
    assert b"mediamtx-answer" in response.content


@patch("urllib.request.urlopen")
def test_whep_proxy_upstream_401_no_secrets_leaked(mock_urlopen, client, db_session):
    cam = Camera(camera_code="CAM-022", name="Cam 22")
    db_session.add(cam)
    db_session.commit()

    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="http://103.250.160.189:8889/stream/cam22/whep",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=None,
    )

    response = client.post(
        f"/api/cameras/{cam.id}/whep",
        content="v=0\r\no=offer\r\n",
        headers={"Content-Type": "application/sdp"}
    )
    assert response.status_code == 502
    data = response.json()
    assert "detail" in data
    # Ensure zero secrets or passwords in the response
    assert "password" not in str(data).lower()
    assert "authorization" not in str(data).lower()
