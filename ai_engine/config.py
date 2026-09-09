"""
AI Engine Configuration for Sentinel.

Reuses existing configuration conventions (AI_MODEL_PATH, DETECTION_CONFIDENCE)
without creating duplicate or conflicting systems.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Sequence

DEFAULT_TARGET_CLASSES: tuple[str, ...] = ("car", "motorcycle", "bus", "truck")


@dataclass
class DetectorConfig:
    """
    Configuration parameters for VehicleDetector.
    """
    model_path: str = field(
        default_factory=lambda: os.environ.get("AI_MODEL_PATH", "models/yolov8n.pt")
    )
    confidence: float = field(
        default_factory=lambda: float(os.environ.get("DETECTION_CONFIDENCE", "0.40"))
    )
    target_classes: Sequence[str] = field(
        default_factory=lambda: DEFAULT_TARGET_CLASSES
    )
    device: str = field(
        default_factory=lambda: os.environ.get("AI_DEVICE", "auto")
    )
