"""
End-to-end check of the stolen-vehicle workflow.

A vehicle is added to the watchlist, seen at one camera, disappears through an
area with no coverage, and is seen again days later at a different camera. What
matters is that both sightings attach to the same vehicle record, both raise
alerts, the timeline puts them in order, and nothing is invented for the gap
in between.

This exercises the persistence chain only. It deliberately feeds recognised
ANPR results in directly, so it stays meaningful whether or not plate-detector
weights are present on the machine running it.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.database.connection import Base
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.models.watchlist import Watchlist
from app.services.anpr_service import ANPRPersistenceService
from ai_engine.anpr.schemas import ANPRResult

STOLEN_PLATE = "GJ05AB1234"
OTHER_PLATE = "GJ01XY9999"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")

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
def cameras(db_session):
    """Two checkpoints; the second has no survey coordinates yet."""
    highway = Camera(
        camera_code="CAM-001", name="Surat Highway", location="Surat",
        latitude=21.1702, longitude=72.8311, status="online",
    )
    bypass = Camera(
        camera_code="CAM-002", name="Bypass Junction", location="Vadodara",
        latitude=None, longitude=None, status="online",
    )
    db_session.add_all([highway, bypass])
    db_session.commit()
    db_session.refresh(highway)
    db_session.refresh(bypass)
    return {"highway": highway, "bypass": bypass}


@pytest.fixture
def flagged(db_session):
    entry = Watchlist(
        plate_number=STOLEN_PLATE,
        description="Reported stolen",
        severity="high",
        is_active=True,
    )
    db_session.add(entry)
    db_session.commit()
    return entry


def recognised(camera_code, plate, confidence=0.93):
    return ANPRResult(
        camera_id=camera_code,
        track_id=1,
        pts_ms=1000.0,
        raw_text=plate,
        normalized_plate=plate,
        confidence=confidence,
        is_valid_format=True,
        status="RECOGNIZED",
    )


def sight(db_session, camera, plate, when, confidence=0.93):
    """Record one sighting exactly as the persistence worker would."""
    return ANPRPersistenceService(db_session).persist_result(
        anpr_result=recognised(camera.camera_code, plate, confidence),
        camera_code=camera.camera_code,
        timestamp=when,
    )


class TestStolenVehicleWorkflow:
    def test_a_flagged_plate_raises_an_alert_on_first_sighting(self, db_session, cameras, flagged):
        first_seen = datetime(2026, 9, 1, 8, 30, 0)
        sight(db_session, cameras["highway"], STOLEN_PLATE, first_seen)

        alerts = db_session.query(Alert).all()
        assert len(alerts) == 1
        assert alerts[0].camera_id == cameras["highway"].id
        assert alerts[0].severity == "high"

    def test_a_plate_not_on_the_watchlist_is_recorded_without_an_alert(self, db_session, cameras, flagged):
        sight(db_session, cameras["highway"], OTHER_PLATE, datetime(2026, 9, 1, 9, 0, 0))

        assert db_session.query(Event).count() == 1
        assert db_session.query(Alert).count() == 0

    def test_reappearing_days_later_links_to_the_same_vehicle(self, db_session, cameras, flagged):
        first_seen = datetime(2026, 9, 1, 8, 30, 0)
        reappeared = first_seen + timedelta(days=4, hours=3)

        sight(db_session, cameras["highway"], STOLEN_PLATE, first_seen)
        # ... nothing at all in between: the vehicle is off camera ...
        sight(db_session, cameras["bypass"], STOLEN_PLATE, reappeared)

        vehicles = db_session.query(Vehicle).filter(Vehicle.plate_number == STOLEN_PLATE).all()
        assert len(vehicles) == 1, "the second sighting must reuse the known vehicle"

        events = db_session.query(Event).filter(Event.vehicle_id == vehicles[0].id).order_by(Event.timestamp).all()
        assert [e.camera_id for e in events] == [cameras["highway"].id, cameras["bypass"].id]
        assert [e.timestamp for e in events] == [first_seen, reappeared]

    def test_each_sighting_of_a_flagged_vehicle_raises_its_own_alert(self, db_session, cameras, flagged):
        first_seen = datetime(2026, 9, 1, 8, 30, 0)
        sight(db_session, cameras["highway"], STOLEN_PLATE, first_seen)
        sight(db_session, cameras["bypass"], STOLEN_PLATE, first_seen + timedelta(days=4))

        alerts = db_session.query(Alert).order_by(Alert.timestamp).all()
        assert len(alerts) == 2
        assert {a.camera_id for a in alerts} == {cameras["highway"].id, cameras["bypass"].id}
        # Both point at the one vehicle record, so an investigator sees one history.
        assert len({a.vehicle_id for a in alerts}) == 1

    def test_nothing_is_recorded_for_the_period_with_no_coverage(self, db_session, cameras, flagged):
        first_seen = datetime(2026, 9, 1, 8, 30, 0)
        reappeared = first_seen + timedelta(days=4)

        sight(db_session, cameras["highway"], STOLEN_PLATE, first_seen)
        sight(db_session, cameras["bypass"], STOLEN_PLATE, reappeared)

        # Exactly two sightings: the gap produces no interpolated position.
        assert db_session.query(Event).count() == 2
        gap_events = (
            db_session.query(Event)
            .filter(Event.timestamp > first_seen, Event.timestamp < reappeared)
            .count()
        )
        assert gap_events == 0

    def test_a_sighting_at_an_unsurveyed_camera_claims_no_position(self, db_session, cameras, flagged):
        """The bypass camera has no coordinates; its sighting must not invent any."""
        sight(db_session, cameras["bypass"], STOLEN_PLATE, datetime(2026, 9, 5, 6, 0, 0))

        camera = db_session.query(Camera).filter(Camera.camera_code == "CAM-002").one()
        assert camera.latitude is None and camera.longitude is None

    def test_deactivating_the_watchlist_entry_stops_new_alerts(self, db_session, cameras, flagged):
        sight(db_session, cameras["highway"], STOLEN_PLATE, datetime(2026, 9, 1, 8, 0, 0))
        assert db_session.query(Alert).count() == 1

        # Vehicle recovered: the flag is stood down.
        flagged.is_active = False
        db_session.commit()

        sight(db_session, cameras["bypass"], STOLEN_PLATE, datetime(2026, 9, 6, 8, 0, 0))

        assert db_session.query(Alert).count() == 1, "a stood-down flag must not keep alerting"
        assert db_session.query(Event).count() == 2, "but the sighting is still recorded"

    def test_adding_a_plate_to_the_watchlist_takes_effect_without_a_restart(self, db_session, cameras):
        """A plate seen before being flagged starts alerting from the next sighting."""
        sight(db_session, cameras["highway"], OTHER_PLATE, datetime(2026, 9, 1, 8, 0, 0))
        assert db_session.query(Alert).count() == 0

        db_session.add(Watchlist(plate_number=OTHER_PLATE, severity="high", is_active=True))
        db_session.commit()

        sight(db_session, cameras["bypass"], OTHER_PLATE, datetime(2026, 9, 1, 9, 0, 0))

        alerts = db_session.query(Alert).all()
        assert len(alerts) == 1
        assert alerts[0].camera_id == cameras["bypass"].id

    def test_the_watchlist_matches_regardless_of_spacing_or_case(self, db_session, cameras):
        db_session.add(Watchlist(plate_number=STOLEN_PLATE, severity="high", is_active=True))
        db_session.commit()

        sight(db_session, cameras["highway"], "gj05 ab 1234", datetime(2026, 9, 1, 8, 0, 0))

        assert db_session.query(Alert).count() == 1
        assert db_session.query(Vehicle).one().plate_number == STOLEN_PLATE
