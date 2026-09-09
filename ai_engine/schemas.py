"""
AI Engine Data Schemas for Sentinel.

Defines decoupled structured representations of vehicle detection results
strictly preserving source presentation timestamps (PTS) and camera IDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Detection:
    """
    Structured representation of a single detected vehicle.

    IMPORTANT:
    `pts_ms` is the source video presentation timestamp inherited from FramePacket.
    Arrival wall-clock time must NEVER be used as the video timestamp.
    """
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    camera_id: str
    pts_ms: float | None

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Return (x1, y1, x2, y2) bounding box."""
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def width(self) -> float:
        """Bounding box width."""
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        """Bounding box height."""
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        """Bounding box pixel area."""
        return self.width * self.height

    def to_dict(self) -> dict[str, Any]:
        """Convert to clean dictionary representation."""
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
            "x2": round(self.x2, 2),
            "y2": round(self.y2, 2),
            "camera_id": self.camera_id,
            "pts_ms": self.pts_ms,
        }


@dataclass(slots=True)
class DetectionResult:
    """
    Aggregated detection results for a single processed video frame.
    """
    camera_id: str
    pts_ms: float | None
    detections: list[Detection] = field(default_factory=list)
    inference_time_ms: float = 0.0
    device: str = "cpu"
    model_name: str = ""
    is_discontinuity: bool = False

    @property
    def count(self) -> int:
        """Number of detected vehicles."""
        return len(self.detections)

    def to_dict(self) -> dict[str, Any]:
        """Convert to clean dictionary representation."""
        return {
            "camera_id": self.camera_id,
            "pts_ms": self.pts_ms,
            "count": self.count,
            "detections": [d.to_dict() for d in self.detections],
            "inference_time_ms": round(self.inference_time_ms, 2),
            "device": self.device,
            "model_name": self.model_name,
            "is_discontinuity": self.is_discontinuity,
        }
