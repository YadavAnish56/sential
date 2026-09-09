"""
Stream Processor and Inference Bridge for Sentinel AI Engine.

Bridges streaming FramePacket objects to VehicleDetector inference with:
- Configurable frame stride and PTS-based target FPS sampling
- Error isolation so detector failures never crash the stream ingestion loop
- Strict preservation of camera_id and source PTS
- Discontinuity propagation for downstream tracking state management
- Telemetry accounting for frames received, processed, skipped, and errors
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from streaming.frame_reader import FramePacket

try:
    from .schemas import DetectionResult
except ImportError:
    from schemas import DetectionResult


logger = logging.getLogger("sentinel.ai_engine.stream_processor")


@dataclass
class StreamProcessorConfig:
    """
    Configuration for StreamProcessor frame sampling and inference control.

    Attributes:
        frame_stride: Process 1 of every N frames (1 = process every frame).
        target_fps: Optional target FPS sampling based on source video PTS (pts_ms).
                    If specified, frames closer together than (1000.0 / target_fps) ms
                    are skipped.
    """
    frame_stride: int = 1
    target_fps: float | None = None

    def __post_init__(self) -> None:
        if self.frame_stride < 1:
            self.frame_stride = 1
        if self.target_fps is not None and self.target_fps <= 0:
            self.target_fps = None


class StreamProcessor:
    """
    Decoupled processing bridge between streaming FramePacket objects
    and VehicleDetector inference.
    """

    def __init__(
        self,
        detector: Any,
        config: StreamProcessorConfig | None = None,
    ) -> None:
        self.detector = detector
        self.config = config or StreamProcessorConfig()

        # Operational telemetry counters
        self.frames_received: int = 0
        self.frames_processed: int = 0
        self.frames_skipped: int = 0
        self.inference_errors: int = 0

        self._last_processed_pts_ms: float | None = None

    def should_process(self, packet: FramePacket) -> bool:
        """
        Determine if the incoming frame should be processed based on configured sampling.

        Rules:
        1. If packet indicates a stream discontinuity (reset/jump), always process
           to ensure downstream models and trackers can re-anchor.
        2. If target_fps is configured and pts_ms is valid, sample by PTS timestamp delta.
        3. Otherwise, sample by frame_stride counter.
        """
        # Discontinuity: always process and reset PTS reference
        if getattr(packet, "is_discontinuity", False):
            self._last_processed_pts_ms = None
            return True

        # PTS-based target FPS sampling
        if self.config.target_fps is not None and self.config.target_fps > 0:
            if packet.pts_ms is not None and self._last_processed_pts_ms is not None:
                min_interval_ms = 1000.0 / self.config.target_fps
                delta = packet.pts_ms - self._last_processed_pts_ms
                # If negative delta without discontinuity flag, re-anchor
                if delta < 0:
                    return True
                if delta < min_interval_ms:
                    return False
            return True

        # Frame stride sampling
        if self.config.frame_stride > 1:
            if (self.frames_received - 1) % self.config.frame_stride != 0:
                return False

        return True

    def process_packet(self, packet: FramePacket) -> DetectionResult | None:
        """
        Process a single FramePacket through the detector with sampling and error isolation.

        Parameters:
            packet: FramePacket instance from the streaming module.

        Returns:
            DetectionResult on successful inference, or None if skipped/errored.
        """
        self.frames_received += 1

        if not self.should_process(packet):
            self.frames_skipped += 1
            return None

        try:
            result = self.detector.detect(packet)
            self.frames_processed += 1

            if result is not None:
                # Propagate discontinuity flag to result
                if getattr(packet, "is_discontinuity", False):
                    result.is_discontinuity = True

                # Anchor last processed PTS
                if packet.pts_ms is not None:
                    self._last_processed_pts_ms = packet.pts_ms

            return result

        except Exception as exc:
            self.inference_errors += 1
            logger.error(
                "Detector error on camera %s (PTS %s): %s",
                getattr(packet, "camera_id", "unknown"),
                getattr(packet, "pts_ms", None),
                exc,
                exc_info=True,
            )
            return None

    def reset_stats(self) -> None:
        """Reset operational telemetry counters and sampling state."""
        self.frames_received = 0
        self.frames_processed = 0
        self.frames_skipped = 0
        self.inference_errors = 0
        self._last_processed_pts_ms = None

    def get_stats(self) -> dict[str, int]:
        """Return operational telemetry dictionary."""
        return {
            "frames_received": self.frames_received,
            "frames_processed": self.frames_processed,
            "frames_skipped": self.frames_skipped,
            "inference_errors": self.inference_errors,
        }
