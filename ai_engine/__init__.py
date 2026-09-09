"""
Sentinel AI Engine.

Vehicle detection, tracking, ANPR, and single-camera pipeline coordination
consuming Phase 4 FramePackets.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .schemas import Detection, DetectionResult
from .config import DetectorConfig, DEFAULT_TARGET_CLASSES
from .detector import VehicleDetector, ModelNotAvailableError
from .stream_processor import StreamProcessor, StreamProcessorConfig
from .tracking import (
    TrackState,
    Track,
    TrackingResult,
    TrackerConfig,
    CameraTracker,
    VehicleTracker,
)
from .anpr import (
    PlateCandidate,
    ANPRResult,
    ANPRConfig,
    BasePlateRecognizer,
    MockPlateRecognizer,
    ANPRCoordinator,
)
from .pipeline import CameraPipeline, PipelineResult
from .pipeline_manager import (
    CameraPipelineManager,
    CameraPipelineSession,
    PipelineConfig,
    PipelineSessionStatus,
)


__all__ = [
    "Detection",
    "DetectionResult",
    "DetectorConfig",
    "DEFAULT_TARGET_CLASSES",
    "VehicleDetector",
    "ModelNotAvailableError",
    "StreamProcessor",
    "StreamProcessorConfig",
    "TrackState",
    "Track",
    "TrackingResult",
    "TrackerConfig",
    "CameraTracker",
    "VehicleTracker",
    "PlateCandidate",
    "ANPRResult",
    "ANPRConfig",
    "BasePlateRecognizer",
    "MockPlateRecognizer",
    "ANPRCoordinator",
    "CameraPipeline",
    "PipelineResult",
    "CameraPipelineManager",
    "CameraPipelineSession",
    "PipelineConfig",
    "PipelineSessionStatus",
]
