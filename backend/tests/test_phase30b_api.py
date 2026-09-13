"""
Phase 30B Focused API Tests — Plate Visibility Enrichment in EventResponse and AlertResponse.
"""

import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.camera import Camera
from app.models.vehicle import Vehicle
from app.models.event import Event
from app.models.alert import Alert


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_events_api_plate_number_enrichment(client, db_session):
    """Verify GET /events returns plate_number joined from Vehicle."""
    camera = Camera(camera_code="CAM-TEST-01", name="Test Cam", status="online")
    vehicle = Vehicle(plate_number="KA02MM9091")
    db_session.add_all([camera, vehicle])
    db_session.commit()

    event1 = Event(
        camera_id=camera.id,
        vehicle_id=vehicle.id,
        event_type="anpr_detection",
        confidence=0.88,
        timestamp=datetime.utcnow(),
    )
    event2 = Event(
        camera_id=camera.id,
        vehicle_id=None,
        event_type="general_motion",
        confidence=0.50,
        timestamp=datetime.utcnow(),
    )
    db_session.add_all([event1, event2])
    db_session.commit()

    # List events
    res = client.get("/api/events")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    events = data["events"]
    
    # Event with vehicle has plate_number populated
    e1_res = next(e for e in events if e["id"] == event1.id)
    assert e1_res["plate_number"] == "KA02MM9091"

    # Event without vehicle has None
    e2_res = next(e for e in events if e["id"] == event2.id)
    assert e2_res["plate_number"] is None

    # Single event lookup
    res_single = client.get(f"/api/events/{event1.id}")
    assert res_single.status_code == 200
    assert res_single.json()["plate_number"] == "KA02MM9091"


def test_alerts_api_plate_number_enrichment(client, db_session):
    """Verify GET /alerts returns plate_number joined from Vehicle."""
    camera = Camera(camera_code="CAM-TEST-02", name="Test Cam 2", status="online")
    vehicle = Vehicle(plate_number="GJ05AB1234")
    db_session.add_all([camera, vehicle])
    db_session.commit()

    alert1 = Alert(
        camera_id=camera.id,
        vehicle_id=vehicle.id,
        alert_type="ANPR_WATCHLIST",
        severity="high",
        message="Watchlist vehicle detected",
        timestamp=datetime.utcnow(),
        status="new",
    )
    alert2 = Alert(
        camera_id=camera.id,
        vehicle_id=None,
        alert_type="PERIMETER_BREACH",
        severity="medium",
        message="Perimeter breach detected",
        timestamp=datetime.utcnow(),
        status="new",
    )
    db_session.add_all([alert1, alert2])
    db_session.commit()

    # List alerts
    res = client.get("/api/alerts")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    alerts = data["alerts"]

    # Alert with vehicle has plate_number populated
    a1_res = next(a for a in alerts if a["id"] == alert1.id)
    assert a1_res["plate_number"] == "GJ05AB1234"

    # Alert without vehicle has None
    a2_res = next(a for a in alerts if a["id"] == alert2.id)
    assert a2_res["plate_number"] is None

    # Single alert lookup
    res_single = client.get(f"/api/alerts/{alert1.id}")
    assert res_single.status_code == 200
    assert res_single.json()["plate_number"] == "GJ05AB1234"
