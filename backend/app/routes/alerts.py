"""Alert API routes — alert listing, creation, and status management."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.vehicle import Vehicle
from app.schemas.alert import (
    AlertCreate,
    AlertUpdate,
    AlertResponse,
    AlertListResponse,
)

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get("", response_model=AlertListResponse)
def list_alerts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None, description="Filter by alert status (new, acknowledged, resolved)"),
    severity: str | None = Query(None, description="Filter by severity (low, medium, high, critical)"),
    db: Session = Depends(get_db),
):
    """List alerts with optional status/severity filters."""
    query = (
        db.query(Alert, Vehicle.plate_number)
        .outerjoin(Vehicle, Alert.vehicle_id == Vehicle.id)
    )
    if status:
        query = query.filter(Alert.status == status)
    if severity:
        query = query.filter(Alert.severity == severity)
    total = query.count()
    rows = query.order_by(Alert.timestamp.desc()).offset(skip).limit(limit).all()

    items = []
    for a, plate in rows:
        resp = AlertResponse.model_validate(a)
        resp.plate_number = plate
        items.append(resp)

    return AlertListResponse(
        alerts=items,
        total=total,
    )


@router.get("/{alert_id}", response_model=AlertResponse)
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    """Get a single alert by ID."""
    row = (
        db.query(Alert, Vehicle.plate_number)
        .outerjoin(Vehicle, Alert.vehicle_id == Vehicle.id)
        .filter(Alert.id == alert_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    alert, plate = row
    resp = AlertResponse.model_validate(alert)
    resp.plate_number = plate
    return resp


@router.post("", response_model=AlertResponse, status_code=201)
def create_alert(payload: AlertCreate, db: Session = Depends(get_db)):
    """Create a new alert."""
    # Verify the camera exists
    camera = db.query(Camera).filter(Camera.id == payload.camera_id).first()
    if not camera:
        raise HTTPException(
            status_code=404, detail=f"Camera {payload.camera_id} not found"
        )
    alert = Alert(**payload.model_dump())
    db.add(alert)
    db.commit()
    db.refresh(alert)

    plate = None
    if alert.vehicle_id:
        v = db.query(Vehicle).filter(Vehicle.id == alert.vehicle_id).first()
        if v:
            plate = v.plate_number

    resp = AlertResponse.model_validate(alert)
    resp.plate_number = plate
    return resp


@router.patch("/{alert_id}", response_model=AlertResponse)
def update_alert(
    alert_id: int, payload: AlertUpdate, db: Session = Depends(get_db)
):
    """Update an alert's status (e.g. acknowledge or resolve)."""
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(alert, field, value)
    db.commit()
    db.refresh(alert)

    plate = None
    if alert.vehicle_id:
        v = db.query(Vehicle).filter(Vehicle.id == alert.vehicle_id).first()
        if v:
            plate = v.plate_number

    resp = AlertResponse.model_validate(alert)
    resp.plate_number = plate
    return resp
