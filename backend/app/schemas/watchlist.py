"""
Pydantic schemas for Watchlist API.

Provides request validation, response serialization, and status controls for
vehicles of interest on the surveillance watchlist.
"""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class WatchlistBase(BaseModel):
    plate_number: str = Field(..., description="Indian vehicle registration number")
    description: str | None = Field(None, description="Reason or notes for flagging the vehicle")
    severity: str = Field("high", description="Alert severity: low, medium, high, critical")
    is_active: bool = Field(True, description="Active status for surveillance matching")


class WatchlistCreate(WatchlistBase):
    pass


class WatchlistUpdate(BaseModel):
    plate_number: str | None = Field(None, description="Updated plate number")
    description: str | None = Field(None, description="Updated reason or notes")
    severity: str | None = Field(None, description="Updated severity: low, medium, high, critical")
    is_active: bool | None = Field(None, description="Toggle active/inactive status")


class WatchlistResponse(WatchlistBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WatchlistListResponse(BaseModel):
    items: list[WatchlistResponse]
    total: int
