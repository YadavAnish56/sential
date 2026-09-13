"""
AI Engine Configuration for Sentinel.

Reuses existing configuration conventions (AI_MODEL_PATH, DETECTION_CONFIDENCE)
without creating duplicate or conflicting systems.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Sequence

from pathlib import Path

# Workspace root is two levels above ai_engine/
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def resolve_model_path(path: str | Path | None, default_filename: str = "") -> str:
    """
    Deterministically resolve model path independent of process current working directory.
    Checks:
    1. If path is already a valid file -> return absolute path
    2. If path exists relative to WORKSPACE_ROOT -> return that absolute path
    3. If filename exists in WORKSPACE_ROOT / 'models' -> return that path
    4. Fallback to path string
    """
    if not path and default_filename:
        path = default_filename

    if not path:
        return ""

    p = Path(path)

    # 1. Direct path check (e.g. absolute or matching CWD)
    if p.is_file():
        return str(p.resolve())

    # 2. Check relative to WORKSPACE_ROOT (e.g. "models/yolov8n.pt")
    candidate_root = (WORKSPACE_ROOT / p).resolve()
    if candidate_root.is_file():
        return str(candidate_root)

    # 3. Check within WORKSPACE_ROOT / "models"
    candidate_models = (WORKSPACE_ROOT / "models" / p.name).resolve()
    if candidate_models.is_file():
        return str(candidate_models)

    # 4. Fallback to string
    return str(p)


DEFAULT_TARGET_CLASSES: tuple[str, ...] = ("car", "motorcycle", "bus", "truck")


@dataclass
class DetectorConfig:
    """
    Configuration parameters for VehicleDetector.
    """
    model_path: str = field(
        default_factory=lambda: resolve_model_path(os.environ.get("AI_MODEL_PATH"), "models/yolov8n.pt")
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
