"""
Data schemas for Phase 5B Multi-Object Vehicle Tracking in Sentinel AI Engine.

Defines stateful representations of tracked vehicles across video frames,
lifecycle states, and aggregated tracking results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TrackState(str, Enum):
    """
    Lifecycle state of a tracked vehicle.

    TENTATIVE:  Newly initialized track with fewer than min_hits observations.
    CONFIRMED:  Actively tracked vehicle with consistent detections.
    LOST:       Temporarily unmatched track undergoing motion coasting.
    TERMINATED: Permanently closed track pruned from the active pool.
    """
    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    LOST = "LOST"
    TERMINATED = "TERMINATED"


@dataclass(slots=True)
class Track:
    """
    Stateful representation of a tracked vehicle across frames.

    Attributes:
        track_id: Monotonically increasing identifier, unique per camera.
        camera_id: Source camera identifier.
        class_id: COCO/YOLO class ID (e.g. 2 for car, 7 for truck).
        class_name: Human-readable class name.
        confidence: Confidence score of the most recent matched detection.
        bbox: Bounding box coordinates (x1, y1, x2, y2).
        state: Current lifecycle state.
        first_pts_ms: Source video PTS (ms) when track was first observed.
        last_pts_ms: Source video PTS (ms) of the latest matched observation.
        hits: Total number of successful detection associations.
        misses: Number of consecutive missed frames since last match.
        best_confidence: Maximum confidence observed across track lifetime.
        best_bbox: Bounding box associated with best_confidence (optimal for ANPR).
        velocity: Estimated velocity (dx1, dy1, dx2, dy2) for linear motion extrapolation.
        velocity_is_per_ms: True if velocity is scaled per millisecond, False if per frame.
    """
    track_id: int
    camera_id: str
    class_id: int
    class_name: str
    confidence: float
    bbox: tuple[float, float, float, float]
    state: TrackState = TrackState.TENTATIVE
    first_pts_ms: float | None = None
    last_pts_ms: float | None = None
    hits: int = 1
    misses: int = 0
    best_confidence: float = 0.0
    best_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    velocity: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    velocity_is_per_ms: bool = False

    @property
    def x1(self) -> float:
        return self.bbox[0]

    @property
    def y1(self) -> float:
        return self.bbox[1]

    @property
    def x2(self) -> float:
        return self.bbox[2]

    @property
    def y2(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return max(0.0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        return max(0.0, self.bbox[3] - self.bbox[1])

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def is_confirmed(self) -> bool:
        return self.state == TrackState.CONFIRMED

    @property
    def is_active(self) -> bool:
        return self.state in (TrackState.TENTATIVE, TrackState.CONFIRMED, TrackState.LOST)

    def to_dict(self) -> dict[str, Any]:
        """Serialize track state to a clean JSON-friendly dictionary."""
        return {
            "track_id": self.track_id,
            "camera_id": self.camera_id,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox": [round(c, 2) for c in self.bbox],
            "state": self.state.value,
            "first_pts_ms": self.first_pts_ms,
            "last_pts_ms": self.last_pts_ms,
            "hits": self.hits,
            "misses": self.misses,
            "best_confidence": round(self.best_confidence, 4),
            "best_bbox": [round(c, 2) for c in self.best_bbox],
        }


@dataclass(slots=True)
class TrackingResult:
    """
    Aggregated multi-object tracking results for a single processed frame.

    Attributes:
        camera_id: Source camera identifier.
        pts_ms: Source video PTS (ms) of the processed frame.
        is_discontinuity: True if this frame represents a stream jump, loop, or reset.
        active_tracks: All active tracks (TENTATIVE, CONFIRMED, LOST) currently maintained.
        new_tracks: Tracks initialized in this frame.
        lost_tracks: Tracks that were terminated in this frame.
    """
    camera_id: str
    pts_ms: float | None
    is_discontinuity: bool = False
    active_tracks: list[Track] = field(default_factory=list)
    new_tracks: list[Track] = field(default_factory=list)
    lost_tracks: list[Track] = field(default_factory=list)

    @property
    def active_count(self) -> int:
        return len(self.active_tracks)

    @property
    def confirmed_tracks(self) -> list[Track]:
        return [t for t in self.active_tracks if t.state == TrackState.CONFIRMED]

    def to_dict(self) -> dict[str, Any]:
        """Serialize tracking results to a clean JSON-friendly dictionary."""
        return {
            "camera_id": self.camera_id,
            "pts_ms": self.pts_ms,
            "is_discontinuity": self.is_discontinuity,
            "active_count": self.active_count,
            "active_tracks": [t.to_dict() for t in self.active_tracks],
            "new_tracks": [t.to_dict() for t in self.new_tracks],
            "lost_tracks": [t.to_dict() for t in self.lost_tracks],
        }
