"""
Data schemas for Phase 6 Automatic Number Plate Recognition (ANPR) in Sentinel.

Defines structured representations for:
- PlateCandidate: OCR hypothesis and plate bounding box
- ANPRResult: Normalized vehicle license plate recognition output
- ANPRConfig: Operational thresholds and budget configurations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PlateCandidate:
    """
    Intermediate candidate emitted by a plate recognizer before or after normalization.

    Attributes:
        raw_text: Literal text emitted by the OCR engine.
        normalized_plate: Sanitized, uppercase alphanumeric plate string.
        confidence: OCR recognition confidence score [0.0, 1.0].
        is_valid_format: True if normalized_plate conforms to Indian registration formats.
        pts_ms: Presentation timestamp of the source video frame.
        plate_bbox: Optional bounding box of the plate within the vehicle crop (x1, y1, x2, y2).
        plate_detector_conf: Optional detection confidence from dedicated plate detector.
        plate_bbox_frame: Optional bounding box of the plate within the full video frame (x1, y1, x2, y2).
        aspect_ratio: Optional width/height aspect ratio of the detected plate box.
    """
    raw_text: str
    normalized_plate: str
    confidence: float
    is_valid_format: bool
    pts_ms: float | None
    plate_bbox: tuple[float, float, float, float] | None = None
    plate_detector_conf: float | None = None
    plate_bbox_frame: tuple[float, float, float, float] | None = None
    aspect_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert candidate to a JSON-serializable dictionary."""
        return {
            "raw_text": self.raw_text,
            "normalized_plate": self.normalized_plate,
            "confidence": round(self.confidence, 4),
            "is_valid_format": self.is_valid_format,
            "pts_ms": self.pts_ms,
            "plate_bbox": [round(c, 2) for c in self.plate_bbox] if self.plate_bbox else None,
            "plate_detector_conf": round(self.plate_detector_conf, 4) if self.plate_detector_conf is not None else None,
            "plate_bbox_frame": [round(c, 2) for c in self.plate_bbox_frame] if self.plate_bbox_frame else None,
            "aspect_ratio": round(self.aspect_ratio, 2) if self.aspect_ratio is not None else None,
        }


@dataclass(slots=True)
class ANPRResult:
    """
    Complete ANPR result associated with a tracked vehicle.

    Attributes:
        camera_id: Source camera identifier.
        track_id: ID of the tracked vehicle this plate belongs to.
        pts_ms: Presentation timestamp of the evaluated observation.
        raw_text: Original raw text emitted by recognizer.
        normalized_plate: Canonical normalized Indian plate string.
        confidence: Recognition confidence score.
        is_valid_format: True if conforming to Indian RTO / BH format.
        status: Status code ("RECOGNIZED", "LOW_CONFIDENCE", "INVALID_FORMAT",
                "NO_PLATE_DETECTED", "FAILED").
        plate_bbox: Optional bounding box of the plate within the vehicle crop.
        plate_bbox_frame: Optional bounding box of the plate within the full video frame.
    """
    camera_id: str
    track_id: int
    pts_ms: float | None
    raw_text: str
    normalized_plate: str
    confidence: float
    is_valid_format: bool
    status: str
    plate_bbox: tuple[float, float, float, float] | None = None
    plate_bbox_frame: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert ANPR result to a clean JSON-serializable dictionary."""
        return {
            "camera_id": self.camera_id,
            "track_id": self.track_id,
            "pts_ms": self.pts_ms,
            "raw_text": self.raw_text,
            "normalized_plate": self.normalized_plate,
            "confidence": round(self.confidence, 4),
            "is_valid_format": self.is_valid_format,
            "status": self.status,
            "plate_bbox": [round(c, 2) for c in self.plate_bbox] if self.plate_bbox else None,
            "plate_bbox_frame": [round(c, 2) for c in self.plate_bbox_frame] if self.plate_bbox_frame else None,
        }


@dataclass
class ANPRConfig:
    """
    Operational configuration for ANPR processing and track-level OCR budgeting.

    Attributes:
        min_confidence: Minimum OCR confidence threshold to classify as RECOGNIZED.
        max_attempts: Maximum number of OCR evaluations permitted per track lifetime.
        min_attempt_spacing_ms: Minimum PTS time gap required between successive OCR calls on the same track.
        early_lock_confidence: Confidence threshold at or above which a valid plate locks further OCR.
        min_crop_width: Minimum pixel width for a vehicle crop to be eligible for ANPR.
        min_crop_height: Minimum pixel height for a vehicle crop to be eligible for ANPR.
        min_crop_quality: Minimum image sharpness/contrast quality score required to attempt OCR.
        enable_plate_detector: If True, uses dedicated plate detector before OCR.
        plate_detector_model_path: Path to the dedicated license plate detector YOLO checkpoint.
        plate_detector_confidence: Minimum detection confidence threshold for plate localization.
        min_plate_width: Minimum pixel width for a detected plate box.
        min_plate_height: Minimum pixel height for a detected plate box.
        observation_buffer_size: Maximum high-quality plate observations buffered per vehicle track.
        consensus_min_observations: Minimum number of observations required for temporal consensus locking.
        consensus_agreement_ratio: Minimum fraction of agreeing observations required for consensus locking.
    """
    min_confidence: float = 0.50
    max_attempts: int = 30
    min_attempt_spacing_ms: float = 150.0
    early_lock_confidence: float = 0.85
    min_crop_width: int = 40
    min_crop_height: int = 30
    min_crop_quality: float = 10.0
    enable_plate_detector: bool = True
    plate_detector_model_path: str = "models/license_plate_detector.pt"
    plate_detector_confidence: float = 0.25
    min_plate_width: int = 30
    min_plate_height: int = 12
    min_plate_sharpness: float = 15.0
    observation_buffer_size: int = 7
    consensus_min_observations: int = 3
    consensus_agreement_ratio: float = 0.60
    min_candidate_confidence: float = 0.20
