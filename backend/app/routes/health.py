"""Health check endpoint for Sentinel API."""

import os

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database.dependencies import get_db

router = APIRouter(tags=["Health"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    System health check.
    Returns API status and database connectivity.
    """
    db_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "unhealthy"

    return {
        "status": "healthy",
        "database": db_status,
        "service": "Sentinel CCTV API",
    }


@router.get("/health/system")
def system_health(request: Request):
    """
    System telemetry and operational health check.
    Returns global persistence worker status.
    """
    worker = getattr(request.app.state, "anpr_worker", None)
    
    worker_alive = False
    queue_size = 0
    if worker is not None:
        worker_alive = worker.is_alive()
        queue_size = worker.qsize
        
    return {
        "anpr_persistence_worker": {
            "is_alive": worker_alive,
            "queue_size": queue_size
        },
        "plate_recognition": _plate_recognition_status(request),
    }


def _plate_recognition_status(request: Request) -> dict:
    """
    Report whether plate recognition can actually read a plate.

    Without the plate-detector weights the coordinator falls back to running OCR
    across the whole vehicle, which effectively never yields a valid
    registration. Detection and tracking still work, so the system looks busy
    while never recording a sighting. This says so plainly instead.
    """
    detail = {
        "ready": False,
        "plate_detector_ready": False,
        "recognizer_ready": False,
        "model_path": None,
        "reason": None,
    }

    try:
        from ai_engine.config import resolve_model_path

        model_path = resolve_model_path(
            os.environ.get("ANPR_MODEL_PATH") or "models/license_plate_detector.pt",
            "models/license_plate_detector.pt",
        )
        detail["model_path"] = model_path
        detail["plate_detector_ready"] = os.path.isfile(model_path)
    except Exception as exc:
        detail["reason"] = f"Could not resolve the plate model path: {exc}"
        return detail

    # The recognizer object exists even when its own weights failed to load, so
    # ask it whether it is actually usable rather than merely present.
    recognizer = getattr(request.app.state, "plate_recognizer", None)
    if recognizer is None:
        detail["recognizer_ready"] = False
    else:
        ready_flag = getattr(recognizer, "is_ready", None)
        detail["recognizer_ready"] = bool(ready_flag) if ready_flag is not None else True

    missing = []
    if not detail["plate_detector_ready"]:
        missing.append(
            f"the plate-detector weights (expected at {detail['model_path']})"
        )
    if not detail["recognizer_ready"]:
        missing.append(
            "the text-recognition weights (EasyOCR needs its models present "
            "locally while downloads are disabled)"
        )

    if missing:
        detail["missing"] = missing
        detail["reason"] = (
            "Plates cannot be read because "
            + " and ".join(missing)
            + " are unavailable. Vehicles are still detected and tracked, but no "
              "sighting will be recorded until this is resolved."
        )
    else:
        detail["ready"] = True

    return detail
