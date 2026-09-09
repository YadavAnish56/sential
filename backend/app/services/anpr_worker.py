"""
Asynchronous ANPR Persistence Worker — Decoupled background queue and persistence worker.

Consumes validated ANPR results from a bounded in-process queue, creates isolated database
sessions, invokes ANPRPersistenceService, and handles retries, errors, and backpressure safely
without blocking the frame-processing hot path.
"""

from __future__ import annotations

import logging
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

# Ensure Sentinel and backend directories are on sys.path
_sentinel_root = Path(__file__).resolve().parent.parent.parent.parent
_backend_dir = _sentinel_root / "backend"
for _path in [str(_sentinel_root), str(_backend_dir)]:
    if _path not in sys.path:
        sys.path.insert(0, _path)

from app.database.connection import SessionLocal
from app.services.anpr_service import ANPRPersistenceResult, ANPRPersistenceService

try:
    from ai_engine.anpr.schemas import ANPRResult
except ModuleNotFoundError:
    ANPRResult = Any  # type: ignore

logger = logging.getLogger("sentinel.anpr_worker")


class EnqueueStatus(str, Enum):
    """Status outcomes for non-blocking persistence queue enqueue operations."""

    ACCEPTED = "ACCEPTED"
    QUEUE_FULL = "QUEUE_FULL"
    STOPPED = "STOPPED"

    @property
    def success(self) -> bool:
        """Return True if successfully enqueued."""
        return self == EnqueueStatus.ACCEPTED

    @property
    def status(self) -> EnqueueStatus:
        """Return self for status-attribute compatibility."""
        return self

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True, frozen=True)
class ANPRQueueItem:
    """
    Self-contained payload for asynchronous ANPR persistence.

    Contains zero references to live SQLAlchemy Sessions or database connections.
    """

    anpr_result: ANPRResult | dict[str, Any]
    camera_code: str | None = None
    timestamp: datetime | None = None
    snapshot_path: str | None = None
    event_type: str = "anpr_detection"
    watchlist: tuple[str, ...] | None = None

    def __init__(
        self,
        anpr_result: ANPRResult | dict[str, Any],
        camera_code: str | None = None,
        timestamp: datetime | None = None,
        snapshot_path: str | None = None,
        event_type: str = "anpr_detection",
        watchlist: Iterable[str] | None = None,
    ) -> None:
        object.__setattr__(self, "anpr_result", anpr_result)
        object.__setattr__(self, "camera_code", camera_code)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "snapshot_path", snapshot_path)
        object.__setattr__(self, "event_type", event_type)
        wl = tuple(watchlist) if watchlist is not None else None
        object.__setattr__(self, "watchlist", wl)


def _safe_error_str(exc: Exception) -> str:
    """Return sanitized exception message, stripping credentials and sensitive connection data."""
    raw = str(exc)
    cleaned = re.sub(r":[^:@\s]+@", ":***@", raw)
    cleaned = re.sub(r"(password|passwd|secret|token)\s*=\s*\S+", r"\1=***", cleaned, flags=re.IGNORECASE)
    return f"{type(exc).__name__}: {cleaned}"


class ANPRPersistenceWorker:
    """
    Dedicated background worker consuming ANPR results from a bounded in-process queue.

    Guarantees:
    - Zero blocking of video-processing callers.
    - Isolated database sessions (created, used, and closed per operation).
    - Controlled QUEUE_FULL backpressure reporting.
    - Bounded retries with backoff for transient database errors.
    - Clean thread lifecycle with explicit start(), stop(), and join().
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        max_queue_size: int = 1000,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.05,
        persistence_service_factory: Callable[[Session], ANPRPersistenceService] | None = None,
        poll_timeout_sec: float = 0.1,
    ) -> None:
        """
        Initialize persistence worker.

        Args:
            session_factory: Factory producing fresh SQLAlchemy Sessions (defaults to SessionLocal).
            max_queue_size: Capacity limit of the in-process queue (bounded backpressure).
            max_retries: Maximum number of persistence retries on transient errors.
            retry_backoff_sec: Base backoff delay in seconds between retries.
            persistence_service_factory: Factory producing ANPRPersistenceService instances.
            poll_timeout_sec: Worker thread queue polling timeout in seconds.
        """
        self.session_factory = session_factory or SessionLocal
        self.max_queue_size = max(1, max_queue_size)
        self.max_retries = max(0, max_retries)
        self.retry_backoff_sec = max(0.0, retry_backoff_sec)
        self.persistence_service_factory = persistence_service_factory or ANPRPersistenceService
        self.poll_timeout_sec = max(0.01, poll_timeout_sec)

        self._queue: queue.Queue[ANPRQueueItem] = queue.Queue(maxsize=self.max_queue_size)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def qsize(self) -> int:
        """Return current approximate number of items in the queue."""
        return self._queue.qsize()

    def is_alive(self) -> bool:
        """Return True if the background worker thread is currently running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """
        Start the background persistence worker thread.

        Idempotent: calling start() on an already running worker is a safe no-op.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"ANPRPersistenceWorker-{id(self)}",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "ANPRPersistenceWorker started (max_queue_size=%d, max_retries=%d)",
                self.max_queue_size,
                self.max_retries,
            )

    def stop(self, timeout: float | None = 5.0, drain: bool = False) -> None:
        """
        Signal the worker thread to stop and optionally wait for termination.

        Args:
            timeout: Maximum seconds to wait for worker thread to terminate.
            drain: If True, waits for remaining queue items to be processed before stopping.
        """
        if drain:
            self.join(timeout=timeout)

        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            logger.info("ANPRPersistenceWorker stopped cleanly")

    def join(self, timeout: float | None = None) -> bool:
        """
        Wait until all items currently in the queue have been processed.

        Args:
            timeout: Optional maximum wait time in seconds. If None, blocks until complete.

        Returns:
            True if all tasks were completed, False if timeout expired.
        """
        if timeout is None:
            self._queue.join()
            return True

        deadline = time.monotonic() + timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._queue.all_tasks_done.wait(remaining)
            return True

    def enqueue(
        self,
        item: ANPRQueueItem | None = None,
        *,
        anpr_result: ANPRResult | dict[str, Any] | None = None,
        camera_code: str | None = None,
        timestamp: datetime | None = None,
        snapshot_path: str | None = None,
        event_type: str = "anpr_detection",
        watchlist: Iterable[str] | None = None,
    ) -> EnqueueStatus:
        """
        Non-blocking enqueue of an ANPR persistence item.

        Accepts either an existing ANPRQueueItem or keyword arguments.
        Never blocks the caller. If the queue is at capacity, returns EnqueueStatus.QUEUE_FULL.
        """
        if item is None:
            if anpr_result is None:
                raise ValueError("Must provide either 'item' (ANPRQueueItem) or 'anpr_result'")
            item = ANPRQueueItem(
                anpr_result=anpr_result,
                camera_code=camera_code,
                timestamp=timestamp,
                snapshot_path=snapshot_path,
                event_type=event_type,
                watchlist=watchlist,
            )

        if self._stop_event.is_set():
            logger.warning(
                "Worker stopped; refusing persistence item for camera=%s",
                item.camera_code,
                extra={"camera_code": item.camera_code, "queue_size": self._queue.qsize()},
            )
            return EnqueueStatus.STOPPED

        try:
            self._queue.put_nowait(item)
            return EnqueueStatus.ACCEPTED
        except queue.Full:
            target_camera = item.camera_code
            if not target_camera and hasattr(item.anpr_result, "camera_id"):
                target_camera = getattr(item.anpr_result, "camera_id", None)
            elif not target_camera and isinstance(item.anpr_result, dict):
                target_camera = item.anpr_result.get("camera_id")

            logger.warning(
                "ANPR persistence queue full (size=%d/%d). Dropping persistence item for camera=%s",
                self._queue.qsize(),
                self.max_queue_size,
                target_camera,
                extra={
                    "camera_code": target_camera,
                    "queue_size": self._queue.qsize(),
                    "max_queue_size": self.max_queue_size,
                },
            )
            return EnqueueStatus.QUEUE_FULL

    def _run(self) -> None:
        """Worker main loop running on dedicated background thread."""
        while not self._stop_event.is_set():
            try:
                item = self._queue.get(timeout=self.poll_timeout_sec)
            except queue.Empty:
                continue

            try:
                self._process_item(item)
            finally:
                self._queue.task_done()

    def _process_item(self, item: ANPRQueueItem) -> None:
        """Process a single queue item with dedicated DB session and bounded retry."""
        camera_code = item.camera_code
        if not camera_code and hasattr(item.anpr_result, "camera_id"):
            camera_code = getattr(item.anpr_result, "camera_id", None)
        elif not camera_code and isinstance(item.anpr_result, dict):
            camera_code = item.anpr_result.get("camera_id")

        attempts = 0
        while attempts <= self.max_retries:
            session: Session | None = None
            try:
                session = self.session_factory()
                service = self.persistence_service_factory(session)
                service.persist_result(
                    anpr_result=item.anpr_result,
                    camera_code=item.camera_code,
                    timestamp=item.timestamp,
                    snapshot_path=item.snapshot_path,
                    event_type=item.event_type,
                    watchlist=item.watchlist,
                )
                # Success
                return
            except Exception as exc:
                attempts += 1
                safe_err = _safe_error_str(exc)
                if attempts <= self.max_retries and not self._stop_event.is_set():
                    backoff = self.retry_backoff_sec * (2 ** (attempts - 1))
                    logger.warning(
                        "Persistence attempt %d/%d failed for camera=%s: %s. Retrying in %.2fs",
                        attempts,
                        self.max_retries + 1,
                        camera_code,
                        safe_err,
                        backoff,
                        extra={
                            "camera_code": camera_code,
                            "attempt": attempts,
                            "max_retries": self.max_retries,
                            "error": safe_err,
                        },
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "Persistence failed after %d attempt(s) for camera=%s: %s. Discarding queue item.",
                        attempts,
                        camera_code,
                        safe_err,
                        extra={
                            "camera_code": camera_code,
                            "attempt": attempts,
                            "max_retries": self.max_retries,
                            "error": safe_err,
                        },
                    )
                    return
            finally:
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass
