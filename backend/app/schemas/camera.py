"""Pydantic schemas for Camera API requests and responses."""

from datetime import datetime
from pydantic import BaseModel


class CameraBase(BaseModel):
    """Shared camera fields."""
    camera_code: str
    name: str
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    stream_url: str | None = None
    status: str = "offline"
    vendor: str | None = None


class CameraCreate(CameraBase):
    """Schema for creating a new camera."""
    pass


class CameraUpdate(BaseModel):
    """Schema for updating a camera. All fields optional."""
    name: str | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    stream_url: str | None = None
    status: str | None = None
    vendor: str | None = None


class CameraResponse(CameraBase):
    """Schema for camera API responses."""
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CameraListResponse(BaseModel):
    """Schema for paginated camera list."""
    cameras: list[CameraResponse]
    total: int
