"""Camera CRUD API routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

try:
    from ai_engine.pipeline_manager import CameraPipelineManager, PipelineConfig
except ImportError:
    CameraPipelineManager = None
    PipelineConfig = None

from app.database.dependencies import get_db
from app.models.camera import Camera
from streaming.camera_catalog import CameraCatalog
from app.schemas.camera import (
    CameraCreate,
    CameraUpdate,
    CameraResponse,
    CameraListResponse,
)

router = APIRouter(prefix="/cameras", tags=["Cameras"])


@router.get("", response_model=CameraListResponse)
def list_cameras(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    status: str | None = Query(None, description="Filter by camera status"),
    db: Session = Depends(get_db),
):
    """List all cameras with optional filtering."""
    query = db.query(Camera)
    if status:
        query = query.filter(Camera.status == status)
    total = query.count()
    cameras = query.order_by(Camera.id).offset(skip).limit(limit).all()
    return CameraListResponse(
        cameras=[CameraResponse.model_validate(c) for c in cameras],
        total=total,
    )


def _get_manager(request: Request) -> "CameraPipelineManager":
    manager = getattr(request.app.state, "pipeline_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="CameraPipelineManager is not available")
    return manager


@router.get("/pipeline-status", tags=["Pipelines"])
def get_all_pipelines_status(request: Request):
    """Retrieve health and telemetry for all registered pipelines."""
    manager = _get_manager(request)
    return manager.get_all_health()


@router.get("/{camera_id}/pipeline-status", tags=["Pipelines"])
def get_camera_pipeline_status(camera_id: int, request: Request, db: Session = Depends(get_db)):
    """Retrieve health and telemetry for a specific camera pipeline."""
    manager = _get_manager(request)
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    health = manager.get_health(camera.camera_code)
    if health is None:
        return {"status": "unregistered"}
    return health


@router.post("/{camera_id}/start", tags=["Pipelines"])
def start_camera_pipeline(camera_id: int, request: Request, db: Session = Depends(get_db)):
    """Start the pipeline for a specific camera."""
    manager = _get_manager(request)
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    if not camera.stream_url:
        raise HTTPException(status_code=400, detail=f"Camera {camera_id} has no stream_url")
    
    config = PipelineConfig(
        camera_id=camera.camera_code,
        rtsp_url=camera.stream_url
    )
    
    # Register safely (manager handles duplicates)
    manager.register_camera(config=config, auto_start=False)
    success = manager.start_camera(camera.camera_code)
    
    if not success:
        # It might be already running or failed to start
        session = manager.get_session(camera.camera_code)
        if session and session.is_running:
            return {"message": f"Camera {camera.camera_code} pipeline is already running."}
        raise HTTPException(status_code=500, detail=f"Failed to start pipeline for camera {camera.camera_code}")
    
    return {"message": f"Camera {camera.camera_code} pipeline started."}


@router.post("/{camera_id}/stop", tags=["Pipelines"])
def stop_camera_pipeline(camera_id: int, request: Request, db: Session = Depends(get_db)):
    """Stop the pipeline for a specific camera."""
    manager = _get_manager(request)
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    success = manager.stop_camera(camera.camera_code)
    if not success:
        # Maybe it's not running or unknown
        return {"message": f"Camera {camera.camera_code} pipeline is not running or unknown."}
    
    return {"message": f"Camera {camera.camera_code} pipeline stopped."}


@router.get("/{camera_id}", response_model=CameraResponse)
def get_camera(camera_id: int, db: Session = Depends(get_db)):
    """Get a single camera by ID."""
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    return CameraResponse.model_validate(camera)


@router.post("", response_model=CameraResponse, status_code=201)
def create_camera(payload: CameraCreate, db: Session = Depends(get_db)):
    """Register a new camera."""
    existing = db.query(Camera).filter(Camera.camera_code == payload.camera_code).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Camera with code '{payload.camera_code}' already exists",
        )
    camera = Camera(**payload.model_dump())
    db.add(camera)
    db.commit()
    db.refresh(camera)
    return CameraResponse.model_validate(camera)


@router.patch("/{camera_id}", response_model=CameraResponse)
def update_camera(
    camera_id: int, payload: CameraUpdate, db: Session = Depends(get_db)
):
    """Update an existing camera's fields."""
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(camera, field, value)
    db.commit()
    db.refresh(camera)
    return CameraResponse.model_validate(camera)

@router.get("/{camera_id}/preview", tags=["Cameras"])
def get_camera_preview(camera_id: int, db: Session = Depends(get_db)):
    """Get safe preview URL from authoritative catalog."""
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera {camera_id} not found")
    
    catalog = CameraCatalog(timeout_seconds=2.0)
    result = catalog.fetch()
    
    if not result.success:
        raise HTTPException(status_code=503, detail="Catalogue fetch failed or unavailable")
        
    for item in result.cameras:
        if item.camera_id == camera.camera_code:
            if not item.webrtc_url and not item.hls_url:
                break
            
            return {
                "camera_id": camera.id,
                "camera_code": camera.camera_code,
                "webrtc_url": item.webrtc_url,
                "hls_url": item.hls_url
            }
            
    raise HTTPException(status_code=404, detail="No safe preview URL available for this camera")
