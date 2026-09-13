"""
Sentinel AI Engine — Phase 6 Automatic Number Plate Recognition (ANPR) Subpackage.
"""

from __future__ import annotations

from .schemas import PlateCandidate, ANPRResult, ANPRConfig
from .normalization import (
    normalize_plate,
    sanitize_plate_string,
    validate_indian_plate_format,
    INDIAN_STATE_CODES,
)
from .preprocessor import (
    extract_vehicle_crop,
    compute_crop_quality,
    preprocess_crop_for_ocr,
    extract_plate_crop,
    compute_plate_sharpness,
    preprocess_plate_for_ocr,
    is_two_row_plate,
)
from .recognizer import BasePlateRecognizer, MockPlateRecognizer, EasyOCRPlateRecognizer
from .plate_detector import PlateDetector, DetectedPlate
from .coordinator import ANPRCoordinator, resolve_temporal_consensus

__all__ = [
    "PlateCandidate",
    "ANPRResult",
    "ANPRConfig",
    "normalize_plate",
    "sanitize_plate_string",
    "validate_indian_plate_format",
    "INDIAN_STATE_CODES",
    "extract_vehicle_crop",
    "compute_crop_quality",
    "preprocess_crop_for_ocr",
    "extract_plate_crop",
    "compute_plate_sharpness",
    "preprocess_plate_for_ocr",
    "is_two_row_plate",
    "BasePlateRecognizer",
    "MockPlateRecognizer",
    "EasyOCRPlateRecognizer",
    "PlateDetector",
    "DetectedPlate",
    "ANPRCoordinator",
    "resolve_temporal_consensus",
]

