import base64
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    from ai_engine.pipeline_manager import CameraPipelineManager, PipelineConfig
except ImportError:
    CameraPipelineManager = None
    PipelineConfig = None

from app.database.dependencies import get_db
from app.models.camera import Camera
from app.models.camera_status import CameraStatusHistory
from app.models.event import Event
from app.models.alert import Alert
from streaming.camera_catalog import CameraCatalog
from app.schemas.camera import (
    CameraCreate,
    CameraUpdate,
    CameraResponse,
    CameraListResponse,
    CameraMapMarker,
    CatalogSyncResponse,
    sanitize_coordinates,
)
from app.services.catalog_sync import CameraCatalogSyncService

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


@router.get("/map", response_model=list[CameraMapMarker], tags=["Cameras", "GIS"])
def get_cameras_map(
    only_mapped: bool = Query(False, description="Filter to only return cameras with valid coordinates"),
    status: str | None = Query(None, description="Filter by camera status"),
    db: Session = Depends(get_db),
):
    """
    Retrieve camera records suitable for GIS map markers.
    Omits credentials and internal RTSP stream URLs.
    Cameras without valid coordinates will not produce invalid map coordinates.
    """
    query = db.query(Camera)
    if status:
        query = query.filter(Camera.status == status)
    cameras = query.order_by(Camera.id).all()

    markers: list[CameraMapMarker] = []
    for cam in cameras:
        lat, lon = sanitize_coordinates(cam.latitude, cam.longitude)
        if only_mapped and (lat is None or lon is None):
            continue
        markers.append(
            CameraMapMarker(
                id=cam.id,
                camera_code=cam.camera_code,
                name=cam.name,
                location=cam.location,
                latitude=lat,
                longitude=lon,
                status=cam.status,
            )
        )
    return markers


@router.post("/sync-catalogue", response_model=CatalogSyncResponse, tags=["Cameras", "Catalogue"])
def sync_camera_catalogue(
    request: Request,
    custom_url: str | None = Query(None, description="Optional custom catalogue URL override (server-side only)"),
    db: Session = Depends(get_db),
):
    """
    Synchronize cameras from the authoritative government catalogue into PostgreSQL.
    Primary authentication is loaded securely server-side from RTSP_CATALOG_TOKEN.
    Optional X-Catalog-Token header is permitted only for controlled server/operator calls.
    Never accepts credentials in URL query parameters.
    """
    operator_token = request.headers.get("X-Catalog-Token")
    sync_service = CameraCatalogSyncService(db=db)
    result = sync_service.sync(custom_url=custom_url, auth_token=operator_token)
    return result


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


def _resolve_camera(camera_id: str | int, db: Session) -> Camera:
    """
    Resolve camera database entity from either:
    - Database integer primary key (e.g. 1, 2)
    - Camera code / catalogue ID (e.g. "CAM-001", "CAM-01-SG-HIGHWAY")
    """
    cid_str = str(camera_id).strip()
    camera = None
    if cid_str.isdigit():
        camera = db.query(Camera).filter(Camera.id == int(cid_str)).first()
    if not camera:
        camera = db.query(Camera).filter(Camera.camera_code == cid_str).first()
    if not camera:
        camera = db.query(Camera).filter(Camera.camera_code.ilike(cid_str)).first()
    if not camera:
        raise HTTPException(status_code=404, detail=f"Camera with identifier '{camera_id}' not found")
    return camera


def _resolve_upstream_stream(camera: Camera) -> str:
    """
    Deterministic server-side resolution from camera record to upstream government stream ID.
    Explicitly separates Sentinel DB ID, camera_code, external catalogue ID, and stream ID.
    Examples:
      - stream_url contains /stream/cam01 -> cam01
      - camera_code 'cam01' -> cam01
      - camera_code 'CAM-001' -> cam01
      - camera_code 'CAM-01-SG-HIGHWAY' -> cam01
      - camera_code 'cam30' -> cam30
      - camera_code '30' or camera.id 30 -> cam30
    """
    # 1. Direct stream URL path if explicitly specified (e.g. rtsp://.../stream/cam01)
    if camera.stream_url:
        stream_m = re.search(r"/stream/(cam\d+|\d+)", camera.stream_url, re.IGNORECASE)
        if stream_m:
            raw_match = stream_m.group(1).lower()
            if raw_match.startswith("cam"):
                num = raw_match.replace("cam", "")
                if num.isdigit():
                    return f"cam{int(num):02d}"
            elif raw_match.isdigit():
                return f"cam{int(raw_match):02d}"
        if "/stream/" in camera.stream_url:
            path = camera.stream_url.split("/stream/")[-1].strip("/").split("?")[0]
            if path:
                return path

    # 2. Match standard government camXX convention from camera_code
    code = (camera.camera_code or "").strip()

    # 2a. Direct cam01..cam30 format (e.g., 'cam01', 'cam1', 'CAM30')
    cam_match = re.match(r"^cam0*([1-9]\d*)$", code, re.IGNORECASE)
    if cam_match:
        return f"cam{int(cam_match.group(1)):02d}"

    # 2b. Standard CAM-001 or CAM-01-SG-HIGHWAY pattern (extract primary camera index)
    cam_code_match = re.match(r"^CAM-?0*([1-9]\d*)(?:-.*)?$", code, re.IGNORECASE)
    if cam_code_match:
        return f"cam{int(cam_code_match.group(1)):02d}"

    # 2c. Pure numeric code
    if code.isdigit():
        return f"cam{int(code):02d}"

    # 2d. Fallback: if camera.id is 1..30 and code starts with CAM
    if camera.id and 1 <= camera.id <= 30 and code.upper().startswith("CAM"):
        return f"cam{camera.id:02d}"

    # Fallback to sanitized code
    code_clean = code.lower().replace("-", "").replace(" ", "").replace("_", "")
    return code_clean or f"cam{camera.id:02d}"


def _get_gateway_base() -> str:
    """Resolve active stream gateway base URL safely on the backend."""
    gateway_base = os.environ.get("WHEP_GATEWAY_URL")
    if not gateway_base:
        gateway_base = getattr(settings, "WHEP_GATEWAY_URL", "http://103.250.160.189:8889")
    return gateway_base.rstrip("/")


@router.get("/{camera_id}/pipeline-status", tags=["Pipelines"])
def get_camera_pipeline_status(camera_id: str, request: Request, db: Session = Depends(get_db)):
    """Retrieve health and telemetry for a specific camera pipeline."""
    manager = _get_manager(request)
    camera = _resolve_camera(camera_id, db)
    
    health = manager.get_health(camera.camera_code)
    if health is None:
        return {"status": "unregistered"}
    return health


def _resolve_authenticated_rtsp_url(camera: Camera) -> str:
    """
    Resolve server-side authenticated RTSP URL for live AI ingestion.
    Retrieves RTSP_USER and RTSP_PASSWORD from server environment/settings.
    Constructs authenticated URL strictly in-memory.
    Never alters database records, never returns to frontend, never logs credentials.
    """
    if not camera.stream_url:
        raise HTTPException(status_code=400, detail=f"Camera {camera.id or camera.camera_code} has no stream_url")

    parsed = urllib.parse.urlsplit(camera.stream_url)
    if parsed.scheme.lower() not in ("rtsp", "rtsps"):
        return camera.stream_url

    # If credentials already present in stream_url, return as-is
    if parsed.username and parsed.password:
        return camera.stream_url

    user = os.environ.get("RTSP_USER", "") or getattr(settings, "RTSP_USER", "") or ""
    pwd = os.environ.get("RTSP_PASSWORD", "") or getattr(settings, "RTSP_PASSWORD", "") or ""

    is_mock = parsed.hostname in ("mock", "testserver")
    is_gateway = (
        parsed.hostname == "103.250.160.189"
        or (parsed.hostname and "103.250.160.189" in parsed.hostname)
        or (parsed.hostname not in ("localhost", "127.0.0.1", "mock", "testserver"))
    )

    if not user or not pwd:
        if is_gateway and not is_mock:
            logger.warning("Upstream RTSP credentials missing for camera %s", camera.camera_code)
            raise HTTPException(
                status_code=502,
                detail="AUTHENTICATION_REQUIRED: Stream gateway authentication required. Verify server credentials."
            )
        return camera.stream_url

    safe_user = urllib.parse.quote(user, safe="")
    safe_pwd = urllib.parse.quote(pwd, safe="")

    # Resolve target stream path
    path = parsed.path
    if not path or path in ("/", "/stream", "/stream/"):
        stream_name = _resolve_upstream_stream(camera)
        path = f"/stream/{stream_name}"

    host = parsed.hostname or ""
    if parsed.port:
        netloc = f"{safe_user}:{safe_pwd}@{host}:{parsed.port}"
    else:
        netloc = f"{safe_user}:{safe_pwd}@{host}"

    return urllib.parse.urlunsplit((parsed.scheme, netloc, path, parsed.query, parsed.fragment))


@router.post("/{camera_id}/start", tags=["Pipelines"])
def start_camera_pipeline(
    camera_id: str,
    request: Request,
    enable_anpr: bool = Query(True, description="Enable or disable ANPR for this camera session"),
    frame_stride: int = Query(1, ge=1, le=30, description="Process 1 of every N frames"),
    db: Session = Depends(get_db),
):
    """Start the pipeline for a specific camera."""
    manager = _get_manager(request)
    camera = _resolve_camera(camera_id, db)
    
    if not camera.stream_url:
        raise HTTPException(status_code=400, detail=f"Camera {camera_id} has no stream_url")

    runtime_rtsp_url = _resolve_authenticated_rtsp_url(camera)

    # If an existing session is registered but stopped or in error, remove it so new config takes effect
    existing_session = manager.get_session(camera.camera_code)
    if existing_session and not existing_session.is_running:
        if hasattr(manager, "unregister_camera"):
            manager.unregister_camera(camera.camera_code)
    
    config = PipelineConfig(
        camera_id=camera.camera_code,
        rtsp_url=runtime_rtsp_url,
        enable_anpr=enable_anpr,
        frame_stride=frame_stride,
    )
    
    # Register safely (manager handles duplicates)
    manager.register_camera(config=config, auto_start=False)
    success = manager.start_camera(camera.camera_code)
    
    if not success:
        # It might be already running or failed to start
        session = manager.get_session(camera.camera_code)
        if session and session.is_running:
            return {"message": f"Camera {camera.camera_code} pipeline is already running."}
        raw_err = getattr(session, "error_message", None) or f"Failed to start pipeline for camera {camera.camera_code}"
        safe_err = re.sub(r"://([^:@\s]+):([^@\s]+)@", r"://***:***@", str(raw_err))
        raise HTTPException(status_code=500, detail=safe_err)
    
    return {"message": f"Camera {camera.camera_code} pipeline started."}


@router.post("/{camera_id}/stop", tags=["Pipelines"])
def stop_camera_pipeline(camera_id: str, request: Request, db: Session = Depends(get_db)):
    """Stop the pipeline for a specific camera."""
    manager = _get_manager(request)
    camera = _resolve_camera(camera_id, db)
    
    success = manager.stop_camera(camera.camera_code)
    if not success:
        # Maybe it's not running or unknown
        return {"message": f"Camera {camera.camera_code} pipeline is not running or unknown."}
    
    return {"message": f"Camera {camera.camera_code} pipeline stopped."}


@router.get("/{camera_id}", response_model=CameraResponse)
def get_camera(camera_id: str, db: Session = Depends(get_db)):
    """Get a single camera by ID or camera code."""
    camera = _resolve_camera(camera_id, db)
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
    camera_id: str, payload: CameraUpdate, db: Session = Depends(get_db)
):
    """Update an existing camera's fields."""
    camera = _resolve_camera(camera_id, db)
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(camera, field, value)
    db.commit()
    db.refresh(camera)
    return CameraResponse.model_validate(camera)


@router.delete("/{camera_id}", tags=["Cameras"])
def delete_camera(
    camera_id: str,
    force: bool = Query(
        False,
        description="Delete the camera even though sightings reference it. "
                    "The sightings themselves are always kept.",
    ),
    db: Session = Depends(get_db),
    request: Request = None,
):
    """
    Remove a camera from the registry.

    Any running pipeline is stopped first. Recorded sightings and alerts are
    evidence, so they are never deleted along with the camera: if any exist the
    request is refused with their counts, and `force=true` is required to
    proceed. Forcing retires the camera instead of erasing it, which keeps the
    rows those sightings point at intact.
    """
    camera = _resolve_camera(camera_id, db)

    # Stop the pipeline first so no worker keeps writing against this camera.
    try:
        manager = _get_manager(request) if request is not None else None
        if manager is not None:
            manager.stop_camera(camera.camera_code)
    except Exception as exc:
        logger.warning("Could not stop pipeline for %s during delete: %s", camera.camera_code, exc)

    event_count = db.query(Event).filter(Event.camera_id == camera.id).count()
    alert_count = db.query(Alert).filter(Alert.camera_id == camera.id).count()
    referenced = event_count + alert_count

    if referenced and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Camera '{camera.camera_code}' has recorded evidence and was not removed."
                ),
                "events": event_count,
                "alerts": alert_count,
                "hint": "Retry with force=true to retire the camera. Its sightings are kept either way.",
            },
        )

    code = camera.camera_code

    if referenced:
        # Retire rather than delete: the evidence rows still point at this row.
        camera.status = "retired"
        db.commit()
        return {
            "message": f"Camera {code} retired. Its {referenced} recorded item(s) were kept.",
            "retired": True,
            "deleted": False,
            "events": event_count,
            "alerts": alert_count,
        }

    db.query(CameraStatusHistory).filter(CameraStatusHistory.camera_id == camera.id).delete(
        synchronize_session=False
    )
    db.delete(camera)
    db.commit()
    return {
        "message": f"Camera {code} removed.",
        "retired": False,
        "deleted": True,
        "events": 0,
        "alerts": 0,
    }


@router.get("/{camera_id}/preview", tags=["Cameras"])
def get_camera_preview(camera_id: str, db: Session = Depends(get_db)):
    """Get safe preview URL from authoritative catalog."""
    camera = _resolve_camera(camera_id, db)
    
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


@router.post("/{camera_id}/whep", tags=["Cameras", "Streaming"])
async def proxy_camera_whep(camera_id: str, request: Request, db: Session = Depends(get_db)):
    """
    Secure WHEP Proxy:
    Receives WebRTC SDP offer from frontend browser, resolves target camera on gateway,
    injects backend-held credentials (Basic Auth), negotiates SDP with upstream MediaMTX,
    and returns SDP answer. Zero credentials or internal gateway secrets are exposed.
    """
    camera = _resolve_camera(camera_id, db)

    offer_body = await request.body()
    if not offer_body or not offer_body.strip():
        raise HTTPException(status_code=400, detail="SDP offer body is required")

    stream_name = _resolve_upstream_stream(camera)
    gateway_base = _get_gateway_base()
    target_url = f"{gateway_base}/stream/{stream_name}/whep"

    user = os.environ.get("RTSP_USER", "") or getattr(settings, "RTSP_USER", "") or ""
    pwd = os.environ.get("RTSP_PASSWORD", "") or getattr(settings, "RTSP_PASSWORD", "") or ""

    if not user or not pwd:
        logger.warning("Upstream WHEP credentials missing: reporting AUTHENTICATION_REQUIRED")
        raise HTTPException(
            status_code=502,
            detail="AUTHENTICATION_REQUIRED: Stream gateway authentication required. Verify server credentials."
        )

    req = urllib.request.Request(
        target_url,
        data=offer_body,
        headers={"Content-Type": "application/sdp"},
        method="POST"
    )

    auth_bytes = f"{user}:{pwd}".encode("utf-8")
    req.add_header("Authorization", f"Basic {base64.b64encode(auth_bytes).decode('ascii')}")

    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            answer_sdp = resp.read()
            return Response(content=answer_sdp, media_type="application/sdp", status_code=201)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            logger.warning("Upstream WHEP 401 for camera %s (auth required)", camera_id)
            raise HTTPException(
                status_code=502,
                detail="AUTHENTICATION_REQUIRED: Stream gateway authentication required. Verify server credentials."
            )
        elif e.code == 404:
            logger.warning("Upstream WHEP 404 for camera %s: stream '%s' unavailable", camera_id, stream_name)
            raise HTTPException(
                status_code=502,
                detail=f"UPSTREAM_NOT_FOUND: Stream '{stream_name}' unavailable on gateway (HTTP 404)."
            )
        elif e.code == 500:
            logger.warning("Upstream WHEP 500 for camera %s: stream '%s' internal gateway error", camera_id, stream_name)
            raise HTTPException(
                status_code=502,
                detail=f"STREAM_GATEWAY_ERROR: Upstream stream gateway error for '{stream_name}' (HTTP 500)."
            )
        raise HTTPException(status_code=502, detail=f"STREAM_GATEWAY_ERROR: Upstream stream gateway error (HTTP {e.code}).")
    except urllib.error.URLError as e:
        logger.warning("Upstream WHEP connection failed: %s", e.reason)
        raise HTTPException(status_code=504, detail="NETWORK_ERROR: Upstream stream gateway unreachable.")
    except Exception as e:
        logger.error("WHEP proxy error: %s", e)
        raise HTTPException(status_code=500, detail="INTERNAL_ERROR: Error establishing stream preview.")
