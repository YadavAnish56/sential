"""
Unit and integration tests for PersistenceDispatcher (Phase 7.5 Stage 3).

Tests:
- T12-A: RECOGNIZED valid Indian ANPRResult is enqueued
- T12-B: NO_PLATE_DETECTED is not enqueued
- T12-C: LOW_CONFIDENCE is not enqueued
- T12-D: INVALID_FORMAT is not enqueued
- T12-E: FAILED is not enqueued
- T12-F: camera_code is preserved
- T12-G: wall-clock timestamp is preserved and pts_ms is not converted
- T12-H: multiple ANPR results are dispatched independently
- T12-I: QUEUE_FULL is reported without blocking
- T12-J: worker/pipeline failure does not crash dispatcher
- T12-K: dispatcher does not mutate ANPRResult
- T12-L: two camera results remain isolated
- T12-M: watchlist data is passed through correctly when configured
- Integration 1: Synthetic FramePacket -> CameraPipeline -> StubDetector / MockPlateRecognizer -> Dispatcher -> MockWorker
- Integration 2: Synthetic ANPRResult -> Dispatcher -> Real ANPRPersistenceWorker -> Real ANPRPersistenceService -> SQLite DB
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure Sentinel root and backend are on sys.path
sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from ai_engine.anpr import ANPRConfig, ANPRCoordinator
from ai_engine.anpr.recognizer import MockPlateRecognizer
from ai_engine.anpr.schemas import ANPRResult, PlateCandidate
from ai_engine.persistence_dispatcher import (
    DispatchResult,
    PersistenceDispatcher,
    dispatch_pipeline_results,
    is_eligible_for_persistence,
)
from ai_engine.pipeline import CameraPipeline, PipelineResult
from ai_engine.schemas import Detection, DetectionResult
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import TrackerConfig, VehicleTracker
from app.database.connection import Base
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.services.anpr_worker import ANPRPersistenceWorker, EnqueueStatus
from streaming.frame_reader import FramePacket


def make_anpr_result(
    camera_id: str = "CAM-01",
    track_id: int = 1,
    pts_ms: float = 1250.0,
    raw_text: str = "MH12AB1234",
    normalized_plate: str = "MH12AB1234",
    confidence: float = 0.92,
    is_valid_format: bool = True,
    status: str = "RECOGNIZED",
) -> ANPRResult:
    """Helper factory for ANPRResult instances."""
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
# T12-A: RECOGNIZED valid Indian ANPRResult is enqueued
# ---------------------------------------------------------------------------
def test_t12_a_recognized_valid_plate_is_enqueued():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.ACCEPTED

    dispatcher = PersistenceDispatcher(worker=mock_worker)
    anpr = make_anpr_result(status="RECOGNIZED", is_valid_format=True, normalized_plate="MH12AB1234")

    res = dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")

    assert res.received == 1
    assert res.eligible == 1
    assert res.enqueued == 1
    assert res.skipped == 0
    assert res.queue_full == 0
    assert res.success is True
    mock_worker.enqueue.assert_called_once()


# ---------------------------------------------------------------------------
# T12-B: NO_PLATE_DETECTED is not enqueued
# ---------------------------------------------------------------------------
def test_t12_b_no_plate_detected_is_not_enqueued():
    mock_worker = MagicMock()
    dispatcher = PersistenceDispatcher(worker=mock_worker)
    anpr = make_anpr_result(
        status="NO_PLATE_DETECTED",
        is_valid_format=False,
        raw_text="",
        normalized_plate="",
        confidence=0.0,
    )

    res = dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")

    assert res.received == 1
    assert res.eligible == 0
    assert res.enqueued == 0
    assert res.skipped == 1
    mock_worker.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# T12-C: LOW_CONFIDENCE is not enqueued
# ---------------------------------------------------------------------------
def test_t12_c_low_confidence_is_not_enqueued():
    mock_worker = MagicMock()
    dispatcher = PersistenceDispatcher(worker=mock_worker)
    anpr = make_anpr_result(
        status="LOW_CONFIDENCE",
        is_valid_format=True,
        normalized_plate="MH12AB1234",
        confidence=0.35,
    )

    res = dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")

    assert res.received == 1
    assert res.eligible == 0
    assert res.enqueued == 0
    assert res.skipped == 1
    mock_worker.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# T12-D: INVALID_FORMAT is not enqueued
# ---------------------------------------------------------------------------
def test_t12_d_invalid_format_is_not_enqueued():
    mock_worker = MagicMock()
    dispatcher = PersistenceDispatcher(worker=mock_worker)
    anpr = make_anpr_result(
        status="INVALID_FORMAT",
        is_valid_format=False,
        normalized_plate="XYZ999",
    )

    res = dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")

    assert res.received == 1
    assert res.eligible == 0
    assert res.enqueued == 0
    assert res.skipped == 1
    mock_worker.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# T12-E: FAILED is not enqueued
# ---------------------------------------------------------------------------
def test_t12_e_failed_is_not_enqueued():
    mock_worker = MagicMock()
    dispatcher = PersistenceDispatcher(worker=mock_worker)
    anpr = make_anpr_result(
        status="FAILED",
        is_valid_format=False,
        normalized_plate="",
    )

    res = dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")

    assert res.received == 1
    assert res.eligible == 0
    assert res.enqueued == 0
    assert res.skipped == 1
    mock_worker.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# T12-F: camera_code is preserved
# ---------------------------------------------------------------------------
def test_t12_f_camera_code_is_preserved():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.ACCEPTED
    dispatcher = PersistenceDispatcher(worker=mock_worker)

    anpr = make_anpr_result(camera_id="CAM-DEFAULT")
    dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-OVERRIDE")

    mock_worker.enqueue.assert_called_once()
    kwargs = mock_worker.enqueue.call_args.kwargs
    assert kwargs["camera_code"] == "CAM-OVERRIDE"


# ---------------------------------------------------------------------------
# T12-G: wall-clock timestamp is preserved and pts_ms is not converted
# ---------------------------------------------------------------------------
def test_t12_g_wall_clock_timestamp_preserved_pts_not_converted():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.ACCEPTED
    dispatcher = PersistenceDispatcher(worker=mock_worker)

    wall_clock = datetime(2026, 9, 6, 10, 30, 0, tzinfo=timezone.utc)
    anpr = make_anpr_result(pts_ms=98765.4)

    dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01", timestamp=wall_clock)

    mock_worker.enqueue.assert_called_once()
    kwargs = mock_worker.enqueue.call_args.kwargs
    assert kwargs["timestamp"] == wall_clock
    # pts_ms on ANPRResult is preserved as numeric float, never converted to datetime
    assert kwargs["anpr_result"].pts_ms == pytest.approx(98765.4)
    assert not isinstance(kwargs["anpr_result"].pts_ms, datetime)


# ---------------------------------------------------------------------------
# T12-H: multiple ANPR results are dispatched independently
# ---------------------------------------------------------------------------
def test_t12_h_multiple_anpr_results_dispatched_independently():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.ACCEPTED
    dispatcher = PersistenceDispatcher(worker=mock_worker)

    anpr1 = make_anpr_result(track_id=1, normalized_plate="MH12AB1111")
    anpr2 = make_anpr_result(track_id=2, normalized_plate="DL01CD2222")
    anpr_noise = make_anpr_result(track_id=3, status="LOW_CONFIDENCE", is_valid_format=False)

    pipeline_res = PipelineResult(
        camera_id="CAM-01",
        pts_ms=100.0,
        anpr_results=[anpr1, anpr_noise, anpr2],
    )

    res = dispatcher.dispatch(pipeline_result=pipeline_res)

    assert res.received == 3
    assert res.eligible == 2
    assert res.enqueued == 2
    assert res.skipped == 1
    assert mock_worker.enqueue.call_count == 2


# ---------------------------------------------------------------------------
# T12-I: QUEUE_FULL is reported without blocking
# ---------------------------------------------------------------------------
def test_t12_i_queue_full_reported_without_blocking():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.QUEUE_FULL
    dispatcher = PersistenceDispatcher(worker=mock_worker)

    anpr = make_anpr_result()

    t0 = time.monotonic()
    res = dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")
    duration = time.monotonic() - t0

    assert duration < 0.1
    assert res.queue_full == 1
    assert res.enqueued == 0
    assert res.has_backpressure is True
    assert res.success is False


# ---------------------------------------------------------------------------
# T12-J: worker/pipeline failure does not crash dispatcher
# ---------------------------------------------------------------------------
def test_t12_j_worker_failure_does_not_crash_dispatcher():
    mock_worker = MagicMock()
    mock_worker.enqueue.side_effect = RuntimeError("Worker connection pool exhausted")
    dispatcher = PersistenceDispatcher(worker=mock_worker)

    anpr = make_anpr_result()
    res = dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")

    assert res.received == 1
    assert res.eligible == 1
    assert len(res.errors) == 1
    assert "RuntimeError" in res.errors[0]
    assert res.success is False


# ---------------------------------------------------------------------------
# T12-K: dispatcher does not mutate ANPRResult
# ---------------------------------------------------------------------------
def test_t12_k_dispatcher_does_not_mutate_anpr_result():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.ACCEPTED
    dispatcher = PersistenceDispatcher(worker=mock_worker)

    anpr = make_anpr_result(
        camera_id="CAM-01",
        track_id=42,
        pts_ms=1234.5,
        raw_text="GJ05AB1234",
        normalized_plate="GJ05AB1234",
        confidence=0.91,
    )
    before_dict = anpr.to_dict()

    dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")

    assert anpr.to_dict() == before_dict


# ---------------------------------------------------------------------------
# T12-L: two camera results remain isolated
# ---------------------------------------------------------------------------
def test_t12_l_two_camera_results_remain_isolated():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.ACCEPTED
    dispatcher = PersistenceDispatcher(worker=mock_worker)

    anpr1 = make_anpr_result(camera_id="CAM-01", normalized_plate="DL01AA1111")
    anpr2 = make_anpr_result(camera_id="CAM-02", normalized_plate="HR26BB2222")

    dispatcher.dispatch(anpr_results=[anpr1], camera_code="CAM-01")
    dispatcher.dispatch(anpr_results=[anpr2], camera_code="CAM-02")

    assert mock_worker.enqueue.call_count == 2
    assert mock_worker.enqueue.call_args_list[0].kwargs["camera_code"] == "CAM-01"
    assert mock_worker.enqueue.call_args_list[1].kwargs["camera_code"] == "CAM-02"


# ---------------------------------------------------------------------------
# T12-M: watchlist data is passed through correctly when configured
# ---------------------------------------------------------------------------
def test_t12_m_watchlist_data_passed_through_correctly():
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.ACCEPTED
    dispatcher = PersistenceDispatcher(
        worker=mock_worker,
        default_watchlist=["MH12AB1234", "DL01AA1111"],
    )

    anpr = make_anpr_result(normalized_plate="MH12AB1234")
    dispatcher.dispatch(anpr_results=[anpr], camera_code="CAM-01")

    mock_worker.enqueue.assert_called_once()
    kwargs = mock_worker.enqueue.call_args.kwargs
    assert kwargs["watchlist"] == ("MH12AB1234", "DL01AA1111")

    # Override on dispatch call
    dispatcher.dispatch(
        anpr_results=[anpr],
        camera_code="CAM-01",
        watchlist=["CUSTOM1234"],
    )
    assert mock_worker.enqueue.call_args.kwargs["watchlist"] == ["CUSTOM1234"]


# ---------------------------------------------------------------------------
# Integration Test 1 (Requirement 12):
# Synthetic FramePacket -> CameraPipeline -> StubDetector / MockRecognizer -> Dispatcher -> MockWorker
# ---------------------------------------------------------------------------
class StubVehicleDetector:
    """Deterministic in-memory test double for VehicleDetector."""

    def __init__(self, detections: list[Detection] | None = None) -> None:
        self.detections = detections or []

    def detect(self, packet: FramePacket) -> DetectionResult:
        current_detections = [
            Detection(
                class_id=d.class_id,
                class_name=d.class_name,
                confidence=d.confidence,
                x1=d.x1,
                y1=d.y1,
                x2=d.x2,
                y2=d.y2,
                camera_id=packet.camera_id,
                pts_ms=packet.pts_ms,
            )
            for d in self.detections
        ]
        return DetectionResult(
            camera_id=packet.camera_id,
            pts_ms=packet.pts_ms,
            detections=current_detections,
            inference_time_ms=1.0,
            device="cpu",
            model_name="stub-detector",
            is_discontinuity=packet.is_discontinuity,
        )


def test_pipeline_to_dispatcher_integration():
    """
    Integration flow:
    Synthetic FramePacket -> CameraPipeline -> StubDetector / MockRecognizer -> PipelineResult -> Dispatcher -> MockWorker
    """
    # 1. Deterministic stub detector providing vehicle bounding box
    car_detection = Detection(
        class_id=2,
        class_name="car",
        confidence=0.95,
        x1=100.0,
        y1=100.0,
        x2=400.0,
        y2=300.0,
        camera_id="CAM-01",
        pts_ms=1000.0,
    )
    stub_detector = StubVehicleDetector(detections=[car_detection])
    stream_processor = StreamProcessor(
        detector=stub_detector,
        config=StreamProcessorConfig(frame_stride=1),
    )
    vehicle_tracker = VehicleTracker(config=TrackerConfig(min_hits=1))

    # 2. Mock recognizer returning valid Indian plate
    mock_candidate = PlateCandidate(
        raw_text="KA01AB1234",
        normalized_plate="KA01AB1234",
        confidence=0.94,
        is_valid_format=True,
        pts_ms=1000.0,
    )
    mock_recognizer = MockPlateRecognizer(default_candidate=mock_candidate)
    anpr_coordinator = ANPRCoordinator(
        recognizer=mock_recognizer,
        config=ANPRConfig(min_confidence=0.70, min_crop_quality=0.0),
    )

    # 3. Real CameraPipeline instance
    pipeline = CameraPipeline(
        camera_id="CAM-01",
        stream_processor=stream_processor,
        vehicle_tracker=vehicle_tracker,
        anpr_coordinator=anpr_coordinator,
    )

    # 4. Generate synthetic FramePacket
    frame = np.full((720, 1280, 3), 128, dtype=np.uint8)
    packet = FramePacket(
        frame=frame,
        pts_ms=1000.0,
        received_at=time.time(),
        width=1280,
        height=720,
        camera_id="CAM-01",
    )

    # 5. Process frame through CameraPipeline
    pipeline_result = pipeline.process_frame(packet)
    assert pipeline_result.has_detections is True
    assert pipeline_result.has_plates is True
    assert len(pipeline_result.anpr_results) > 0

    # 6. Dispatch to mock persistence worker
    mock_worker = MagicMock()
    mock_worker.enqueue.return_value = EnqueueStatus.ACCEPTED

    dispatcher = PersistenceDispatcher(worker=mock_worker)
    dispatch_res = dispatcher.dispatch(pipeline_result=pipeline_result)

    assert dispatch_res.received >= 1
    assert dispatch_res.enqueued >= 1
    assert dispatch_res.success is True

    mock_worker.enqueue.assert_called()
    call_kwargs = mock_worker.enqueue.call_args.kwargs
    assert call_kwargs["camera_code"] == "CAM-01"
    assert call_kwargs["anpr_result"].normalized_plate == "KA01AB1234"


# ---------------------------------------------------------------------------
# Integration Test 2 (Requirement 13):
# Synthetic ANPRResult -> Dispatcher -> Real ANPRPersistenceWorker -> Real ANPRPersistenceService -> SQLite DB
# ---------------------------------------------------------------------------
@pytest.fixture
def sqlite_test_db():
    """In-memory SQLite session factory for end-to-end worker persistence test."""
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
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed test camera
    session = session_factory()
    try:
        camera = Camera(
            camera_code="CAM-01",
            name="Main Gate",
            location="North",
            status="active",
        )
        session.add(camera)
        session.commit()
    finally:
        session.close()

    yield session_factory
    Base.metadata.drop_all(bind=engine)


def test_sqlite_worker_backed_end_to_end_persistence(sqlite_test_db):
    """
    Worker-backed SQLite integration test:
    Synthetic recognized ANPRResult -> Dispatcher -> Real ANPRPersistenceWorker -> Real ANPRPersistenceService -> SQLite
    -> Vehicle + Event + Alert
    """
    # 1. Initialize real ANPRPersistenceWorker with SQLite test session factory
    worker = ANPRPersistenceWorker(
        session_factory=sqlite_test_db,
        poll_timeout_sec=0.05,
    )
    worker.start()

    try:
        # 2. Initialize Dispatcher with watchlist configured for plate
        watchlist = ["MH12AB1234"]
        dispatcher = PersistenceDispatcher(worker=worker, default_watchlist=watchlist)

        # 3. Create synthetic recognized ANPRResult
        anpr = make_anpr_result(
            camera_id="CAM-01",
            normalized_plate="MH12AB1234",
            confidence=0.93,
        )

        # 4. Dispatch result
        dispatch_res = dispatcher.dispatch(
            anpr_results=[anpr],
            camera_code="CAM-01",
            snapshot_path="/snapshots/mh12ab1234.jpg",
        )
        assert dispatch_res.enqueued == 1
        assert dispatch_res.success is True

        # 5. Wait for worker queue to drain into database
        drained = worker.join(timeout=3.0)
        assert drained is True

        # 6. Verify SQLite database contains Vehicle, Event, and Alert
        session = sqlite_test_db()
        try:
            vehicle = session.query(Vehicle).filter(Vehicle.plate_number == "MH12AB1234").first()
            assert vehicle is not None

            event_record = session.query(Event).filter(Event.vehicle_id == vehicle.id).first()
            assert event_record is not None
            assert event_record.confidence == pytest.approx(0.93)
            assert event_record.snapshot_path == "/snapshots/mh12ab1234.jpg"

            alert_record = session.query(Alert).filter(Alert.vehicle_id == vehicle.id).first()
            assert alert_record is not None
            assert alert_record.vehicle_id == vehicle.id
            assert "MH12AB1234" in (alert_record.message or "")
            assert alert_record.camera_id == event_record.camera_id
        finally:
            session.close()
    finally:
        worker.stop(timeout=1.0)
