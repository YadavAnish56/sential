"""Pydantic schemas for Vehicle API requests and responses."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class VehicleBase(BaseModel):
    """Shared vehicle fields."""
    plate_number: str
    vehicle_type: str | None = None
    color: str | None = None
    make: str | None = None
    model: str | None = None


class VehicleCreate(VehicleBase):
    """Schema for creating a new vehicle."""
    pass


class VehicleResponse(VehicleBase):
    """Schema for vehicle API responses."""
    id: int

    model_config = ConfigDict(from_attributes=True)


class VehicleTimelineEntry(BaseModel):
    """A single detection in a vehicle's cross-camera timeline."""
    event_id: int
    camera_id: int
    camera_code: str
    camera_name: str
    location: str | None = None
    latitude: float | None = Field(
        None, ge=-90.0, le=90.0, description="Camera latitude in degrees (-90 to 90)"
    )
    longitude: float | None = Field(
        None, ge=-180.0, le=180.0, description="Camera longitude in degrees (-180 to 180)"
    )
    event_type: str
    confidence: float | None = None
    timestamp: datetime
    snapshot_path: str | None = None

    model_config = ConfigDict(from_attributes=True)


class VehicleTimelineResponse(BaseModel):
    """Full timeline for a vehicle across cameras."""
    plate_number: str
    vehicle: VehicleResponse
    timeline: list[VehicleTimelineEntry]
    total_detections: int
