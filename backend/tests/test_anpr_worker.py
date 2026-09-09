"""
Unit and integration tests for ANPRPersistenceWorker (Phase 7.5 Stage 2).

Tests:
- T11-A: enqueue accepted when queue has capacity
- T11-B: enqueue returns controlled QUEUE_FULL when queue is full and does not block
- T11-C: worker consumes an item and invokes ANPRPersistenceService
- T11-D: worker creates/uses its own database session rather than receiving a session from caller
- T11-E: successful worker processing persists the expected result
- T11-F: database/persistence exception does not terminate the worker
- T11-G: worker continues processing the next valid item after a failed item
- T11-H: stop() shuts the worker down cleanly
- T11-I: queue drain/join behavior works correctly
- T11-J: two independent worker instances do not share mutable state
- T11-K: camera identifiers remain isolated between queued items
- T11-L: worker does not mutate ANPRResult data
- Plus lifecycle, retry, and sanitization tests.
"""

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure Sentinel root and backend root are on sys.path
sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.database.connection import Base
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.services.anpr_service import ANPRPersistenceService
from app.services.anpr_worker import (
    ANPRPersistenceWorker,
    ANPRQueueItem,
    EnqueueStatus,
    _safe_error_str,
)
from ai_engine.anpr.schemas import ANPRResult


@pytest.fixture
def sqlite_engine():
    """Create a thread-safe in-memory SQLite database engine using StaticPool."""
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
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sqlite_session_factory(sqlite_engine):
    """Factory creating fresh sessions against the shared SQLite engine."""
    return sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False)


@pytest.fixture
def seeded_camera(sqlite_session_factory):
    """Seed test camera CAM-01."""
    session = sqlite_session_factory()
    try:
        camera = Camera(
            camera_code="CAM-01",
            name="Main Entrance Gate",
            location="North Gate",
            status="active",
        )
        session.add(camera)
        session.commit()
        session.refresh(camera)
        return camera
    finally:
        session.close()


@pytest.fixture
def seeded_multi_cameras(sqlite_session_factory):
    """Seed CAM-01 and CAM-02 for camera isolation tests."""
    session = sqlite_session_factory()
    try:
        c1 = Camera(camera_code="CAM-01", name="Gate 1", location="North", status="active")
        c2 = Camera(camera_code="CAM-02", name="Gate 2", location="South", status="active")
        session.add_all([c1, c2])
        session.commit()
        session.refresh(c1)
        session.refresh(c2)
        return c1, c2
    finally:
        session.close()


def make_anpr_result(
    camera_id: str = "CAM-01",
    track_id: int = 1,
    pts_ms: float = 100.0,
    raw_text: str = "GJ05AB1234",
    normalized_plate: str = "GJ05AB1234",
    confidence: float = 0.92,
    is_valid_format: bool = True,
    status: str = "RECOGNIZED",
) -> ANPRResult:
    """Helper factory for valid ANPRResult instances."""
    return ANPRResult(
        camera_id=camera_id,
        track_id=track_id,
        pts_ms=pts_ms,
        raw_text=raw_text,
        normalized_plate=normalized_plate,
        confidence=confidence,
        is_valid_format=is_valid_format,
        status=status,
    )


# ---------------------------------------------------------------------------
# T11-A: Enqueue accepted when queue has capacity
# ---------------------------------------------------------------------------
def test_t11_a_enqueue_accepted_when_capacity():
    worker = ANPRPersistenceWorker(max_queue_size=5)
    anpr = make_anpr_result()

    result = worker.enqueue(anpr_result=anpr, camera_code="CAM-01")

    assert result == EnqueueStatus.ACCEPTED
    assert result == "ACCEPTED"
    assert result.success is True
    assert worker.qsize == 1


# ---------------------------------------------------------------------------
# T11-B: Enqueue returns controlled QUEUE_FULL when full and does not block
# ---------------------------------------------------------------------------
def test_t11_b_enqueue_returns_controlled_queue_full_when_full():
    worker = ANPRPersistenceWorker(max_queue_size=2)
    anpr = make_anpr_result()

    res1 = worker.enqueue(anpr_result=anpr, camera_code="CAM-01")
    res2 = worker.enqueue(anpr_result=anpr, camera_code="CAM-01")
    assert res1 == EnqueueStatus.ACCEPTED
    assert res2 == EnqueueStatus.ACCEPTED
    assert worker.qsize == 2

    # Attempt to enqueue beyond capacity
    t0 = time.monotonic()
    res3 = worker.enqueue(anpr_result=anpr, camera_code="CAM-01")
    duration = time.monotonic() - t0

    assert res3 == EnqueueStatus.QUEUE_FULL
    assert res3 == "QUEUE_FULL"
    assert res3.success is False
    assert duration < 0.1  # Verified zero blocking
    assert worker.qsize == 2


# ---------------------------------------------------------------------------
# T11-C: Worker consumes item and invokes ANPRPersistenceService
# ---------------------------------------------------------------------------
def test_t11_c_worker_consumes_item_and_invokes_persistence_service():
    mock_session = MagicMock()
    mock_session_factory = MagicMock(return_value=mock_session)
    mock_service = MagicMock()
    mock_service_factory = MagicMock(return_value=mock_service)

    worker = ANPRPersistenceWorker(
        session_factory=mock_session_factory,
        persistence_service_factory=mock_service_factory,
        poll_timeout_sec=0.05,
    )
    worker.start()

    anpr = make_anpr_result()
    worker.enqueue(
        anpr_result=anpr,
        camera_code="CAM-01",
        snapshot_path="/snapshots/car.jpg",
        event_type="custom_detection",
    )

    drained = worker.join(timeout=2.0)
    assert drained is True

    mock_service.persist_result.assert_called_once()
    call_kwargs = mock_service.persist_result.call_args.kwargs
    assert call_kwargs["anpr_result"] == anpr
    assert call_kwargs["camera_code"] == "CAM-01"
    assert call_kwargs["snapshot_path"] == "/snapshots/car.jpg"
    assert call_kwargs["event_type"] == "custom_detection"

    worker.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# T11-D: Worker creates/uses its own database session and closes it
# ---------------------------------------------------------------------------
def test_t11_d_worker_creates_and_closes_own_session():
    mock_session = MagicMock()
    mock_session_factory = MagicMock(return_value=mock_session)
    mock_service = MagicMock()
    mock_service_factory = MagicMock(return_value=mock_service)

    worker = ANPRPersistenceWorker(
        session_factory=mock_session_factory,
        persistence_service_factory=mock_service_factory,
        poll_timeout_sec=0.05,
    )
    worker.start()

    anpr = make_anpr_result()
    # Note: caller never passes any db session into enqueue
    worker.enqueue(anpr_result=anpr, camera_code="CAM-01")

    assert worker.join(timeout=2.0) is True

    # Worker invoked factory itself and closed the session
    mock_session_factory.assert_called_once()
    mock_session.close.assert_called_once()

    worker.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# T11-E: Successful worker processing persists expected result in database
# ---------------------------------------------------------------------------
def test_t11_e_successful_worker_processing_persists_expected_result(
    sqlite_session_factory, seeded_camera
):
    worker = ANPRPersistenceWorker(
        session_factory=sqlite_session_factory,
        poll_timeout_sec=0.05,
    )
    worker.start()

    anpr = make_anpr_result(raw_text="MH12AB1234", normalized_plate="MH12AB1234")
    worker.enqueue(
        anpr_result=anpr,
        camera_code="CAM-01",
        snapshot_path="/data/snap1.jpg",
    )

    assert worker.join(timeout=3.0) is True
    worker.stop(timeout=1.0)

    # Inspect database via fresh inspection session
    session = sqlite_session_factory()
    try:
        vehicle = session.query(Vehicle).filter(Vehicle.plate_number == "MH12AB1234").first()
        assert vehicle is not None

        event = session.query(Event).filter(Event.vehicle_id == vehicle.id).first()
        assert event is not None
        assert event.camera_id == seeded_camera.id
        assert event.snapshot_path == "/data/snap1.jpg"
        assert event.confidence == pytest.approx(0.92)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# T11-F: Database exception does not terminate the worker
# ---------------------------------------------------------------------------
def test_t11_f_database_exception_does_not_terminate_worker():
    failing_session_factory = MagicMock(side_effect=SQLAlchemyError("Connection refused"))

    worker = ANPRPersistenceWorker(
        session_factory=failing_session_factory,
        max_retries=1,
        retry_backoff_sec=0.01,
        poll_timeout_sec=0.05,
    )
    worker.start()

    anpr = make_anpr_result()
    worker.enqueue(anpr_result=anpr, camera_code="CAM-01")

    assert worker.join(timeout=2.0) is True
    # Worker thread survived the exception
    assert worker.is_alive() is True

    worker.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# T11-G: Worker continues processing next valid item after a failed item
# ---------------------------------------------------------------------------
def test_t11_g_worker_continues_processing_next_valid_item_after_failure():
    call_records = []

    def mock_persist_side_effect(**kwargs):
        call_records.append(kwargs["camera_code"])
        if len(call_records) == 1:
            raise SQLAlchemyError("Intermittent DB failure")
        return None

    mock_service = MagicMock()
    mock_service.persist_result.side_effect = mock_persist_side_effect
    mock_service_factory = MagicMock(return_value=mock_service)
    mock_session = MagicMock()
    mock_session_factory = MagicMock(return_value=mock_session)

    worker = ANPRPersistenceWorker(
        session_factory=mock_session_factory,
        persistence_service_factory=mock_service_factory,
        max_retries=0,  # Fast fail first item to test continuation
        poll_timeout_sec=0.05,
    )
    worker.start()

    item1 = make_anpr_result(normalized_plate="KA01AB1111")
    item2 = make_anpr_result(normalized_plate="KA01AB2222")

    worker.enqueue(anpr_result=item1, camera_code="CAM-01")
    worker.enqueue(anpr_result=item2, camera_code="CAM-01")

    assert worker.join(timeout=3.0) is True
    assert mock_service.persist_result.call_count == 2
    assert worker.is_alive() is True

    worker.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# T11-H: stop() shuts worker down cleanly
# ---------------------------------------------------------------------------
def test_t11_h_stop_shuts_worker_down_cleanly():
    worker = ANPRPersistenceWorker(poll_timeout_sec=0.05)
    worker.start()
    assert worker.is_alive() is True

    worker.stop(timeout=2.0)
    assert worker.is_alive() is False


# ---------------------------------------------------------------------------
# T11-I: Queue drain/join behavior works correctly
# ---------------------------------------------------------------------------
def test_t11_i_queue_drain_join_behavior():
    mock_session_factory = MagicMock()
    mock_service_factory = MagicMock()

    worker = ANPRPersistenceWorker(
        session_factory=mock_session_factory,
        persistence_service_factory=mock_service_factory,
        poll_timeout_sec=0.05,
    )
    worker.start()

    for i in range(10):
        worker.enqueue(
            anpr_result=make_anpr_result(track_id=i),
            camera_code="CAM-01",
        )

    assert worker.join(timeout=5.0) is True
    assert worker.qsize == 0

    worker.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# T11-J: Two independent worker instances do not share mutable state
# ---------------------------------------------------------------------------
def test_t11_j_two_independent_worker_instances_do_not_share_state():
    worker1 = ANPRPersistenceWorker(max_queue_size=2)
    worker2 = ANPRPersistenceWorker(max_queue_size=5)

    anpr = make_anpr_result()

    worker1.enqueue(anpr_result=anpr, camera_code="CAM-01")
    worker1.enqueue(anpr_result=anpr, camera_code="CAM-01")

    assert worker1.qsize == 2
    assert worker2.qsize == 0

    # worker1 is full, worker2 still has capacity
    assert worker1.enqueue(anpr_result=anpr, camera_code="CAM-01") == EnqueueStatus.QUEUE_FULL
    assert worker2.enqueue(anpr_result=anpr, camera_code="CAM-02") == EnqueueStatus.ACCEPTED

    assert worker1.qsize == 2
    assert worker2.qsize == 1


# ---------------------------------------------------------------------------
# T11-K: Camera identifiers remain isolated between queued items
# ---------------------------------------------------------------------------
def test_t11_k_camera_identifiers_remain_isolated(
    sqlite_session_factory, seeded_multi_cameras
):
    cam1, cam2 = seeded_multi_cameras

    worker = ANPRPersistenceWorker(
        session_factory=sqlite_session_factory,
        poll_timeout_sec=0.05,
    )
    worker.start()

    res1 = make_anpr_result(camera_id="CAM-01", normalized_plate="DL01AA1111")
    res2 = make_anpr_result(camera_id="CAM-02", normalized_plate="HR26BB2222")

    worker.enqueue(anpr_result=res1, camera_code="CAM-01")
    worker.enqueue(anpr_result=res2, camera_code="CAM-02")

    assert worker.join(timeout=3.0) is True
    worker.stop(timeout=1.0)

    session = sqlite_session_factory()
    try:
        v1 = session.query(Vehicle).filter(Vehicle.plate_number == "DL01AA1111").first()
        v2 = session.query(Vehicle).filter(Vehicle.plate_number == "HR26BB2222").first()
        assert v1 is not None and v2 is not None

        e1 = session.query(Event).filter(Event.vehicle_id == v1.id).first()
        e2 = session.query(Event).filter(Event.vehicle_id == v2.id).first()
        assert e1.camera_id == cam1.id
        assert e2.camera_id == cam2.id
    finally:
        session.close()


# ---------------------------------------------------------------------------
# T11-L: Worker does not mutate ANPRResult data
# ---------------------------------------------------------------------------
def test_t11_l_worker_does_not_mutate_anpr_result(sqlite_session_factory, seeded_camera):
    worker = ANPRPersistenceWorker(
        session_factory=sqlite_session_factory,
        poll_timeout_sec=0.05,
    )
    worker.start()

    anpr = make_anpr_result(
        camera_id="CAM-01",
        track_id=42,
        pts_ms=1234.5,
        raw_text="GJ05AB1234",
        normalized_plate="GJ05AB1234",
        confidence=0.88,
    )

    before_dict = anpr.to_dict()

    worker.enqueue(anpr_result=anpr, camera_code="CAM-01")
    assert worker.join(timeout=3.0) is True

    after_dict = anpr.to_dict()
    assert before_dict == after_dict

    worker.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Supplementary Tests: Retries, Sanitization, Idempotency, QueueItem
# ---------------------------------------------------------------------------
def test_worker_retries_transient_failure_then_succeeds():
    """Worker retries on transient error and succeeds on subsequent attempt."""
    call_count = 0

    def flaky_persist(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise SQLAlchemyError("Temporary lock timeout")
        return None

    mock_service = MagicMock()
    mock_service.persist_result.side_effect = flaky_persist
    mock_service_factory = MagicMock(return_value=mock_service)
    mock_session = MagicMock()
    mock_session_factory = MagicMock(return_value=mock_session)

    worker = ANPRPersistenceWorker(
        session_factory=mock_session_factory,
        persistence_service_factory=mock_service_factory,
        max_retries=2,
        retry_backoff_sec=0.01,
        poll_timeout_sec=0.05,
    )
    worker.start()

    worker.enqueue(anpr_result=make_anpr_result(), camera_code="CAM-01")

    assert worker.join(timeout=2.0) is True
    assert call_count == 2
    assert worker.is_alive() is True

    worker.stop(timeout=1.0)


def test_safe_error_str_scrubs_passwords_and_uris():
    """Exception message scrubber strips passwords and database connection strings."""
    secret_err = Exception("FATAL: password authentication failed for postgresql://sentinel:secret_pass_123@localhost:5432/db")
    scrubbed = _safe_error_str(secret_err)
    assert "secret_pass_123" not in scrubbed
    assert ":***@" in scrubbed

    param_err = Exception("Connection failed with password=super_confidential_pw host=localhost")
    scrubbed_param = _safe_error_str(param_err)
    assert "super_confidential_pw" not in scrubbed_param
    assert "password=***" in scrubbed_param


def test_start_and_stop_idempotent():
    """Calling start() and stop() repeatedly does not raise errors or leak threads."""
    worker = ANPRPersistenceWorker(poll_timeout_sec=0.05)
    worker.start()
    thread_1 = worker._thread
    worker.start()
    assert worker._thread is thread_1

    worker.stop(timeout=1.0)
    assert worker.is_alive() is False
    worker.stop(timeout=1.0)
    assert worker.is_alive() is False


def test_enqueue_when_stopped_returns_stopped():
    """Enqueueing when worker is stopped returns EnqueueStatus.STOPPED."""
    worker = ANPRPersistenceWorker(poll_timeout_sec=0.05)
    worker.start()
    worker.stop(timeout=1.0)

    res = worker.enqueue(anpr_result=make_anpr_result(), camera_code="CAM-01")
    assert res == EnqueueStatus.STOPPED
    assert res == "STOPPED"
    assert res.success is False


def test_queue_item_immutability():
    """ANPRQueueItem is frozen and converts iterable watchlist to tuple."""
    item = ANPRQueueItem(
        anpr_result=make_anpr_result(),
        camera_code="CAM-01",
        watchlist=["MH12AB1234", "DL01AA1111"],
    )
    assert isinstance(item.watchlist, tuple)
    assert item.watchlist == ("MH12AB1234", "DL01AA1111")
    with pytest.raises(Exception):
        item.camera_code = "CAM-02"  # type: ignore
