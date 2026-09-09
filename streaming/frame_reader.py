"""
Frame Reader and Packet Abstraction for Sentinel.

Consumes frames from the RTSPClient, associates presentation timestamps via
the PTSTracker, attaches operational arrival metadata, and produces clean,
decoupled FramePacket instances for downstream AI processing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from streaming.pts import PTSTracker
from streaming.rtsp_client import RTSPClient

logger = logging.getLogger("sentinel.streaming.frame_reader")


@dataclass(slots=True)
class FramePacket:
    """
    Decoupled representation of a single video frame.

    IMPORTANT:
    `pts_ms` is the source video presentation timestamp.
    `received_at` is strictly operational wall-clock arrival time for health/metrics
    and must NEVER be used as the authoritative video timestamp.
    """
    frame: Any                          # np.ndarray
    pts_ms: float | None                # Source presentation time in ms (CAP_PROP_POS_MSEC)
    received_at: float                  # System UTC timestamp (time.time())
    width: int
    height: int
    camera_id: str
    codec: str | None = None
    pts_delta_ms: float | None = None   # Delta since previous frame PTS
    is_irregular: bool = False          # True if frame gap/burst detected
    is_discontinuity: bool = False      # True if timestamp jump or loop reset detected


class FrameReader:
    """
    Reads frames from an RTSPClient, isolates video presentation timing,
    and handles intermittent read failures and irregular intervals.
    """

    def __init__(
        self,
        client: RTSPClient,
        pts_tracker: PTSTracker | None = None,
    ) -> None:
        self.client = client
        self.pts_tracker = pts_tracker or PTSTracker()
        self._consecutive_read_failures: int = 0
        self._total_frames_read: int = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_read_failures

    @property
    def total_frames_read(self) -> int:
        return self._total_frames_read

    def read_packet(self) -> tuple[bool, FramePacket | None]:
        """
        Read the next frame and package it into a FramePacket.

        Returns:
            (True, FramePacket) on successful frame read.
            (False, None) if frame read failed or end of stream.
        """
        success, raw_frame, raw_pts = self.client.read_frame()
        arrival_time = time.time()

        if not success or raw_frame is None:
            self._consecutive_read_failures += 1
            return False, None

        self._consecutive_read_failures = 0
        self._total_frames_read += 1

        # Process source PTS (independent of arrival time)
        pts_info = self.pts_tracker.process(raw_pts)

        h, w = (
            raw_frame.shape[0],
            raw_frame.shape[1],
        ) if hasattr(raw_frame, "shape") and len(raw_frame.shape) >= 2 else (
            self.client.height or 0,
            self.client.width or 0,
        )

        packet = FramePacket(
            frame=raw_frame,
            pts_ms=pts_info.pts_ms,
            received_at=arrival_time,
            width=w,
            height=h,
            camera_id=self.client.camera_id,
            codec=self.client.codec,
            pts_delta_ms=pts_info.delta_ms,
            is_irregular=pts_info.is_irregular,
            is_discontinuity=(pts_info.is_jump or pts_info.is_loop_or_reset),
        )
        return True, packet

    def reset(self) -> None:
        """Reset internal failure counters and timing tracker."""
        self._consecutive_read_failures = 0
        self._total_frames_read = 0
        self.pts_tracker.reset()
