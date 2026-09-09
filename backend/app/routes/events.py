"""Event API routes — detection event listing and creation."""

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.event import Event
from app.models.camera import Camera
from app.schemas.event import EventCreate, EventResponse, EventListResponse

router = APIRouter(prefix="/events", tags=["Events"])


@router.get("", response_model=EventListResponse)
def list_events(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    camera_id: int | None = Query(None, description="Filter by camera"),
    event_type: str | None = Query(None, description="Filter by event type"),
    db: Session = Depends(get_db),
):
    """List detection events with optional camera/type filters."""
    query = db.query(Event)
    if camera_id is not None:
        query = query.filter(Event.camera_id == camera_id)
    if event_type:
        query = query.filter(Event.event_type == event_type)
    total = query.count()
    events = query.order_by(Event.timestamp.desc()).offset(skip).limit(limit).all()
    return EventListResponse(
        events=[EventResponse.model_validate(e) for e in events],
        total=total,
    )


@router.get("/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    """Get a single event by ID."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return EventResponse.model_validate(event)


@router.post("", response_model=EventResponse, status_code=201)
def create_event(payload: EventCreate, db: Session = Depends(get_db)):
    """Record a new detection event."""
    # Verify the camera exists
    camera = db.query(Camera).filter(Camera.id == payload.camera_id).first()
    if not camera:
        raise HTTPException(
            status_code=404, detail=f"Camera {payload.camera_id} not found"
        )
    event_data = payload.model_dump()
    if event_data.get("timestamp") is None:
        event_data["timestamp"] = datetime.utcnow()
    event = Event(**event_data)
    db.add(event)
    db.commit()
    db.refresh(event)
    return EventResponse.model_validate(event)
