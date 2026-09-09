"""
Stream Health and Operational State Monitoring for Sentinel.

Requirements:
- Track status: connecting, online, reconnecting, offline, error.
- Track last_seen, last_frame, last_pts, reconnect_attempts, error information.
- Use a reasonable failure threshold (do not mark offline on a single transient frame drop).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("sentinel.streaming.health")


class StreamStatus(str, Enum):
    CONNECTING = "connecting"
    ONLINE = "online"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass
class StreamHealth:
    """Snapshot of current camera stream health and metrics."""
    camera_id: str
    status: StreamStatus = StreamStatus.OFFLINE
    last_seen: float | None = None
    last_frame_number: int = 0
    last_pts_ms: float | None = None
    consecutive_failures: int = 0
    reconnect_attempts: int = 0
    error_message: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "status": self.status.value,
            "last_seen": self.last_seen,
            "last_frame_number": self.last_frame_number,
            "last_pts_ms": self.last_pts_ms,
            "consecutive_failures": self.consecutive_failures,
            "reconnect_attempts": self.reconnect_attempts,
            "error_message": self.error_message,
            "properties": self.properties,
        }


class StreamHealthTracker:
    """
    Tracks and updates operational health states for a camera stream.
    Applies failure thresholds to prevent flapping on occasional dropped frames.
    """

    def __init__(
        self,
        camera_id: str,
        failure_threshold: int = 5,
        stale_timeout_sec: float = 10.0,
    ) -> None:
        self.camera_id = camera_id
        self.failure_threshold = failure_threshold
        self.stale_timeout_sec = stale_timeout_sec

        self._health = StreamHealth(camera_id=camera_id)

    @property
    def current(self) -> StreamHealth:
        return self._health

    @property
    def status(self) -> StreamStatus:
        return self._health.status

    def set_connecting(self) -> None:
        """Mark stream as actively connecting."""
        self._health.status = StreamStatus.CONNECTING
        self._health.error_message = None
        logger.info("Camera %s status changed to CONNECTING", self.camera_id)

    def set_online(self, stream_props: dict[str, Any] | None = None) -> None:
        """Mark stream as established and online."""
        self._health.status = StreamStatus.ONLINE
        self._health.consecutive_failures = 0
        self._health.error_message = None
        if stream_props:
            self._health.properties.update(stream_props)
        logger.info("Camera %s status changed to ONLINE", self.camera_id)

    def record_frame(
        self,
        frame_number: int,
        pts_ms: float | None,
        now: float | None = None,
    ) -> None:
        """
        Record a successfully received and parsed frame.
        Guarantees status is ONLINE and clears transient failure counters.
        """
        current_time = now if now is not None else time.time()
        self._health.last_seen = current_time
        self._health.last_frame_number = frame_number
        self._health.last_pts_ms = pts_ms
        self._health.consecutive_failures = 0

        if self._health.status != StreamStatus.ONLINE:
            self._health.status = StreamStatus.ONLINE
            self._health.error_message = None

    def record_read_failure(self, error: str | None = None) -> None:
        """
        Record a frame read failure.
        Only transitions state to RECONNECTING/ERROR if consecutive failures
        exceed the configured failure_threshold.
        """
        self._health.consecutive_failures += 1

        if self._health.consecutive_failures >= self.failure_threshold:
            prev_status = self._health.status
            self._health.status = StreamStatus.ERROR
            self._health.error_message = error or f"Exceeded {self.failure_threshold} consecutive read failures"
            if prev_status != StreamStatus.ERROR:
                logger.warning(
                    "Camera %s degraded to ERROR (%d consecutive failures): %s",
                    self.camera_id, self._health.consecutive_failures, self._health.error_message
                )
        else:
            logger.debug(
                "Camera %s transient read failure (%d/%d)",
                self.camera_id, self._health.consecutive_failures, self.failure_threshold
            )

    def set_reconnecting(self, attempt: int) -> None:
        """Mark stream as attempting reconnection."""
        self._health.status = StreamStatus.RECONNECTING
        self._health.reconnect_attempts = attempt
        logger.info("Camera %s status changed to RECONNECTING (attempt %d)", self.camera_id, attempt)

    def set_offline(self, reason: str | None = None) -> None:
        """Mark stream as intentionally stopped or offline."""
        self._health.status = StreamStatus.OFFLINE
        self._health.error_message = reason
        logger.info("Camera %s status changed to OFFLINE (%s)", self.camera_id, reason or "stopped")

    def check_stale(self, now: float | None = None) -> bool:
        """
        Check if the stream has stalled without frames beyond stale_timeout_sec.
        If stalled while online, transitions to ERROR.
        """
        if self._health.status != StreamStatus.ONLINE or self._health.last_seen is None:
            return False

        current_time = now if now is not None else time.time()
        elapsed = current_time - self._health.last_seen
        if elapsed > self.stale_timeout_sec:
            self._health.status = StreamStatus.ERROR
            self._health.error_message = f"Stream stalled: no frames received for {elapsed:.1f}s"
            logger.warning("Camera %s stream stalled (%s)", self.camera_id, self._health.error_message)
            return True
        return False
