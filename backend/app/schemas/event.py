"""Pydantic schemas for Event API requests and responses."""

from datetime import datetime
from pydantic import BaseModel


class EventBase(BaseModel):
    """Shared event fields."""
    camera_id: int
    vehicle_id: int | None = None
    event_type: str
    object_type: str | None = None
    confidence: float | None = None
    timestamp: datetime | None = None
    snapshot_path: str | None = None


class EventCreate(EventBase):
    """Schema for creating a new event."""
    pass


class EventResponse(EventBase):
    """Schema for event API responses."""
    id: int
    timestamp: datetime
    plate_number: str | None = None

    model_config = {"from_attributes": True}


class EventListResponse(BaseModel):
    """Schema for paginated event list."""
    events: list[EventResponse]
    total: int
