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
)
from .recognizer import BasePlateRecognizer, MockPlateRecognizer, EasyOCRPlateRecognizer
from .coordinator import ANPRCoordinator

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
    "BasePlateRecognizer",
    "MockPlateRecognizer",
    "EasyOCRPlateRecognizer",
    "ANPRCoordinator",
]

