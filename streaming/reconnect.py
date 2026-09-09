"""
Reconnect Manager with Exponential Backoff for Sentinel Streaming.

Requirements:
- Initial backoff ~2s, max ~30s (2s -> 4s -> 8s -> 16s -> 30s -> 30s...).
- Never tight-loop reconnect.
- Reset backoff on successful connection.
- Cleanly release capture before reconnecting.
- Fully testable without mandatory real-time delays.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

logger = logging.getLogger("sentinel.streaming.reconnect")


class ReconnectManager:
    """
    Manages connection retries and exponential backoff timing for streaming clients.
    """

    def __init__(
        self,
        camera_id: str = "unknown",
        initial_delay: float = 2.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
    ) -> None:
        self.camera_id = camera_id
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

        self._attempts: int = 0
        self._current_delay: float = initial_delay
        self._last_attempt_time: float | None = None
        self._is_reconnecting: bool = False

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def current_delay(self) -> float:
        return self._current_delay

    @property
    def is_reconnecting(self) -> bool:
        return self._is_reconnecting

    def calculate_delay(self, attempt: int) -> float:
        """Calculate the backoff delay for a given attempt index (0-indexed)."""
        delay = self.initial_delay * (self.backoff_factor ** attempt)
        return min(delay, self.max_delay)

    def record_failure(self, now: float | None = None) -> float:
        """
        Record a failed connection attempt and advance backoff.
        Returns the delay to wait before the next attempt.
        """
        current_time = now if now is not None else time.time()
        self._last_attempt_time = current_time
        self._is_reconnecting = True

        delay = self.calculate_delay(self._attempts)
        self._current_delay = delay
        self._attempts += 1

        logger.warning(
            "Camera %s connection failed (attempt %d). Backoff delay: %.1fs",
            self.camera_id, self._attempts, delay
        )
        return delay

    def record_success(self) -> None:
        """Reset backoff counters upon successful connection."""
        if self._attempts > 0:
            logger.info(
                "Camera %s reconnected successfully after %d attempts. Backoff reset.",
                self.camera_id, self._attempts
            )
        self._attempts = 0
        self._current_delay = self.initial_delay
        self._is_reconnecting = False
        self._last_attempt_time = None

    def can_attempt_now(self, now: float | None = None) -> bool:
        """
        Check if sufficient backoff time has elapsed to attempt connection.
        Non-blocking check for event loops or polling.
        """
        if self._last_attempt_time is None:
            return True
        current_time = now if now is not None else time.time()
        return (current_time - self._last_attempt_time) >= self._current_delay

    def wait_backoff(self, sleep_fn: Callable[[float], None] | None = None) -> None:
        """Wait for the current backoff delay."""
        sleeper = sleep_fn or time.sleep
        delay = self.calculate_delay(max(0, self._attempts - 1))
        sleeper(delay)

    def reconnect(
        self,
        connect_fn: Callable[[], bool],
        release_fn: Callable[[], None] | None = None,
        max_attempts: int | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> bool:
        """
        Execute reconnect loop with exponential backoff until success or max_attempts.
        Guarantees clean release of prior resources and never tight-loops.
        """
        sleeper = sleep_fn or time.sleep

        while max_attempts is None or self._attempts < max_attempts:
            # Cleanly release any failed capture first
            if release_fn is not None:
                try:
                    release_fn()
                except Exception as e:
                    logger.debug("Error in release_fn before reconnect: %s", e)

            delay = self.record_failure()
            sleeper(delay)

            logger.info(
                "Attempting reconnect for camera %s (attempt %d)...",
                self.camera_id, self._attempts
            )

            try:
                if connect_fn():
                    self.record_success()
                    return True
            except Exception as e:
                logger.error("Exception during connect_fn for camera %s: %s", self.camera_id, e)

        logger.error(
            "Max reconnect attempts (%s) reached for camera %s",
            max_attempts, self.camera_id
        )
        return False
