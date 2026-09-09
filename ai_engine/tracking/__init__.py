"""
Phase 5B Multi-Object Vehicle Tracking for Sentinel AI Engine.
"""

from __future__ import annotations

from .schemas import TrackState, Track, TrackingResult
from .matching import box_iou_batch, associate_detections_to_tracks
from .tracker import TrackerConfig, CameraTracker, VehicleTracker

__all__ = [
    "TrackState",
    "Track",
    "TrackingResult",
    "box_iou_batch",
    "associate_detections_to_tracks",
    "TrackerConfig",
    "CameraTracker",
    "VehicleTracker",
]
