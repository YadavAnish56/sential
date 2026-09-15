"""Pydantic schemas for Alert API requests and responses."""

from datetime import datetime
from pydantic import BaseModel


class AlertBase(BaseModel):
    """Shared alert fields."""
    camera_id: int
    vehicle_id: int | None = None
    alert_type: str
    severity: str
    message: str | None = None
    status: str = "new"


class AlertCreate(AlertBase):
    """Schema for creating a new alert."""
    pass


class AlertUpdate(BaseModel):
    """Schema for updating an alert (e.g. acknowledging it)."""
    status: str | None = None
    message: str | None = None


class AlertResponse(AlertBase):
    """Schema for alert API responses."""
    id: int
    timestamp: datetime
    plate_number: str | None = None

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    """Schema for paginated alert list."""
    alerts: list[AlertResponse]
    total: int
