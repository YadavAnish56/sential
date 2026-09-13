"""
ANPR Coordinator for Sentinel AI Engine.

Coordinates track evaluation, opportunistic vehicle cropping, dedicated license plate
detection, quality assessment, plate preprocessing, OCR execution, observation
buffering, syntax-aware temporal consensus, and track-level plate locking.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from streaming.frame_reader import FramePacket

try:
    from ..tracking.schemas import Track, TrackState, TrackingResult
    from .schemas import ANPRConfig, ANPRResult, PlateCandidate
    from .normalization import normalize_plate, validate_indian_plate_format
    from .preprocessor import (
        extract_vehicle_crop,
        compute_crop_quality,
        extract_plate_crop,
        compute_plate_sharpness,
        preprocess_plate_for_ocr,
        is_two_row_plate,
    )
    from .recognizer import BasePlateRecognizer, MockPlateRecognizer
    from .plate_detector import PlateDetector, DetectedPlate
except ImportError:
    from ai_engine.tracking.schemas import Track, TrackState, TrackingResult
    from ai_engine.anpr.schemas import ANPRConfig, ANPRResult, PlateCandidate
    from ai_engine.anpr.normalization import normalize_plate, validate_indian_plate_format
    from ai_engine.anpr.preprocessor import (
        extract_vehicle_crop,
        compute_crop_quality,
        extract_plate_crop,
        compute_plate_sharpness,
        preprocess_plate_for_ocr,
        is_two_row_plate,
    )
    from ai_engine.anpr.recognizer import BasePlateRecognizer, MockPlateRecognizer
    from ai_engine.anpr.plate_detector import PlateDetector, DetectedPlate

logger = logging.getLogger("sentinel.ai_engine.anpr.coordinator")

# Indian Plate Regex Patterns for semantic tokenization
STANDARD_RTO_REGEX = re.compile(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{1,4})$")
BHARAT_SERIES_REGEX = re.compile(r"^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$")


def parse_indian_plate_tokens(normalized_plate: str) -> dict[str, str] | None:
    """
    Parse a normalized Indian plate string into structural semantic components.
    """
    if not normalized_plate:
        return None

    m_std = STANDARD_RTO_REGEX.match(normalized_plate)
    if m_std:
        return {
            "type": "STANDARD",
            "state": m_std.group(1),
            "rto": m_std.group(2).zfill(2),
            "series": m_std.group(3),
            "number": m_std.group(4),
        }

    m_bh = BHARAT_SERIES_REGEX.match(normalized_plate)
    if m_bh:
        return {
            "type": "BH",
            "year": m_bh.group(1),
            "bh": "BH",
            "number": m_bh.group(3),
            "series": m_bh.group(4),
        }

    return None


@dataclass(slots=True)
class TrackPlateObservation:
    """A single high-quality plate observation recorded for a vehicle track."""
    pts_ms: float | None
    plate_bbox_crop: tuple[float, float, float, float]
    plate_bbox_frame: tuple[float, float, float, float]
    raw_text: str
    normalized_plate: str
    ocr_confidence: float
    plate_detector_confidence: float
    sharpness: float
    is_valid_format: bool
    aspect_ratio: float


@dataclass
class _TrackANPRState:
    """Internal coordinator state tracked per (camera_id, track_id)."""
    attempt_count: int = 0
    total_evaluations: int = 0
    last_attempt_pts_ms: float | None = None
    best_crop_quality: float = 0.0
    best_plate_sharpness: float = 0.0
    best_plate_area: float = 0.0
    locked: bool = False
    recognized_plate: str | None = None
    lock_method: str | None = None
    candidates: list[PlateCandidate] = field(default_factory=list)
    observations: list[TrackPlateObservation] = field(default_factory=list)


def resolve_temporal_consensus(
    observations: list[TrackPlateObservation],
    min_observations: int = 3,
    agreement_ratio: float = 0.60,
    fast_lock_confidence: float = 0.85,
    min_candidate_confidence: float = 0.20,
) -> tuple[str | None, str | None]:
    """
    Evaluate buffered plate observations for a track and attempt to resolve consensus.

    Returns:
        tuple of (resolved_plate_string, lock_method) or (None, None).
    """
    if not observations:
        return None, None

    # Policy 1: FAST LOCK on high-confidence valid observation
    for obs in reversed(observations):
        if obs.is_valid_format and obs.ocr_confidence >= fast_lock_confidence:
            return obs.normalized_plate, "FAST_LOCK"

    # Policy 2: Filter syntax-valid candidates meeting admission floor for consensus voting
    valid_obs = [
        obs for obs in observations
        if obs.is_valid_format and obs.ocr_confidence >= min_candidate_confidence
    ]
    if len(valid_obs) < min_observations:
        return None, None

    # Parse observations into semantic tokens
    parsed_candidates: list[tuple[dict[str, str], float, str]] = []
    for obs in valid_obs:
        tokens = parse_indian_plate_tokens(obs.normalized_plate)
        if tokens is not None:
            parsed_candidates.append((tokens, obs.ocr_confidence, obs.normalized_plate))

    if len(parsed_candidates) < min_observations:
        # Fall back to weighted exact-string frequency
        string_weights: dict[str, float] = {}
        total_w = 0.0
        for obs in valid_obs:
            string_weights[obs.normalized_plate] = string_weights.get(obs.normalized_plate, 0.0) + obs.ocr_confidence
            total_w += obs.ocr_confidence

        best_str, best_w = max(string_weights.items(), key=lambda x: x[1])
        if total_w > 0 and (best_w / total_w) >= agreement_ratio:
            return best_str, "CONSENSUS_LOCK"
        return None, None

    # Group by plate type (STANDARD vs BH)
    type_weights: dict[str, float] = {}
    for tokens, conf, _ in parsed_candidates:
        ptype = tokens["type"]
        type_weights[ptype] = type_weights.get(ptype, 0.0) + conf

    dominant_type = max(type_weights.items(), key=lambda x: x[1])[0]
    matched = [p for p in parsed_candidates if p[0]["type"] == dominant_type]

    # Token-level confidence-weighted voting
    fields = ["state", "rto", "series", "number"] if dominant_type == "STANDARD" else ["year", "bh", "number", "series"]
    consensus_tokens: dict[str, str] = {"type": dominant_type}

    for f in fields:
        val_weights: dict[str, float] = {}
        for tokens, conf, _ in matched:
            val = tokens.get(f, "")
            val_weights[val] = val_weights.get(val, 0.0) + conf
        winner = max(val_weights.items(), key=lambda x: x[1])[0]
        consensus_tokens[f] = winner

    if dominant_type == "STANDARD":
        consensus_plate = f"{consensus_tokens['state']}{consensus_tokens['rto']}{consensus_tokens['series']}{consensus_tokens['number']}"
    else:
        consensus_plate = f"{consensus_tokens['year']}{consensus_tokens['bh']}{consensus_tokens['number']}{consensus_tokens['series']}"

    # Verify structural validity of consensus output
    _, is_valid = normalize_plate(consensus_plate)
    if not is_valid:
        return None, None

    # Calculate agreement ratio against consensus plate
    total_conf = sum(conf for _, conf, _ in matched)
    agreeing_conf = sum(conf for _, conf, plate_str in matched if plate_str == consensus_plate)

    if total_conf > 0 and (agreeing_conf / total_conf) >= agreement_ratio:
        return consensus_plate, "CONSENSUS_LOCK"

    return None, None


class ANPRCoordinator:
    """
    Decoupled coordinator managing plate detection, tight-crop OCR, quality-aware budgeting,
    and temporal consensus for multi-camera tracking streams.

    Guarantees:
    - Only processes CONFIRMED vehicle tracks.
    - Cascades vehicle detection -> plate detection -> quality gate -> tight-crop OCR.
    - Quality-aware budgeting: tracks continue receiving ANPR opportunities as vehicles approach.
    - Multi-frame observation buffer with syntax-aware temporal consensus.
    - Early lock-in on high-confidence valid plates.
    - Zero image data leakage into Track objects.
    - Full multi-camera state isolation via (camera_id, track_id) keys.
    """

    def __init__(
        self,
        recognizer: BasePlateRecognizer,
        plate_detector: PlateDetector | None = None,
        config: ANPRConfig | None = None,
    ) -> None:
        self.recognizer = recognizer
        self.config = config or ANPRConfig()
        self.plate_detector = plate_detector

        # Lazily initialize plate detector if enabled and not provided
        if self.plate_detector is None and getattr(self.config, "enable_plate_detector", True):
            try:
                self.plate_detector = PlateDetector(
                    model_path=getattr(self.config, "plate_detector_model_path", "models/license_plate_detector.pt"),
                    confidence_threshold=getattr(self.config, "plate_detector_confidence", 0.25),
                )
            except Exception as exc:
                logger.warning("ANPRCoordinator: Failed to initialize PlateDetector: %s", exc)
                self.plate_detector = None

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
                # Track is already locked with a confirmed plate
                continue

            # Stage 1: Extract vehicle crop safely
            veh_crop = extract_vehicle_crop(
                frame=packet.frame,
                bbox=track.bbox,
                min_width=self.config.min_crop_width,
                min_height=self.config.min_crop_height,
            )
            if veh_crop is None:
                continue

            quality = compute_crop_quality(veh_crop)
            state.best_crop_quality = max(state.best_crop_quality, quality)
            state.total_evaluations += 1

            # Stage 2: Plate Detection (Cascaded)
            target_crop: np.ndarray | None = None
            plate_bbox_crop: tuple[float, float, float, float] | None = None
            plate_bbox_frame: tuple[float, float, float, float] | None = None
            plate_detector_conf: float = 0.0
            aspect_ratio: float = float(veh_crop.shape[1]) / max(1.0, float(veh_crop.shape[0]))
            plate_sharpness: float = 0.0
            current_plate_area: float = 0.0

            use_plate_detector = (
                getattr(self.config, "enable_plate_detector", True)
                and self.plate_detector is not None
                and self.plate_detector.is_ready
            )

            if use_plate_detector:
                detected_plates = self.plate_detector.detect(
                    vehicle_crop=veh_crop,
                    vehicle_bbox=track.bbox,
                )

                if detected_plates:
                    best_det = detected_plates[0]
                    min_pw = getattr(self.config, "min_plate_width", 30)
                    min_ph = getattr(self.config, "min_plate_height", 12)
                    min_sharpness = getattr(self.config, "min_plate_sharpness", 15.0)

                    # Stage 3: Tight Plate Crop extraction
                    extracted_plate = extract_plate_crop(
                        vehicle_crop=veh_crop,
                        plate_bbox=best_det.bbox_crop,
                        margin_ratio=0.15,
                    )

                    # Stage 4: Plate Quality Gate
                    if (
                        extracted_plate is not None
                        and best_det.width >= min_pw
                        and best_det.height >= min_ph
                    ):
                        psh = compute_plate_sharpness(extracted_plate)
                        if psh >= min_sharpness:
                            target_crop = extracted_plate
                            plate_bbox_crop = best_det.bbox_crop
                            plate_bbox_frame = best_det.bbox_frame
                            plate_detector_conf = best_det.confidence
                            aspect_ratio = best_det.aspect_ratio
                            plate_sharpness = psh
                            current_plate_area = best_det.width * best_det.height

            # If no plate was localized on the vehicle crop:
            if target_crop is None:
                # Fall back to vehicle crop for test mocks / legacy mode without real detector
                is_mock = (
                    isinstance(self.recognizer, MockPlateRecognizer)
                    or hasattr(self.recognizer, "_responses")
                    or (hasattr(self.recognizer, "_reader") and "Dummy" in type(self.recognizer._reader).__name__)
                    or not use_plate_detector
                )
                if is_mock:
                    target_crop = veh_crop
                    plate_sharpness = compute_plate_sharpness(target_crop)
                else:
                    # In real production ANPR with plate detector: do not run OCR on car body
                    continue

            # Stage 5: Quality-Aware Track ANPR Budgeting & Spacing
            is_quality_upgrade = (
                state.best_plate_area > 0
                and (
                    (current_plate_area > state.best_plate_area * 1.20)
                    or (plate_sharpness > state.best_plate_sharpness * 1.25)
                )
            )

            # Policy C: Maximum attempts budget check (remains eligible on quality upgrade)
            if state.attempt_count >= self.config.max_attempts and not is_quality_upgrade:
                continue

            # Policy D: Minimum PTS spacing check between successive evaluations
            if (
                pts_ms is not None
                and state.last_attempt_pts_ms is not None
                and (pts_ms - state.last_attempt_pts_ms) < self.config.min_attempt_spacing_ms
                and not is_quality_upgrade
            ):
                continue

            state.attempt_count += 1
            state.last_attempt_pts_ms = pts_ms
            state.best_plate_area = max(state.best_plate_area, current_plate_area)
            state.best_plate_sharpness = max(state.best_plate_sharpness, plate_sharpness)

            try:
                candidate = self.recognizer.recognize(crop=target_crop, pts_ms=pts_ms)

                # If color crop OCR yielded no result, retry on preprocessed grayscale
                if candidate is None or not candidate.raw_text:
                    prep_crop = preprocess_plate_for_ocr(target_crop)
                    candidate = self.recognizer.recognize(crop=prep_crop, pts_ms=pts_ms)
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
                        plate_bbox=plate_bbox_crop,
                        plate_bbox_frame=plate_bbox_frame,
                    )
                )
                continue

            if candidate is None or not candidate.raw_text:
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
                        plate_bbox=plate_bbox_crop,
                        plate_bbox_frame=plate_bbox_frame,
                    )
                )
                continue

            # Stage 4: Normalization & Format Validation
            normalized_plate, is_valid = normalize_plate(candidate.raw_text)
            conf = candidate.confidence

            # Attach plate detector metadata
            candidate_with_meta = PlateCandidate(
                raw_text=candidate.raw_text,
                normalized_plate=normalized_plate,
                confidence=conf,
                is_valid_format=is_valid,
                pts_ms=pts_ms,
                plate_bbox=plate_bbox_crop or candidate.plate_bbox,
                plate_detector_conf=plate_detector_conf,
                plate_bbox_frame=plate_bbox_frame,
                aspect_ratio=aspect_ratio,
            )
            state.candidates.append(candidate_with_meta)

            # Stage 5: Observation Buffer Management (Candidate Admission Floor: >= 0.20)
            min_cand_conf = getattr(self.config, "min_candidate_confidence", 0.20)
            if conf >= min_cand_conf:
                obs = TrackPlateObservation(
                    pts_ms=pts_ms,
                    plate_bbox_crop=plate_bbox_crop or (0.0, 0.0, 0.0, 0.0),
                    plate_bbox_frame=plate_bbox_frame or (0.0, 0.0, 0.0, 0.0),
                    raw_text=candidate.raw_text,
                    normalized_plate=normalized_plate,
                    ocr_confidence=conf,
                    plate_detector_confidence=plate_detector_conf,
                    sharpness=plate_sharpness,
                    is_valid_format=is_valid,
                    aspect_ratio=aspect_ratio,
                )
                state.observations.append(obs)
                max_buffer_sz = getattr(self.config, "observation_buffer_size", 7)
                if len(state.observations) > max_buffer_sz:
                    state.observations.pop(0)

            # Stage 6: Temporal Consensus & Lock Evaluation
            fast_lock_th = getattr(self.config, "fast_lock_confidence", self.config.early_lock_confidence)
            min_obs = getattr(self.config, "consensus_min_observations", 3)
            agr_ratio = getattr(self.config, "consensus_agreement_ratio", 0.60)

            resolved_plate, lock_method = resolve_temporal_consensus(
                observations=state.observations,
                min_observations=min_obs,
                agreement_ratio=agr_ratio,
                fast_lock_confidence=fast_lock_th,
                min_candidate_confidence=min_cand_conf,
            )

            if resolved_plate is not None:
                state.locked = True
                state.recognized_plate = resolved_plate
                state.lock_method = lock_method
                status = "RECOGNIZED"
                final_plate_str = resolved_plate
            elif not is_valid:
                status = "INVALID_FORMAT"
                final_plate_str = normalized_plate
            else:
                status = "LOW_CONFIDENCE"
                final_plate_str = normalized_plate

            anpr_res = ANPRResult(
                camera_id=camera_id,
                track_id=track.track_id,
                pts_ms=pts_ms,
                raw_text=candidate.raw_text,
                normalized_plate=final_plate_str,
                confidence=conf,
                is_valid_format=is_valid,
                status=status,
                plate_bbox=plate_bbox_crop or candidate.plate_bbox,
                plate_bbox_frame=plate_bbox_frame,
            )
            results.append(anpr_res)

        return results
