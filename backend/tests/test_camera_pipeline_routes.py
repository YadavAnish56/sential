import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.camera import Camera
from app.database.connection import Base
from app.database.dependencies import get_db
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

# Use a mock CameraPipelineManager for tests
class MockPipelineSession:
    def __init__(self, camera_id):
        self.camera_id = camera_id
        self._is_running = False

    @property
    def is_running(self):
        return self._is_running

    def start(self):
        if self._is_running:
            return False
        self._is_running = True
        return True

    def stop(self, timeout_sec=5.0):
        if not self._is_running:
            return True
        self._is_running = False
        return True
        
    def get_health(self):
        return {
            "camera_id": self.camera_id,
            "status": "running" if self._is_running else "stopped",
            "is_running": self._is_running
        }

class MockCameraPipelineManager:
    def __init__(self):
        self._sessions = {}
        self.stop_all_called = False

    def register_camera(self, config, auto_start=False, **kwargs):
        if config.camera_id not in self._sessions:
            self._sessions[config.camera_id] = MockPipelineSession(config.camera_id)
        if auto_start:
            self._sessions[config.camera_id].start()
        return self._sessions[config.camera_id]

    def start_camera(self, camera_id: str):
        if camera_id not in self._sessions:
            return False
        return self._sessions[camera_id].start()

    def stop_camera(self, camera_id: str, timeout_sec=5.0):
        if camera_id not in self._sessions:
            return False
        return self._sessions[camera_id].stop(timeout_sec)
        
    def stop_all(self, timeout_sec=5.0):
        self.stop_all_called = True
        for session in self._sessions.values():
            session.stop(timeout_sec)
        return {cam_id: True for cam_id in self._sessions}

    def get_session(self, camera_id: str):
        return self._sessions.get(camera_id)

    def get_health(self, camera_id: str):
        session = self._sessions.get(camera_id)
        return session.get_health() if session else None

    def get_all_health(self):
        return {cam_id: s.get_health() for cam_id, s in self._sessions.items()}


@pytest.fixture
def mock_manager():
    return MockCameraPipelineManager()


@pytest.fixture
def test_app(mock_manager, db_session):
    # Override database dependency
    def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    
    # Patch CameraPipelineManager in app.main so lifespan creates the mock
    with patch("app.main.CameraPipelineManager", return_value=mock_manager):
        yield app
    
    app.dependency_overrides.clear()


from sqlalchemy.pool import StaticPool

@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
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
def client(test_app):
    with TestClient(test_app) as c:
        yield c


@pytest.fixture
def db_camera(db_session):
    cam = Camera(
        camera_code="CAM-TEST",
        name="Test Camera",
        location="Gate 1",
        stream_url="rtsp://mock",
        status="active"
    )
    db_session.add(cam)
    db_session.commit()
    db_session.refresh(cam)
    return cam

@pytest.fixture
def db_camera_no_url(db_session):
    cam = Camera(
        camera_code="CAM-NO-URL",
        name="Test Camera No URL",
        location="Gate 2",
        status="active"
    )
    db_session.add(cam)
    db_session.commit()
    db_session.refresh(cam)
    return cam

# T1 manager created during application lifespan
def test_t1_manager_lifespan_startup():
    # Remove state to test lifespan
    if hasattr(app.state, "pipeline_manager"):
        del app.state.pipeline_manager
    with TestClient(app) as client:
        assert getattr(app.state, "pipeline_manager", None) is not None

# T12 shutdown invokes the manager's actual stop-all lifecycle method
def test_t12_manager_lifespan_shutdown(mock_manager, test_app):
    with TestClient(test_app):
        # Just enter and exit to trigger lifespan
        pass
    assert mock_manager.stop_all_called

# T2 startup does not automatically start cameras
def test_t2_startup_does_not_auto_start_cameras(mock_manager, client):
    assert len(mock_manager._sessions) == 0

# T3 valid camera start
def test_t3_valid_camera_start(client, mock_manager, db_camera):
    response = client.post(f"/api/cameras/{db_camera.id}/start")
    assert response.status_code == 200
    assert response.json()["message"] == f"Camera {db_camera.camera_code} pipeline started."
    session = mock_manager.get_session(db_camera.camera_code)
    assert session is not None
    assert session.is_running

# T4 unknown camera returns 404
def test_t4_unknown_camera_returns_404(client):
    response = client.post("/api/cameras/9999/start")
    assert response.status_code == 404

# T5 missing RTSP URL returns 400
def test_t5_missing_rtsp_url_returns_400(client, db_camera_no_url):
    response = client.post(f"/api/cameras/{db_camera_no_url.id}/start")
    assert response.status_code == 400
    assert "no stream_url" in response.json()["detail"]

# T6 duplicate start handled safely
def test_t6_duplicate_start_handled_safely(client, mock_manager, db_camera):
    # First start
    client.post(f"/api/cameras/{db_camera.id}/start")
    session = mock_manager.get_session(db_camera.camera_code)
    assert session.is_running
    
    # Second start
    response = client.post(f"/api/cameras/{db_camera.id}/start")
    assert response.status_code == 200
    assert "already running" in response.json()["message"]

# T7 valid camera stop
def test_t7_valid_camera_stop(client, mock_manager, db_camera):
    # Start first
    client.post(f"/api/cameras/{db_camera.id}/start")
    assert mock_manager.get_session(db_camera.camera_code).is_running
    
    # Then stop
    response = client.post(f"/api/cameras/{db_camera.id}/stop")
    assert response.status_code == 200
    assert not mock_manager.get_session(db_camera.camera_code).is_running

# T8 already-stopped camera handled safely
def test_t8_already_stopped_camera_handled_safely(client, mock_manager, db_camera):
    # Try stop without start
    response = client.post(f"/api/cameras/{db_camera.id}/stop")
    assert response.status_code == 200
    assert "not running or unknown" in response.json()["message"]

# T9 individual pipeline status
def test_t9_individual_pipeline_status(client, db_camera):
    client.post(f"/api/cameras/{db_camera.id}/start")
    response = client.get(f"/api/cameras/{db_camera.id}/pipeline-status")
    assert response.status_code == 200
    data = response.json()
    assert data["camera_id"] == db_camera.camera_code
    assert data["is_running"] is True

# T10 all pipeline status
def test_t10_all_pipeline_status(client, db_camera):
    client.post(f"/api/cameras/{db_camera.id}/start")
    response = client.get("/api/cameras/pipeline-status")
    assert response.status_code == 200
    data = response.json()
    assert db_camera.camera_code in data
    assert data[db_camera.camera_code]["is_running"] is True

# T11 pipeline error represented safely
def test_t11_pipeline_error_represented_safely(client, mock_manager, db_camera):
    # If a pipeline fails to start for internal reasons
    class FailingMockSession(MockPipelineSession):
        def start(self):
            return False

    mock_manager._sessions[db_camera.camera_code] = FailingMockSession(db_camera.camera_code)
    
    response = client.post(f"/api/cameras/{db_camera.id}/start")
    assert response.status_code == 500
    assert "Failed to start pipeline" in response.json()["detail"]

# T13 existing camera CRUD remains functional
def test_t13_existing_camera_crud_functional(client):
    response = client.post("/api/cameras", json={
        "camera_code": "CAM-CRUD",
        "name": "CRUD Test",
        "location": "CRUD Location",
        "stream_url": "rtsp://crud"
    })
    assert response.status_code == 201
    
    crud_id = response.json()["id"]
    get_resp = client.get(f"/api/cameras/{crud_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["camera_code"] == "CAM-CRUD"

# T14 RTSP credentials are never returned
def test_t14_rtsp_credentials_never_returned(client, db_camera):
    client.post(f"/api/cameras/{db_camera.id}/start")
    response = client.get(f"/api/cameras/{db_camera.id}/pipeline-status")
    data = response.json()
    # Check that stream_url or rtsp_url is not in the health payload
    assert "rtsp_url" not in str(data).lower()
    assert "stream_url" not in str(data).lower()
from streaming.camera_catalog import CatalogFetchResult, CameraCatalogItem

def test_preview_camera_not_found(client, db_session):
    response = client.get("/api/cameras/999/preview")
    assert response.status_code == 404

def test_preview_catalog_success(client, db_session):
    camera = Camera(camera_code="CAM-PRV-1", name="Preview1", stream_url="rtsp://test1")
    db_session.add(camera)
    db_session.commit()

    with patch("streaming.camera_catalog.CameraCatalog.fetch") as mock_fetch:
        mock_fetch.return_value = CatalogFetchResult(
            success=True,
            cameras=[
                CameraCatalogItem(
                    camera_id="CAM-PRV-1",
                    webrtc_url="https://live/webrtc",
                    hls_url="https://live/hls",
                    rtsp_url="rtsp://secret",
                    raw_data={"secret": "token"}
                )
            ]
        )
        response = client.get(f"/api/cameras/{camera.id}/preview")
        assert response.status_code == 200
        data = response.json()
        assert data["camera_code"] == "CAM-PRV-1"
        assert data["webrtc_url"] == "https://live/webrtc"
        assert data["hls_url"] == "https://live/hls"
        assert "rtsp_url" not in data
        assert "raw_data" not in data

def test_preview_webrtc_preferred(client, db_session):
    camera = Camera(camera_code="CAM-PRV-2", name="Preview2", stream_url="rtsp://test2")
    db_session.add(camera)
    db_session.commit()

    with patch("streaming.camera_catalog.CameraCatalog.fetch") as mock_fetch:
        mock_fetch.return_value = CatalogFetchResult(
            success=True,
            cameras=[
                CameraCatalogItem(
                    camera_id="CAM-PRV-2",
                    webrtc_url="https://live/webrtc2",
                    hls_url=None
                )
            ]
        )
        response = client.get(f"/api/cameras/{camera.id}/preview")
        assert response.status_code == 200
        assert response.json()["webrtc_url"] == "https://live/webrtc2"
        assert response.json()["hls_url"] is None

def test_preview_hls_fallback(client, db_session):
    camera = Camera(camera_code="CAM-PRV-3", name="Preview3", stream_url="rtsp://test3")
    db_session.add(camera)
    db_session.commit()

    with patch("streaming.camera_catalog.CameraCatalog.fetch") as mock_fetch:
        mock_fetch.return_value = CatalogFetchResult(
            success=True,
            cameras=[
                CameraCatalogItem(
                    camera_id="CAM-PRV-3",
                    webrtc_url=None,
                    hls_url="https://live/hls3"
                )
            ]
        )
        response = client.get(f"/api/cameras/{camera.id}/preview")
        assert response.status_code == 200
        assert response.json()["webrtc_url"] is None
        assert response.json()["hls_url"] == "https://live/hls3"

def test_preview_neither_safe_url(client, db_session):
    camera = Camera(camera_code="CAM-PRV-4", name="Preview4", stream_url="rtsp://test4")
    db_session.add(camera)
    db_session.commit()

    with patch("streaming.camera_catalog.CameraCatalog.fetch") as mock_fetch:
        mock_fetch.return_value = CatalogFetchResult(
            success=True,
            cameras=[
                CameraCatalogItem(
                    camera_id="CAM-PRV-4",
                    webrtc_url=None,
                    hls_url=None,
                    rtsp_url="rtsp://secret"
                )
            ]
        )
        response = client.get(f"/api/cameras/{camera.id}/preview")
        assert response.status_code == 404

def test_preview_catalog_failure(client, db_session):
    camera = Camera(camera_code="CAM-PRV-5", name="Preview5", stream_url="rtsp://test5")
    db_session.add(camera)
    db_session.commit()

    with patch("streaming.camera_catalog.CameraCatalog.fetch") as mock_fetch:
        mock_fetch.return_value = CatalogFetchResult(
            success=False,
            error_message="Connection timeout"
        )
        response = client.get(f"/api/cameras/{camera.id}/preview")
        assert response.status_code == 503
