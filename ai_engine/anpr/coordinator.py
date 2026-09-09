"""
ANPR Coordinator for Sentinel AI Engine.

Coordinates track evaluation, opportunistic vehicle cropping, quality assessment,
OCR execution, normalization, and track-level plate locking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from streaming.frame_reader import FramePacket

try:
    from ..tracking.schemas import Track, TrackState, TrackingResult
    from .schemas import ANPRConfig, ANPRResult, PlateCandidate
    from .normalization import normalize_plate
    from .preprocessor import extract_vehicle_crop, compute_crop_quality
    from .recognizer import BasePlateRecognizer
except ImportError:
    from ai_engine.tracking.schemas import Track, TrackState, TrackingResult
    from ai_engine.anpr.schemas import ANPRConfig, ANPRResult, PlateCandidate
    from ai_engine.anpr.normalization import normalize_plate
    from ai_engine.anpr.preprocessor import extract_vehicle_crop, compute_crop_quality
    from ai_engine.anpr.recognizer import BasePlateRecognizer

logger = logging.getLogger("sentinel.ai_engine.anpr.coordinator")


@dataclass
class _TrackANPRState:
    """Internal coordinator state tracked per (camera_id, track_id)."""
    attempt_count: int = 0
    last_attempt_pts_ms: float | None = None
    best_crop_quality: float = 0.0
    locked: bool = False
    recognized_plate: str | None = None
    candidates: list[PlateCandidate] = field(default_factory=list)


class ANPRCoordinator:
    """
    Decoupled coordinator managing plate recognition for multi-camera tracking streams.

    Guarantees:
    - Only processes CONFIRMED vehicle tracks.
    - Opportunistic triggering (at most N attempts per track, spaced by PTS interval).
    - Objective crop quality gating (independent of detector confidence).
    - Early lock-in on high-confidence valid plates.
    - Zero image data leakage into Track objects.
    - Full multi-camera state isolation via (camera_id, track_id) keys.
    """

    def __init__(
        self,
        recognizer: BasePlateRecognizer,
        config: ANPRConfig | None = None,
    ) -> None:
        self.recognizer = recognizer
        self.config = config or ANPRConfig()
        # Keyed by (camera_id, track_id) to strictly isolate camera state
        self._track_states: dict[tuple[str, int], _TrackANPRState] = {}

    def get_track_state(self, camera_id: str, track_id: int) -> _TrackANPRState | None:
        """Retrieve current ANPR state for a specific camera and track."""
        return self._track_states.get((camera_id, track_id))

    def process(
        self,
        packet: FramePacket,
        tracking_result: TrackingResult,
    ) -> list[ANPRResult]:
        """
        Evaluate tracks in TrackingResult against the current frame and execute ANPR
        where warranted by the budgeting policy.

        Parameters:
            packet: FramePacket containing the raw frame array and source PTS.
            tracking_result: TrackingResult containing active and lost tracks.

        Returns:
            List of ANPRResult objects for all recognition evaluations performed in this frame.
        """
        camera_id = tracking_result.camera_id
        pts_ms = packet.pts_ms
        results: list[ANPRResult] = []

        # 1. Handle Discontinuity: Flush stale ANPR states for this camera
        if tracking_result.is_discontinuity:
            logger.info("ANPRCoordinator: Discontinuity on camera %s. Flushing track states.", camera_id)
            keys_to_remove = [k for k in self._track_states if k[0] == camera_id]
            for k in keys_to_remove:
                del self._track_states[k]

        # 2. Cleanup lost/terminated tracks from state dictionary to prevent memory leaks
        for lost_track in tracking_result.lost_tracks:
            self._track_states.pop((camera_id, lost_track.track_id), None)

        # 3. Evaluate Active Tracks
        if packet.frame is None:
            return results

        for track in tracking_result.active_tracks:
            # Policy A: Only CONFIRMED tracks are eligible for ANPR
            if track.state != TrackState.CONFIRMED:
                continue

            track_key = (camera_id, track.track_id)
            state = self._track_states.setdefault(track_key, _TrackANPRState())

            # Policy B: Early lock check
            if state.locked:
                continue

            # Policy C: Maximum attempts budget check
            if state.attempt_count >= self.config.max_attempts:
                continue

            # Policy D: Minimum PTS spacing check (when both timestamps available)
            if (
                pts_ms is not None
                and state.last_attempt_pts_ms is not None
                and (pts_ms - state.last_attempt_pts_ms) < self.config.min_attempt_spacing_ms
            ):
                continue

            # Extract vehicle crop safely
            crop = extract_vehicle_crop(
                frame=packet.frame,
                bbox=track.bbox,
                min_width=self.config.min_crop_width,
                min_height=self.config.min_crop_height,
            )
            if crop is None:
                continue

            # Policy E: Objective crop quality check (Laplacian + contrast)
            quality = compute_crop_quality(crop)
            if quality < self.config.min_crop_quality:
                continue

            # Execute OCR recognition
            state.attempt_count += 1
            state.last_attempt_pts_ms = pts_ms
            state.best_crop_quality = max(state.best_crop_quality, quality)

            try:
                candidate = self.recognizer.recognize(crop=crop, pts_ms=pts_ms)
            except Exception as exc:
                logger.error(
                    "ANPR recognition failed for camera %s track %d: %s",
                    camera_id, track.track_id, exc, exc_info=True
                )
                results.append(
                    ANPRResult(
                        camera_id=camera_id,
                        track_id=track.track_id,
                        pts_ms=pts_ms,
                        raw_text="",
                        normalized_plate="",
                        confidence=0.0,
                        is_valid_format=False,
                        status="FAILED",
                        plate_bbox=None,
                    )
                )
                continue

            if candidate is None:
                results.append(
                    ANPRResult(
                        camera_id=camera_id,
                        track_id=track.track_id,
                        pts_ms=pts_ms,
                        raw_text="",
                        normalized_plate="",
                        confidence=0.0,
                        is_valid_format=False,
                        status="NO_PLATE_DETECTED",
                        plate_bbox=None,
                    )
                )
                continue

            # Policy G: Normalization & Format Validation
            normalized_plate, is_valid = normalize_plate(candidate.raw_text)
            conf = candidate.confidence

            if conf < self.config.min_confidence:
                status = "LOW_CONFIDENCE"
            elif not is_valid:
                status = "INVALID_FORMAT"
            else:
                status = "RECOGNIZED"

            # Policy H: Early Lock-in
            if conf >= self.config.early_lock_confidence and is_valid:
                state.locked = True
                state.recognized_plate = normalized_plate

            state.candidates.append(candidate)

            anpr_res = ANPRResult(
                camera_id=camera_id,
                track_id=track.track_id,
                pts_ms=pts_ms,
                raw_text=candidate.raw_text,
                normalized_plate=normalized_plate,
                confidence=conf,
                is_valid_format=is_valid,
                status=status,
                plate_bbox=candidate.plate_bbox,
            )
            results.append(anpr_res)

        return results
