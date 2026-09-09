"""
PTS (Presentation Time Stamp) and Video Timing Isolation Module.

CRITICAL REQUIREMENTS:
- Preserve source timing from capture backend (e.g., CAP_PROP_POS_MSEC).
- Never use CAP_PROP_FPS as authoritative video timing.
- Never calculate video time from frame arrival wall-clock time.
- Tolerate irregular frame intervals without crashing.
- Do not assume constant FPS or uniform intervals.
- Handle missing or invalid PTS gracefully.
- Detect abnormal PTS jumps and stream loops/resets.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger("sentinel.streaming.pts")

# Defaults for jump/irregularity thresholds
DEFAULT_MAX_NORMAL_INTERVAL_MS: Final[float] = 150.0  # >150ms implies noticeable delay/irregularity at ~25-30fps
DEFAULT_PTS_JUMP_THRESHOLD_MS: Final[float] = 3000.0   # >3s jump forward is flagged as discontinuity
DEFAULT_RESET_THRESHOLD_MS: Final[float] = -500.0      # PTS dropping by >500ms indicates loop/reset


@dataclass
class PTSInfo:
    """Calculated presentation timestamp metadata for a single frame."""
    pts_ms: float | None
    is_valid: bool
    delta_ms: float | None = None
    is_jump: bool = False
    is_loop_or_reset: bool = False
    is_irregular: bool = False
    raw_pts: float | None = None


class PTSTracker:
    """
    Stateful tracker and validator for frame presentation timestamps.

    Maintains sequence statistics, detects discontinuities (jumps, resets/loops, gaps),
    and ensures video timing remains strictly derived from the stream source.
    """

    def __init__(
        self,
        max_normal_interval_ms: float = DEFAULT_MAX_NORMAL_INTERVAL_MS,
        pts_jump_threshold_ms: float = DEFAULT_PTS_JUMP_THRESHOLD_MS,
        reset_threshold_ms: float = DEFAULT_RESET_THRESHOLD_MS,
    ) -> None:
        self.max_normal_interval_ms = max_normal_interval_ms
        self.pts_jump_threshold_ms = pts_jump_threshold_ms
        self.reset_threshold_ms = reset_threshold_ms

        # Sequence state
        self._last_pts_ms: float | None = None
        self._first_pts_ms: float | None = None
        self._frame_count: int = 0
        self._missing_pts_count: int = 0
        self._irregular_count: int = 0
        self._jumps_count: int = 0
        self._resets_count: int = 0

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def missing_pts_count(self) -> int:
        return self._missing_pts_count

    @property
    def irregular_count(self) -> int:
        return self._irregular_count

    @property
    def jumps_count(self) -> int:
        return self._jumps_count

    @property
    def resets_count(self) -> int:
        return self._resets_count

    @property
    def last_pts_ms(self) -> float | None:
        return self._last_pts_ms

    def reset(self) -> None:
        """Reset sequence tracker state (e.g. after stream reconnect)."""
        self._last_pts_ms = None
        self._first_pts_ms = None
        self._frame_count = 0
        self._missing_pts_count = 0
        self._irregular_count = 0
        self._jumps_count = 0
        self._resets_count = 0

    def process(self, raw_pts: float | int | None) -> PTSInfo:
        """
        Process raw PTS returned from capture backend.

        Validates timestamp, computes delta against previous frame, and flags
        irregular intervals, jumps, or stream loop resets.
        """
        self._frame_count += 1

        # Check for invalid / missing PTS
        if raw_pts is None or math.isnan(raw_pts) or math.isinf(raw_pts) or raw_pts < 0:
            self._missing_pts_count += 1
            return PTSInfo(
                pts_ms=None,
                is_valid=False,
                delta_ms=None,
                is_jump=False,
                is_loop_or_reset=False,
                is_irregular=False,
                raw_pts=raw_pts if isinstance(raw_pts, (int, float)) and not (math.isnan(raw_pts) or math.isinf(raw_pts)) else None,
            )

        pts_ms = float(raw_pts)

        if self._first_pts_ms is None:
            self._first_pts_ms = pts_ms

        delta_ms: float | None = None
        is_jump = False
        is_loop_or_reset = False
        is_irregular = False

        if self._last_pts_ms is not None:
            delta_ms = pts_ms - self._last_pts_ms

            # Check for backwards timestamp (stream looped or restarted)
            if delta_ms < self.reset_threshold_ms:
                is_loop_or_reset = True
                self._resets_count += 1
                logger.info(
                    "PTS loop/reset detected: last=%.2f ms, current=%.2f ms (delta=%.2f ms)",
                    self._last_pts_ms, pts_ms, delta_ms
                )
            # Check for abnormal forward jump
            elif delta_ms > self.pts_jump_threshold_ms:
                is_jump = True
                self._jumps_count += 1
                logger.warning(
                    "PTS forward jump detected: last=%.2f ms, current=%.2f ms (delta=%.2f ms)",
                    self._last_pts_ms, pts_ms, delta_ms
                )
            # Check for irregular interval (gap or burst)
            elif delta_ms > self.max_normal_interval_ms or delta_ms <= 0:
                is_irregular = True
                self._irregular_count += 1

        self._last_pts_ms = pts_ms

        return PTSInfo(
            pts_ms=pts_ms,
            is_valid=True,
            delta_ms=delta_ms,
            is_jump=is_jump,
            is_loop_or_reset=is_loop_or_reset,
            is_irregular=is_irregular,
            raw_pts=raw_pts,
        )
