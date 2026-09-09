"""
Persistence Dispatcher — Bridge between CameraPipeline and ANPRPersistenceWorker.

Dispatches validated ANPR results from pipeline execution passes into the bounded
in-process persistence queue without database dependencies or frame-processing blocking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

try:
    from ai_engine.anpr.schemas import ANPRResult
    from ai_engine.pipeline import PipelineResult
except ImportError:
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from ai_engine.anpr.schemas import ANPRResult
    from ai_engine.pipeline import PipelineResult

logger = logging.getLogger("sentinel.ai_engine.dispatcher")


@dataclass(slots=True)
class DispatchResult:
    """
    Lightweight per-dispatch outcome telemetry.

    Attributes:
        received: Total number of ANPRResult objects evaluated.
        eligible: Number of results satisfying persistence eligibility criteria.
        enqueued: Number of results successfully accepted into the worker queue.
        skipped: Number of results filtered out due to ineligibility (noise/failed).
        queue_full: Number of eligible results dropped due to worker queue capacity.
        errors: Error descriptions encountered during dispatch attempts.
    """

    received: int = 0
    eligible: int = 0
    enqueued: int = 0
    skipped: int = 0
    queue_full: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """True if all eligible results were enqueued with zero queue drops or errors."""
        return self.queue_full == 0 and len(self.errors) == 0

    @property
    def has_backpressure(self) -> bool:
        """True if any eligible result encountered QUEUE_FULL backpressure."""
        return self.queue_full > 0

    def to_dict(self) -> dict[str, Any]:
        """Convert telemetry to a JSON-serializable dictionary."""
        return {
            "received": self.received,
            "eligible": self.eligible,
            "enqueued": self.enqueued,
            "skipped": self.skipped,
            "queue_full": self.queue_full,
            "errors": list(self.errors),
            "success": self.success,
            "has_backpressure": self.has_backpressure,
        }


def is_eligible_for_persistence(anpr_result: ANPRResult | dict[str, Any] | None) -> bool:
    """
    Evaluate whether an ANPR result meets eligibility criteria for database persistence.

    Excludes:
    - NO_PLATE_DETECTED
    - LOW_CONFIDENCE
    - FAILED
    - INVALID_FORMAT
    - None or empty plates

    Only RECOGNIZED results conforming to valid Indian registration plate format are eligible.
    """
    if anpr_result is None:
        return False

    if isinstance(anpr_result, ANPRResult):
        status = anpr_result.status
        is_valid_format = anpr_result.is_valid_format
        normalized_plate = anpr_result.normalized_plate
    elif isinstance(anpr_result, dict):
        status = anpr_result.get("status")
        is_valid_format = bool(anpr_result.get("is_valid_format", False))
        normalized_plate = anpr_result.get("normalized_plate", "")
    else:
        return False

    if status != "RECOGNIZED":
        return False
    if not is_valid_format:
        return False
    if not normalized_plate or not str(normalized_plate).strip():
        return False

    return True


class PersistenceDispatcher:
    """
    Database-agnostic dispatcher forwarding eligible ANPR results from CameraPipeline
    into an ANPRPersistenceWorker queue.

    Guarantees:
    - Zero database imports (SQLAlchemy, SessionLocal, PostgreSQL).
    - Pre-filters non-eligible noise (LOW_CONFIDENCE, NO_PLATE_DETECTED, INVALID_FORMAT, FAILED).
    - Never blocks the video-processing loop when the worker queue is full.
    - Preserves ANPRResult, pts_ms, normalized plate, confidence, and track_id without mutation.
    - Never converts pts_ms into a wall-clock datetime.
    - Explicit worker lifecycle: does not start or stop worker threads behind the scenes.
    """

    def __init__(
        self,
        worker: Any,
        default_watchlist: Iterable[str] | None = None,
        default_event_type: str = "anpr_detection",
    ) -> None:
        """
        Initialize persistence dispatcher.

        Args:
            worker: ANPRPersistenceWorker instance (or compatible test double).
            default_watchlist: Optional default collection of watchlist plate strings.
            default_event_type: Default event classification type.
        """
        self.worker = worker
        self.default_watchlist = tuple(default_watchlist) if default_watchlist is not None else None
        self.default_event_type = default_event_type

    def dispatch(
        self,
        pipeline_result: PipelineResult | None = None,
        *,
        anpr_results: list[ANPRResult] | None = None,
        camera_code: str | None = None,
        timestamp: datetime | None = None,
        snapshot_path: str | None = None,
        event_type: str | None = None,
        watchlist: Iterable[str] | None = None,
    ) -> DispatchResult:
        """
        Evaluate and dispatch eligible ANPR results to the persistence worker.

        Args:
            pipeline_result: PipelineResult from a CameraPipeline frame pass.
            anpr_results: Alternative direct list of ANPRResult objects (if pipeline_result omitted).
            camera_code: Camera code identifier (falls back to pipeline_result.camera_id).
            timestamp: Wall-clock UTC datetime for persistence (never derived from pts_ms).
            snapshot_path: Optional storage path or URL to detection snapshot.
            event_type: Event classification type override.
            watchlist: Watchlist plate strings override for alert matching.

        Returns:
            DispatchResult containing counts of received, eligible, enqueued, skipped, and dropped items.
        """
        dispatch_result = DispatchResult()

        if pipeline_result is not None:
            results_to_process = pipeline_result.anpr_results
            source_camera = camera_code or pipeline_result.camera_id
        else:
            results_to_process = anpr_results or []
            source_camera = camera_code

        target_event_type = event_type or self.default_event_type
        target_watchlist = watchlist if watchlist is not None else self.default_watchlist

        for anpr in results_to_process:
            dispatch_result.received += 1

            if not is_eligible_for_persistence(anpr):
                dispatch_result.skipped += 1
                continue

            dispatch_result.eligible += 1

            # Resolve camera identity for this item
            item_camera = source_camera
            if not item_camera and hasattr(anpr, "camera_id"):
                item_camera = getattr(anpr, "camera_id", None)
            elif not item_camera and isinstance(anpr, dict):
                item_camera = anpr.get("camera_id")

            try:
                enqueue_status = self.worker.enqueue(
                    anpr_result=anpr,
                    camera_code=item_camera,
                    timestamp=timestamp,
                    snapshot_path=snapshot_path,
                    event_type=target_event_type,
                    watchlist=target_watchlist,
                )

                if enqueue_status == "ACCEPTED" or getattr(enqueue_status, "name", "") == "ACCEPTED" or getattr(enqueue_status, "success", False):
                    dispatch_result.enqueued += 1
                elif enqueue_status == "QUEUE_FULL" or str(enqueue_status) == "QUEUE_FULL":
                    dispatch_result.queue_full += 1
                    logger.warning(
                        "Persistence queue full (QUEUE_FULL) for camera=%s. Dropping ANPR item.",
                        item_camera,
                        extra={"camera_code": item_camera, "status": "QUEUE_FULL"},
                    )
                elif enqueue_status == "STOPPED" or str(enqueue_status) == "STOPPED":
                    err_msg = f"Persistence worker stopped (status={enqueue_status})"
                    dispatch_result.errors.append(err_msg)
                    logger.warning("%s for camera=%s", err_msg, item_camera)
                else:
                    dispatch_result.queue_full += 1
                    logger.warning(
                        "Persistence worker rejected item with status=%s for camera=%s",
                        enqueue_status,
                        item_camera,
                    )
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                dispatch_result.errors.append(err_msg)
                logger.error(
                    "Unexpected error during persistence dispatch for camera=%s: %s",
                    item_camera,
                    err_msg,
                    extra={"camera_code": item_camera, "error": err_msg},
                )

        return dispatch_result


def dispatch_pipeline_results(
    pipeline_result: PipelineResult,
    worker: Any,
    camera_code: str | None = None,
    timestamp: datetime | None = None,
    snapshot_path: str | None = None,
    event_type: str = "anpr_detection",
    watchlist: Iterable[str] | None = None,
) -> DispatchResult:
    """
    Convenience functional helper to dispatch a PipelineResult to a worker.
    """
    dispatcher = PersistenceDispatcher(
        worker=worker,
        default_watchlist=watchlist,
        default_event_type=event_type,
    )
    return dispatcher.dispatch(
        pipeline_result=pipeline_result,
        camera_code=camera_code,
        timestamp=timestamp,
        snapshot_path=snapshot_path,
        event_type=event_type,
        watchlist=watchlist,
    )
