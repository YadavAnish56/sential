"""Health check endpoint for Sentinel API."""

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
        }
    }
