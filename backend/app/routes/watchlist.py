"""
Watchlist API routes for Sentinel.

Provides CRUD endpoints and status management for vehicles of interest:
- Normalizes and validates Indian license plate registration syntax.
- Enforces uniqueness constraints on active watchlist records.
- Supports active/inactive lifecycle controls and severity classification.
"""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.watchlist import Watchlist
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistUpdate,
    WatchlistResponse,
)

try:
    from ai_engine.anpr.normalization import normalize_plate
except ImportError:
    import sys
    from pathlib import Path
    _sentinel_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    if _sentinel_root not in sys.path:
        sys.path.insert(0, _sentinel_root)
    from ai_engine.anpr.normalization import normalize_plate

router = APIRouter(prefix="/watchlist", tags=["Watchlist"])

VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


def _normalize_and_validate_plate(plate_input: str) -> str:
    """Validate and normalize plate string, raising HTTP 422 on invalid format."""
    if not plate_input or not plate_input.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Plate number cannot be empty",
        )
    normalized, is_valid = normalize_plate(plate_input)
    if not is_valid or not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid Indian license plate format: '{plate_input}'. Must follow standard RTO or BH series syntax.",
        )
    return normalized


@router.get("", response_model=list[WatchlistResponse])
def list_watchlist(
    skip: int = Query(0, ge=0, description="Records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    severity: str | None = Query(None, description="Filter by severity (low, medium, high, critical)"),
    search: str | None = Query(None, description="Search by partial or full plate number"),
    db: Session = Depends(get_db),
):
    """List all watchlist entries with optional filtering and search."""
    query = db.query(Watchlist)

    if is_active is not None:
        query = query.filter(Watchlist.is_active == is_active)
    if severity:
        query = query.filter(Watchlist.severity == severity.lower())
    if search:
        cleaned_search = search.strip().upper()
        query = query.filter(Watchlist.plate_number.contains(cleaned_search))

    entries = query.order_by(Watchlist.created_at.desc()).offset(skip).limit(limit).all()
    return [WatchlistResponse.model_validate(e) for e in entries]


@router.post("", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
def create_watchlist_entry(payload: WatchlistCreate, db: Session = Depends(get_db)):
    """Register a new vehicle of interest on the surveillance watchlist."""
    normalized_plate = _normalize_and_validate_plate(payload.plate_number)

    severity = payload.severity.lower() if payload.severity else "high"
    if severity not in VALID_SEVERITIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid severity '{payload.severity}'. Must be one of: {', '.join(sorted(VALID_SEVERITIES))}",
        )

    # Check duplicate active entries
    if payload.is_active:
        existing_active = (
            db.query(Watchlist)
            .filter(Watchlist.plate_number == normalized_plate, Watchlist.is_active == True)
            .first()
        )
        if existing_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Active watchlist entry for plate '{normalized_plate}' already exists (ID: {existing_active.id})",
            )

    entry = Watchlist(
        plate_number=normalized_plate,
        description=payload.description.strip() if payload.description else None,
        severity=severity,
        is_active=payload.is_active,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return WatchlistResponse.model_validate(entry)


@router.get("/{watchlist_id}", response_model=WatchlistResponse)
def get_watchlist_entry(watchlist_id: int, db: Session = Depends(get_db)):
    """Get a single watchlist record by ID."""
    entry = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist entry {watchlist_id} not found",
        )
    return WatchlistResponse.model_validate(entry)


@router.patch("/{watchlist_id}", response_model=WatchlistResponse)
def update_watchlist_entry(
    watchlist_id: int, payload: WatchlistUpdate, db: Session = Depends(get_db)
):
    """Update a watchlist entry's plate number, severity, description, or active status."""
    entry = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist entry {watchlist_id} not found",
        )

    update_data = payload.model_dump(exclude_unset=True)

    target_plate = entry.plate_number
    if "plate_number" in update_data and update_data["plate_number"] is not None:
        target_plate = _normalize_and_validate_plate(update_data["plate_number"])
        update_data["plate_number"] = target_plate

    if "severity" in update_data and update_data["severity"] is not None:
        sev = update_data["severity"].lower()
        if sev not in VALID_SEVERITIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid severity '{update_data['severity']}'. Must be one of: {', '.join(sorted(VALID_SEVERITIES))}",
            )
        update_data["severity"] = sev

    # Check for duplicate active entry collision
    will_be_active = update_data.get("is_active", entry.is_active)
    if will_be_active:
        collision = (
            db.query(Watchlist)
            .filter(
                Watchlist.plate_number == target_plate,
                Watchlist.is_active == True,
                Watchlist.id != watchlist_id,
            )
            .first()
        )
        if collision:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Another active watchlist entry for plate '{target_plate}' already exists (ID: {collision.id})",
            )

    for field, value in update_data.items():
        setattr(entry, field, value)

    entry.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(entry)
    return WatchlistResponse.model_validate(entry)


@router.delete("/{watchlist_id}")
def delete_watchlist_entry(
    watchlist_id: int,
    deactivate_only: bool = Query(False, description="If true, mark inactive instead of hard delete"),
    db: Session = Depends(get_db),
):
    """Delete a watchlist entry or optionally deactivate it."""
    entry = db.query(Watchlist).filter(Watchlist.id == watchlist_id).first()
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Watchlist entry {watchlist_id} not found",
        )

    if deactivate_only:
        entry.is_active = False
        entry.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(entry)
        return {
            "message": f"Watchlist entry {watchlist_id} deactivated successfully",
            "id": watchlist_id,
            "is_active": False,
        }

    db.delete(entry)
    db.commit()
    return {
        "message": f"Watchlist entry {watchlist_id} deleted successfully",
        "id": watchlist_id,
    }
