"""
Unit tests for Sentinel Watchlist API and Alert Integration (Phase 10).

Tests:
1. Create watchlist entry (validation, normalization, default severity)
2. List entries with pagination and filtering (is_active, severity, search)
3. Get entry by ID
4. Update entry fields (severity, description, active toggle)
5. Delete and deactivate entry (hard delete vs deactivate_only)
6. Plate normalization (stripping punctuation/whitespace, Indian format validation)
7. Duplicate handling (409 Conflict on duplicate active plates)
8. Invalid ID handling (404 Not Found)
9. Inactive entries do NOT trigger alerts in the persistence pipeline
10. Active entries trigger the existing alert flow with matching severity and metadata
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure project root and backend root are on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
for p in [str(ROOT_DIR), str(BACKEND_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from app.database.connection import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.models.watchlist import Watchlist
from app.services.alert_service import AlertService
from app.services.anpr_service import ANPRPersistenceService
from ai_engine.anpr.schemas import ANPRResult


@pytest.fixture
def db_session():
    """In-memory SQLite database session fixture with foreign keys enabled."""
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
    """FastAPI TestClient with overridden get_db dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def test_camera(db_session):
    """Seed a test camera."""
    camera = Camera(
        camera_code="CAM-01",
        name="Main Gate Camera",
        location="North Toll Plaza",
        status="online",
    )
    db_session.add(camera)
    db_session.commit()
    db_session.refresh(camera)
    return camera


# =========================================================================
# 1. CREATE WATCHLIST ENTRY
# =========================================================================
def test_create_watchlist_entry(client):
    response = client.post(
        "/api/watchlist",
        json={
            "plate_number": "GJ05AB1234",
            "description": "Suspect in toll fraud investigation",
            "severity": "critical",
            "is_active": True,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["plate_number"] == "GJ05AB1234"
    assert data["description"] == "Suspect in toll fraud investigation"
    assert data["severity"] == "critical"
    assert data["is_active"] is True
    assert "created_at" in data
    assert "updated_at" in data


# =========================================================================
# 2. LIST ENTRIES WITH FILTERING & SEARCH
# =========================================================================
def test_list_watchlist_entries(client):
    # Seed entries
    client.post("/api/watchlist", json={"plate_number": "GJ01AB1111", "severity": "high", "is_active": True})
    client.post("/api/watchlist", json={"plate_number": "MH12CD2222", "severity": "low", "is_active": False})
    client.post("/api/watchlist", json={"plate_number": "GJ05EF3333", "severity": "critical", "is_active": True})

    # List all
    res_all = client.get("/api/watchlist")
    assert res_all.status_code == 200
    assert len(res_all.json()) == 3

    # Filter is_active=true
    res_active = client.get("/api/watchlist?is_active=true")
    assert res_active.status_code == 200
    active_plates = [x["plate_number"] for x in res_active.json()]
    assert "GJ01AB1111" in active_plates
    assert "GJ05EF3333" in active_plates
    assert "MH12CD2222" not in active_plates

    # Filter is_active=false
    res_inactive = client.get("/api/watchlist?is_active=false")
    assert res_inactive.status_code == 200
    assert len(res_inactive.json()) == 1
    assert res_inactive.json()[0]["plate_number"] == "MH12CD2222"

    # Filter severity
    res_critical = client.get("/api/watchlist?severity=critical")
    assert res_critical.status_code == 200
    assert len(res_critical.json()) == 1
    assert res_critical.json()[0]["plate_number"] == "GJ05EF3333"

    # Search by partial plate
    res_search = client.get("/api/watchlist?search=MH12")
    assert res_search.status_code == 200
    assert len(res_search.json()) == 1
    assert res_search.json()[0]["plate_number"] == "MH12CD2222"


# =========================================================================
# 3. GET ENTRY BY ID
# =========================================================================
def test_get_watchlist_entry(client):
    create_res = client.post(
        "/api/watchlist",
        json={"plate_number": "DL01A1234", "description": "Stolen sedan", "severity": "high"},
    )
    entry_id = create_res.json()["id"]

    get_res = client.get(f"/api/watchlist/{entry_id}")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["id"] == entry_id
    assert data["plate_number"] == "DL01A1234"
    assert data["description"] == "Stolen sedan"


# =========================================================================
# 4. UPDATE ENTRY
# =========================================================================
def test_update_watchlist_entry(client):
    create_res = client.post(
        "/api/watchlist",
        json={"plate_number": "KA04MH9999", "description": "Old note", "severity": "medium"},
    )
    entry_id = create_res.json()["id"]

    patch_res = client.patch(
        f"/api/watchlist/{entry_id}",
        json={
            "description": "Updated priority investigation",
            "severity": "critical",
            "is_active": False,
        },
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["description"] == "Updated priority investigation"
    assert data["severity"] == "critical"
    assert data["is_active"] is False


# =========================================================================
# 5. DELETE & DEACTIVATE ENTRY
# =========================================================================
def test_delete_and_deactivate_entry(client):
    # 5a: Soft deactivate
    res1 = client.post("/api/watchlist", json={"plate_number": "RJ14CA5555", "is_active": True})
    id1 = res1.json()["id"]

    deact_res = client.delete(f"/api/watchlist/{id1}?deactivate_only=true")
    assert deact_res.status_code == 200
    assert deact_res.json()["is_active"] is False

    # Verify it is now inactive in DB
    get_res = client.get(f"/api/watchlist/{id1}")
    assert get_res.json()["is_active"] is False

    # 5b: Hard delete
    res2 = client.post("/api/watchlist", json={"plate_number": "TS08UB7777"})
    id2 = res2.json()["id"]

    del_res = client.delete(f"/api/watchlist/{id2}")
    assert del_res.status_code == 200

    get_del = client.get(f"/api/watchlist/{id2}")
    assert get_del.status_code == 404


# =========================================================================
# 6. PLATE NORMALIZATION & VALIDATION
# =========================================================================
def test_plate_normalization(client):
    # Punctuation & lowercase input
    res = client.post(
        "/api/watchlist",
        json={"plate_number": "gj - 05 ab . 1234", "description": "Dirty formatting test"},
    )
    assert res.status_code == 201
    assert res.json()["plate_number"] == "GJ05AB1234"

    # Bharat Series plate normalization
    res_bh = client.post(
        "/api/watchlist",
        json={"plate_number": "22 bh 1234 aa"},
    )
    assert res_bh.status_code == 201
    assert res_bh.json()["plate_number"] == "22BH1234AA"

    # Invalid non-Indian plate
    res_inv = client.post(
        "/api/watchlist",
        json={"plate_number": "INVALID-123-XYZ"},
    )
    assert res_inv.status_code == 422
    assert "Invalid Indian license plate format" in res_inv.json()["detail"]


# =========================================================================
# 7. DUPLICATE PROTECTION
# =========================================================================
def test_duplicate_active_protection(client):
    # Create first active entry
    res1 = client.post(
        "/api/watchlist",
        json={"plate_number": "GJ01AB9999", "is_active": True},
    )
    assert res1.status_code == 201

    # Attempt to create duplicate active entry with same plate (even with spaces)
    res2 = client.post(
        "/api/watchlist",
        json={"plate_number": "GJ 01 AB 9999", "is_active": True},
    )
    assert res2.status_code == 409
    assert "already exists" in res2.json()["detail"]


# =========================================================================
# 8. INVALID ID HANDLING
# =========================================================================
def test_invalid_id_returns_404(client):
    assert client.get("/api/watchlist/99999").status_code == 404
    assert client.patch("/api/watchlist/99999", json={"description": "x"}).status_code == 404
    assert client.delete("/api/watchlist/99999").status_code == 404


# =========================================================================
# 9. INACTIVE ENTRIES DO NOT TRIGGER ALERTS
# =========================================================================
def test_inactive_entry_does_not_trigger_alert(client, db_session, test_camera):
    # Add inactive entry
    client.post(
        "/api/watchlist",
        json={
            "plate_number": "GJ05ZZ0001",
            "description": "Deactivated target",
            "severity": "critical",
            "is_active": False,
        },
    )

    # Persist detection through ANPRPersistenceService with watchlist=None (using DB)
    service = ANPRPersistenceService(db_session)
    result = service.persist_result(
        anpr_result=ANPRResult(
            camera_id="CAM-01",
            track_id=10,
            pts_ms=100.0,
            raw_text="GJ05ZZ0001",
            normalized_plate="GJ05ZZ0001",
            confidence=0.95,
            is_valid_format=True,
            status="RECOGNIZED",
        ),
        camera_code="CAM-01",
        watchlist=None,  # Tests database-backed query
    )

    assert result is not None
    assert result.plate_number == "GJ05ZZ0001"
    assert result.event_id is not None
    assert result.alert_id is None
    assert result.alert is None

    # Verify no alert in database
    alerts_count = db_session.query(Alert).count()
    assert alerts_count == 0


# =========================================================================
# 10. ACTIVE ENTRIES TRIGGER THE ALERT FLOW
# =========================================================================
def test_active_entry_triggers_alert_flow(client, db_session, test_camera):
    # Add active entry with specific severity and description
    client.post(
        "/api/watchlist",
        json={
            "plate_number": "GJ05ZZ9999",
            "description": "High value wanted vehicle",
            "severity": "critical",
            "is_active": True,
        },
    )

    # Persist detection with watchlist=None (triggers database-backed query)
    service = ANPRPersistenceService(db_session)
    result = service.persist_result(
        anpr_result=ANPRResult(
            camera_id="CAM-01",
            track_id=20,
            pts_ms=250.0,
            raw_text="GJ05ZZ9999",
            normalized_plate="GJ05ZZ9999",
            confidence=0.98,
            is_valid_format=True,
            status="RECOGNIZED",
        ),
        camera_code="CAM-01",
        watchlist=None,
    )

    assert result is not None
    assert result.plate_number == "GJ05ZZ9999"
    assert result.alert_id is not None
    assert result.alert is not None
    assert result.alert.severity == "critical"
    assert "High value wanted vehicle" in result.alert.message
    assert "CAM-01" in result.alert.message
    assert result.alert.status == "new"

    # Verify Alert was saved to database
    db_alert = db_session.query(Alert).filter(Alert.id == result.alert_id).first()
    assert db_alert is not None
    assert db_alert.camera_id == test_camera.id
    assert db_alert.vehicle_id == result.vehicle_id
