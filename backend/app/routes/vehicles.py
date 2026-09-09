"""Vehicle API routes — vehicle lookup, search, and cross-camera timeline."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.vehicle import Vehicle
from app.models.event import Event
from app.models.camera import Camera
from app.schemas.vehicle import (
    VehicleCreate,
    VehicleResponse,
    VehicleTimelineEntry,
    VehicleTimelineResponse,
)

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])


@router.get("", response_model=list[VehicleResponse])
def list_vehicles(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List all known vehicles."""
    vehicles = db.query(Vehicle).order_by(Vehicle.id).offset(skip).limit(limit).all()
    return [VehicleResponse.model_validate(v) for v in vehicles]


@router.get("/search", response_model=list[VehicleResponse])
def search_vehicles(
    plate: str = Query(..., min_length=1, description="Partial or full plate number"),
    db: Session = Depends(get_db),
):
    """Search vehicles by partial plate number (case-insensitive)."""
    vehicles = (
        db.query(Vehicle)
        .filter(Vehicle.plate_number.ilike(f"%{plate}%"))
        .limit(20)
        .all()
    )
    return [VehicleResponse.model_validate(v) for v in vehicles]


@router.get("/{plate_number}", response_model=VehicleResponse)
def get_vehicle(plate_number: str, db: Session = Depends(get_db)):
    """Get a vehicle by exact plate number."""
    vehicle = (
        db.query(Vehicle)
        .filter(Vehicle.plate_number == plate_number.upper())
        .first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=404, detail=f"Vehicle with plate '{plate_number}' not found"
        )
    return VehicleResponse.model_validate(vehicle)


@router.get("/{plate_number}/timeline", response_model=VehicleTimelineResponse)
def get_vehicle_timeline(plate_number: str, db: Session = Depends(get_db)):
    """
    Get the cross-camera detection timeline for a vehicle.
    Returns chronological detections with camera info and timestamps.
    """
    vehicle = (
        db.query(Vehicle)
        .filter(Vehicle.plate_number == plate_number.upper())
        .first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=404, detail=f"Vehicle with plate '{plate_number}' not found"
        )

    # Join events with cameras to build the timeline
    results = (
        db.query(Event, Camera)
        .join(Camera, Event.camera_id == Camera.id)
        .filter(Event.vehicle_id == vehicle.id)
        .order_by(Event.timestamp.asc())
        .all()
    )

    timeline = [
        VehicleTimelineEntry(
            event_id=event.id,
            camera_id=camera.id,
            camera_code=camera.camera_code,
            camera_name=camera.name,
            location=camera.location,
            event_type=event.event_type,
            confidence=event.confidence,
            timestamp=event.timestamp,
            snapshot_path=event.snapshot_path,
        )
        for event, camera in results
    ]

    return VehicleTimelineResponse(
        plate_number=vehicle.plate_number,
        vehicle=VehicleResponse.model_validate(vehicle),
        timeline=timeline,
        total_detections=len(timeline),
    )


@router.post("", response_model=VehicleResponse, status_code=201)
def create_vehicle(payload: VehicleCreate, db: Session = Depends(get_db)):
    """Register a new vehicle."""
    existing = (
        db.query(Vehicle)
        .filter(Vehicle.plate_number == payload.plate_number.upper())
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Vehicle with plate '{payload.plate_number}' already exists",
        )
    vehicle_data = payload.model_dump()
    vehicle_data["plate_number"] = vehicle_data["plate_number"].upper()
    vehicle = Vehicle(**vehicle_data)
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return VehicleResponse.model_validate(vehicle)
