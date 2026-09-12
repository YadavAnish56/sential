"""Pydantic schemas for Camera API requests and responses."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


def sanitize_coordinates(
    lat: float | None, lon: float | None
) -> tuple[float | None, float | None]:
    """
    Validate and sanitize geographic coordinate pairs.
    Returns (lat, lon) only if both coordinates exist and fall strictly within
    valid geographic ranges (-90 to 90 for latitude, -180 to 180 for longitude).
    Returns (None, None) if either is missing or invalid.
    Prevents invalid coordinates from being propagated to GIS map layers.
    """
    if lat is None or lon is None:
        return None, None
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (ValueError, TypeError):
        return None, None
    if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0):
        return None, None
    return lat_f, lon_f


class CameraBase(BaseModel):
    """Shared camera fields."""
    camera_code: str
    name: str
    location: str | None = None
    latitude: float | None = Field(
        None, ge=-90.0, le=90.0, description="Latitude in degrees (-90 to 90)"
    )
    longitude: float | None = Field(
        None, ge=-180.0, le=180.0, description="Longitude in degrees (-180 to 180)"
    )
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
    latitude: float | None = Field(
        None, ge=-90.0, le=90.0, description="Latitude in degrees (-90 to 90)"
    )
    longitude: float | None = Field(
        None, ge=-180.0, le=180.0, description="Longitude in degrees (-180 to 180)"
    )
    stream_url: str | None = None
    status: str | None = None
    vendor: str | None = None


class CameraResponse(CameraBase):
    """Schema for camera API responses."""
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CameraListResponse(BaseModel):
    """Schema for paginated camera list."""
    cameras: list[CameraResponse]
    total: int


class CameraMapMarker(BaseModel):
    """
    Camera record suitable for GIS map markers.
    Omits credentials and stream URLs. Coordinates are strictly validated.
    """
    id: int
    camera_code: str
    name: str
    location: str | None = None
    latitude: float | None = Field(
        None, ge=-90.0, le=90.0, description="Latitude in degrees (-90 to 90)"
    )
    longitude: float | None = Field(
        None, ge=-180.0, le=180.0, description="Longitude in degrees (-180 to 180)"
    )
    status: str

    model_config = ConfigDict(from_attributes=True)


# Alias for flexible referencing
CameraMapResponse = CameraMapMarker


def sanitize_stream_url(url: str | None) -> str | None:
    """
    Sanitize stream URL by stripping any embedded user:password credentials.
    Guarantees that credentials are never stored in the database or exposed via API.
    """
    if not url:
        return None
    try:
        import urllib.parse
        parsed = urllib.parse.urlsplit(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return urllib.parse.urlunsplit(
                (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
            )
    except Exception:
        pass
    return url


class CatalogSyncStats(BaseModel):
    """Statistics for a catalogue synchronization operation."""
    total_received: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    with_coordinates: int = 0
    with_rtsp: int = 0
    with_whep: int = 0


class CatalogSyncResponse(BaseModel):
    """Response schema for POST /api/cameras/sync-catalogue."""
    success: bool
    status_code: int | None = None
    message: str
    requires_auth: bool = False
    redirect_url: str | None = None
    stats: CatalogSyncStats = Field(default_factory=CatalogSyncStats)
    source_url: str | None = None

