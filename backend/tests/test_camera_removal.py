"""
Removing a camera must never destroy evidence.

A camera with recorded sightings is retired rather than deleted, so the events
and alerts that point at it still resolve to a real camera. A retired camera is
no longer part of the live estate, so it disappears from the map and the
default listing while remaining reachable when explicitly asked for.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.database.connection import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def cameras(db_session):
    spare = Camera(camera_code="CAM-SPARE", name="Unused Post", latitude=21.0, longitude=72.0, status="offline")
    active = Camera(camera_code="CAM-001", name="Surat Highway", latitude=21.1702, longitude=72.8311, status="online")
    db_session.add_all([spare, active])
    db_session.commit()
    db_session.refresh(spare)
    db_session.refresh(active)
    return {"spare": spare, "active": active}


@pytest.fixture
def sighting(db_session, cameras):
    vehicle = Vehicle(plate_number="GJ05AB1234")
    db_session.add(vehicle)
    db_session.flush()
    db_session.add(Event(
        camera_id=cameras["active"].id, vehicle_id=vehicle.id,
        event_type="anpr_detection", object_type="vehicle",
        confidence=0.93, timestamp=datetime(2026, 9, 10, 9, 0, 0),
    ))
    db_session.add(Alert(
        camera_id=cameras["active"].id, vehicle_id=vehicle.id,
        alert_type="ANPR_WATCHLIST", severity="high",
        message="match", timestamp=datetime(2026, 9, 10, 9, 0, 0),
    ))
    db_session.commit()
    return vehicle


class TestCameraRemoval:
    def test_a_camera_with_no_sightings_is_deleted_outright(self, client, db_session, cameras):
        response = client.delete("/api/cameras/CAM-SPARE")

        assert response.status_code == 200
        body = response.json()
        assert body["deleted"] is True and body["retired"] is False
        assert db_session.query(Camera).filter(Camera.camera_code == "CAM-SPARE").first() is None

    def test_removing_a_camera_with_evidence_is_refused(self, client, cameras, sighting):
        response = client.delete("/api/cameras/CAM-001")

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["events"] == 1
        assert detail["alerts"] == 1
        assert "force" in detail["hint"]

    def test_a_refusal_removes_nothing(self, client, db_session, cameras, sighting):
        client.delete("/api/cameras/CAM-001")

        assert db_session.query(Camera).filter(Camera.camera_code == "CAM-001").first() is not None
        assert db_session.query(Event).count() == 1

    def test_forcing_retires_the_camera_and_keeps_its_evidence(self, client, db_session, cameras, sighting):
        response = client.delete("/api/cameras/CAM-001?force=true")

        assert response.status_code == 200
        body = response.json()
        assert body["retired"] is True and body["deleted"] is False

        camera = db_session.query(Camera).filter(Camera.camera_code == "CAM-001").one()
        assert camera.status == "retired"
        assert db_session.query(Event).count() == 1
        assert db_session.query(Alert).count() == 1

    def test_a_retired_camera_leaves_the_map(self, client, cameras, sighting):
        client.delete("/api/cameras/CAM-001?force=true")

        codes = [c["camera_code"] for c in client.get("/api/cameras/map?only_mapped=true").json()]
        assert "CAM-001" not in codes

    def test_a_retired_camera_leaves_the_default_listing(self, client, cameras, sighting):
        client.delete("/api/cameras/CAM-001?force=true")

        body = client.get("/api/cameras").json()
        assert "CAM-001" not in [c["camera_code"] for c in body["cameras"]]
        # `total` must agree with the rows returned, or paging misreports.
        assert body["total"] == len(body["cameras"])

    def test_a_retired_camera_is_still_reachable_when_asked_for(self, client, cameras, sighting):
        client.delete("/api/cameras/CAM-001?force=true")

        body = client.get("/api/cameras?include_retired=true").json()
        retired = [c for c in body["cameras"] if c["camera_code"] == "CAM-001"]
        assert retired and retired[0]["status"] == "retired"

    def test_evidence_still_resolves_to_the_retired_camera(self, client, cameras, sighting):
        """The point of retiring: a sighting must not become an orphan."""
        client.delete("/api/cameras/CAM-001?force=true")

        timeline = client.get("/api/vehicles/GJ05AB1234/timeline").json()["timeline"]
        assert len(timeline) == 1
        assert timeline[0]["camera_code"] == "CAM-001"
        assert timeline[0]["latitude"] == pytest.approx(21.1702)

    def test_removing_an_unknown_camera_reports_not_found(self, client, cameras):
        assert client.delete("/api/cameras/CAM-999").status_code == 404

    def test_a_camera_can_be_removed_by_database_id(self, client, db_session, cameras):
        response = client.delete(f"/api/cameras/{cameras['spare'].id}")

        assert response.status_code == 200
        assert db_session.query(Camera).filter(Camera.camera_code == "CAM-SPARE").first() is None
