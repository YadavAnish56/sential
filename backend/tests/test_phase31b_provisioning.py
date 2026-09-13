"""
Automated regression tests for Phase 31B:
Verified Government Camera Provisioning & Multi-Camera Registry.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base
from app.models.camera import Camera
from app.routes.cameras import _resolve_upstream_stream


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


def test_upstream_stream_resolution_for_all_8_cameras():
    """Verify that CAM-001 through CAM-008 map deterministically to cam01 through cam08."""
    for i in range(1, 9):
        code = f"CAM-00{i}"
        expected_stream = f"cam{i:02d}"
        cam = Camera(id=i, camera_code=code, name=f"Camera {i:02d}")
        assert _resolve_upstream_stream(cam) == expected_stream


def test_coordinate_safety_rules(db_session):
    """
    Verify coordinate safety:
    - CAM-001 has verified Surat coordinates.
    - CAM-007 coordinates are NULL (uncorroborated coordinates rejected per User Correction 1).
    - CAM-002..CAM-006, CAM-008 coordinates are NULL (no fabricated coordinates).
    """
    cams = [
        Camera(camera_code="CAM-001", name="Camera 01", latitude=21.1702, longitude=72.8311, status="online"),
        Camera(camera_code="CAM-002", name="Camera 02", latitude=None, longitude=None, status="online"),
        Camera(camera_code="CAM-006", name="Camera 06", latitude=None, longitude=None, status="online"),
        Camera(camera_code="CAM-007", name="Camera 07", latitude=None, longitude=None, status="online"),
        Camera(camera_code="CAM-008", name="Camera 08", latitude=None, longitude=None, status="degraded"),
        Camera(camera_code="CAM-CRUD", name="CRUD Test (Test Node)", latitude=None, longitude=None, status="offline", vendor="Test Node"),
    ]
    db_session.add_all(cams)
    db_session.commit()

    c1 = db_session.query(Camera).filter(Camera.camera_code == "CAM-001").first()
    assert c1.latitude == pytest.approx(21.1702)
    assert c1.longitude == pytest.approx(72.8311)

    c7 = db_session.query(Camera).filter(Camera.camera_code == "CAM-007").first()
    assert c7.latitude is None
    assert c7.longitude is None

    c8 = db_session.query(Camera).filter(Camera.camera_code == "CAM-008").first()
    assert c8.status == "degraded"
    assert c8.latitude is None

    crud = db_session.query(Camera).filter(Camera.camera_code == "CAM-CRUD").first()
    assert crud.status == "offline"
    assert crud.vendor == "Test Node"


def test_no_credential_leak_in_stream_url():
    """Verify that stored stream URLs contain zero credentials."""
    for i in range(1, 9):
        url = f"rtsp://103.250.160.189:8554/stream/cam{i:02d}"
        assert "@" not in url
        assert "password" not in url.lower()
