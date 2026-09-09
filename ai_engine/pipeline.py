"""
Single-Camera AI Pipeline Coordinator for Sentinel AI Engine (Phase 6D.2).

Chains existing AI components into a unified per-camera processing pipeline:

    FramePacket
        → StreamProcessor  (sampling + detection)
        → VehicleTracker   (multi-object tracking)
        → ANPRCoordinator  (plate recognition)
        → PipelineResult

Design Rules:
- One CameraPipeline instance = exactly one camera-processing session.
- Purely orchestrates existing components; zero duplicated detection/tracking/ANPR logic.
- Preserves original DetectionResult, TrackingResult, and ANPRResult objects.
- Does NOT touch database, backend API, network RTSP, or deployment infrastructure.
- Error isolation: component-level exceptions are caught, logged, and surfaced
  in PipelineResult without crashing the pipeline.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from streaming.frame_reader import FramePacket

try:
    from .schemas import DetectionResult
    from .stream_processor import StreamProcessor
    from .tracking import VehicleTracker, TrackingResult
    from .anpr import ANPRCoordinator, ANPRResult
except ImportError:
    from ai_engine.schemas import DetectionResult
    from ai_engine.stream_processor import StreamProcessor
    from ai_engine.tracking import VehicleTracker, TrackingResult
    from ai_engine.anpr import ANPRCoordinator, ANPRResult

logger = logging.getLogger("sentinel.ai_engine.pipeline")


@dataclass(slots=True)
class PipelineResult:
    """
    Aggregated output from a single CameraPipeline frame processing pass.

    Attributes:
        camera_id: Source camera identifier.
        pts_ms: Source video presentation timestamp (ms) from the FramePacket.
        detection_result: DetectionResult from StreamProcessor, or None if skipped/errored.
        tracking_result: TrackingResult from VehicleTracker, or None if detection was None.
        anpr_results: List of ANPRResult objects produced by ANPRCoordinator (may be empty).
        skipped: True if the frame was skipped by StreamProcessor sampling policy.
        pipeline_time_ms: Wall-clock time (ms) consumed by the full pipeline pass.
        errors: List of error descriptions for any component-level failures.
    """
    camera_id: str
    pts_ms: float | None
    detection_result: DetectionResult | None = None
    tracking_result: TrackingResult | None = None
    anpr_results: list[ANPRResult] = field(default_factory=list)
    skipped: bool = False
    pipeline_time_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def has_detections(self) -> bool:
        """True if at least one vehicle was detected in this frame."""
        return self.detection_result is not None and self.detection_result.count > 0

    @property
    def has_tracks(self) -> bool:
        """True if the tracker produced active tracks for this frame."""
        return self.tracking_result is not None and self.tracking_result.active_count > 0

    @property
    def has_plates(self) -> bool:
        """True if at least one ANPR result was produced."""
        return len(self.anpr_results) > 0

    @property
    def recognized_plates(self) -> list[ANPRResult]:
        """Return only ANPR results with status 'RECOGNIZED'."""
        return [r for r in self.anpr_results if r.status == "RECOGNIZED"]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the pipeline result to a JSON-friendly dictionary."""
        return {
            "camera_id": self.camera_id,
            "pts_ms": self.pts_ms,
            "skipped": self.skipped,
            "has_detections": self.has_detections,
            "has_tracks": self.has_tracks,
            "has_plates": self.has_plates,
            "detection_count": self.detection_result.count if self.detection_result else 0,
            "active_track_count": self.tracking_result.active_count if self.tracking_result else 0,
            "anpr_count": len(self.anpr_results),
            "recognized_count": len(self.recognized_plates),
            "pipeline_time_ms": round(self.pipeline_time_ms, 2),
            "errors": self.errors,
        }


class CameraPipeline:
    """
    Single-camera AI pipeline coordinator.

    Chains existing components:
        StreamProcessor → VehicleTracker → ANPRCoordinator

    Each CameraPipeline instance owns references to its components but does NOT
    create them — they are injected at construction. This keeps the pipeline
    purely an orchestrator with zero ownership of model loading or config policy.

    Usage:
        pipeline = CameraPipeline(
            camera_id="CAM_01",
            stream_processor=stream_processor,
            vehicle_tracker=vehicle_tracker,
            anpr_coordinator=anpr_coordinator,
        )

        for packet in frame_source:
            result = pipeline.process_frame(packet)
            # result.detection_result, result.tracking_result, result.anpr_results
    """

    def __init__(
        self,
        camera_id: str,
        stream_processor: StreamProcessor,
        vehicle_tracker: VehicleTracker,
        anpr_coordinator: ANPRCoordinator | None = None,
    ) -> None:
        """
        Initialize a single-camera pipeline session.

        Parameters:
            camera_id: Unique camera identifier for this pipeline session.
            stream_processor: Pre-configured StreamProcessor with an attached VehicleDetector.
            vehicle_tracker: Pre-configured VehicleTracker (may be shared across cameras
                             since it lazily creates per-camera CameraTrackers internally).
            anpr_coordinator: Optional ANPRCoordinator. If None, ANPR stage is skipped.
        """
        if not camera_id:
            raise ValueError("camera_id must be a non-empty string")
        if stream_processor is None:
            raise ValueError("stream_processor must not be None")
        if vehicle_tracker is None:
            raise ValueError("vehicle_tracker must not be None")

        self.camera_id = camera_id
        self.stream_processor = stream_processor
        self.vehicle_tracker = vehicle_tracker
        self.anpr_coordinator = anpr_coordinator

        # Per-pipeline session telemetry
        self._frames_total: int = 0
        self._frames_skipped: int = 0
        self._frames_detected: int = 0
        self._frames_tracked: int = 0
        self._frames_anpr: int = 0
        self._total_errors: int = 0

    def process_frame(self, packet: FramePacket) -> PipelineResult:
        """
        Process a single FramePacket through the full AI pipeline.

        Execution order:
        1. StreamProcessor: Sampling + YOLO detection → DetectionResult | None
        2. VehicleTracker: Multi-object tracking → TrackingResult | None
        3. ANPRCoordinator: Plate recognition → list[ANPRResult]

        Error isolation: If any stage raises an exception, the pipeline captures
        the error, logs it, and returns a partial PipelineResult. Downstream
        stages are skipped if their upstream dependency returned None.

        Parameters:
            packet: FramePacket from the streaming module.

        Returns:
            PipelineResult with outputs from all executed stages.
        """
        t_start = time.perf_counter()
        self._frames_total += 1

        result = PipelineResult(
            camera_id=self.camera_id,
            pts_ms=packet.pts_ms,
        )

        # ── Stage 1: Detection via StreamProcessor ──────────────────────
        detection_result: DetectionResult | None = None
        try:
            detection_result = self.stream_processor.process_packet(packet)
        except Exception as exc:
            error_msg = f"StreamProcessor error: {exc}"
            logger.error(
                "Pipeline %s: %s (PTS %s)",
                self.camera_id, error_msg, packet.pts_ms,
                exc_info=True,
            )
            result.errors.append(error_msg)
            self._total_errors += 1

        if detection_result is None:
            # Frame was skipped by sampling policy or detector returned None
            result.skipped = True
            self._frames_skipped += 1
            result.pipeline_time_ms = (time.perf_counter() - t_start) * 1000.0
            return result

        result.detection_result = detection_result
        self._frames_detected += 1

        # ── Stage 2: Tracking via VehicleTracker ────────────────────────
        tracking_result: TrackingResult | None = None
        try:
            tracking_result = self.vehicle_tracker.update(detection_result)
        except Exception as exc:
            error_msg = f"VehicleTracker error: {exc}"
            logger.error(
                "Pipeline %s: %s (PTS %s)",
                self.camera_id, error_msg, packet.pts_ms,
                exc_info=True,
            )
            result.errors.append(error_msg)
            self._total_errors += 1

        if tracking_result is not None:
            result.tracking_result = tracking_result
            self._frames_tracked += 1

        # ── Stage 3: ANPR via ANPRCoordinator ───────────────────────────
        if self.anpr_coordinator is not None and tracking_result is not None:
            try:
                anpr_results = self.anpr_coordinator.process(
                    packet=packet,
                    tracking_result=tracking_result,
                )
                result.anpr_results = anpr_results
                if anpr_results:
                    self._frames_anpr += 1
            except Exception as exc:
                error_msg = f"ANPRCoordinator error: {exc}"
                logger.error(
                    "Pipeline %s: %s (PTS %s)",
                    self.camera_id, error_msg, packet.pts_ms,
                    exc_info=True,
                )
                result.errors.append(error_msg)
                self._total_errors += 1

        result.pipeline_time_ms = (time.perf_counter() - t_start) * 1000.0
        return result

    def get_stats(self) -> dict[str, Any]:
        """
        Return aggregated pipeline session telemetry.

        Returns dict with keys:
            camera_id, frames_total, frames_skipped, frames_detected,
            frames_tracked, frames_anpr, total_errors,
            stream_processor_stats.
        """
        return {
            "camera_id": self.camera_id,
            "frames_total": self._frames_total,
            "frames_skipped": self._frames_skipped,
            "frames_detected": self._frames_detected,
            "frames_tracked": self._frames_tracked,
            "frames_anpr": self._frames_anpr,
            "total_errors": self._total_errors,
            "stream_processor_stats": self.stream_processor.get_stats(),
        }

    def reset_stats(self) -> None:
        """Reset pipeline-level telemetry counters (does NOT reset component state)."""
        self._frames_total = 0
        self._frames_skipped = 0
        self._frames_detected = 0
        self._frames_tracked = 0
        self._frames_anpr = 0
        self._total_errors = 0
