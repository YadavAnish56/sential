"""
Sentinel CCTV API — Main FastAPI application.

This is the central application entry point that registers all routers,
configures middleware, and sets up the API.
"""

import logging
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# Ensure workspace root and backend are in sys.path
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ROOT_DIR = _BACKEND_DIR.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routes import health, cameras, events, vehicles, alerts, watchlist
from app.database.connection import SessionLocal
from app.services.anpr_worker import ANPRPersistenceWorker

try:
    from ai_engine.pipeline_manager import CameraPipelineManager
    from ai_engine.persistence_dispatcher import PersistenceDispatcher
    from ai_engine.detector import VehicleDetector
    from ai_engine.anpr.recognizer import EasyOCRPlateRecognizer
except ImportError:
    CameraPipelineManager = None
    PersistenceDispatcher = None
    VehicleDetector = None
    EasyOCRPlateRecognizer = None

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    worker = None
    dispatcher = None
    
    if CameraPipelineManager is not None and PersistenceDispatcher is not None and VehicleDetector is not None:
        logger.info("Starting ANPRPersistenceWorker...")
        worker = ANPRPersistenceWorker(session_factory=SessionLocal)
        worker.start()
        
        logger.info("Initializing shared VehicleDetector...")
        shared_detector = VehicleDetector()

        shared_recognizer = None
        if EasyOCRPlateRecognizer is not None:
            logger.info("Initializing shared EasyOCRPlateRecognizer...")
            try:
                shared_recognizer = EasyOCRPlateRecognizer()
                logger.info(
                    "EasyOCRPlateRecognizer initialized (status=%s, device=%s)",
                    shared_recognizer.status,
                    shared_recognizer.device,
                )
            except Exception as exc:
                logger.error("Failed to initialize EasyOCRPlateRecognizer: %s", exc, exc_info=True)
                shared_recognizer = None
        
        # Database-backed active watchlist determines whether an ANPR result generates an alert.
        dispatcher = PersistenceDispatcher(worker=worker, default_watchlist=None)
        
        app.state.pipeline_manager = CameraPipelineManager(
            detector=shared_detector,
            plate_recognizer=shared_recognizer,
            dispatcher=dispatcher,
        )
        app.state.anpr_worker = worker
        app.state.plate_recognizer = shared_recognizer
        logger.info("CameraPipelineManager initialized with shared VehicleDetector, EasyOCRPlateRecognizer, and PersistenceDispatcher in app.state.")
    else:
        app.state.pipeline_manager = None
        app.state.anpr_worker = None
        app.state.plate_recognizer = None
        logger.warning("CameraPipelineManager or PersistenceDispatcher not available.")
    
    yield
    
    # Shutdown
    if getattr(app.state, "pipeline_manager", None) is not None:
        logger.info("Stopping all camera pipelines...")
        app.state.pipeline_manager.stop_all(timeout_sec=5.0)
        logger.info("All camera pipelines stopped.")
        
    worker_to_stop = getattr(app.state, "anpr_worker", None)
    if worker_to_stop is not None:
        logger.info("Stopping ANPRPersistenceWorker...")
        worker_to_stop.stop()
        worker_to_stop.join(timeout=5.0)
        logger.info("ANPRPersistenceWorker stopped.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Unified CCTV Viewing & Selective Analytics Platform — Gujarat Police Innovation Challenge 2026",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS middleware for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes under /api prefix
app.include_router(health.router, prefix=settings.API_PREFIX)
app.include_router(cameras.router, prefix=settings.API_PREFIX)
app.include_router(events.router, prefix=settings.API_PREFIX)
app.include_router(vehicles.router, prefix=settings.API_PREFIX)
app.include_router(alerts.router, prefix=settings.API_PREFIX)
app.include_router(watchlist.router, prefix=settings.API_PREFIX)


@app.get("/", tags=["Root"])
def root():
    """Root endpoint — basic service info."""
    return {
        "message": "Sentinel CCTV API is running",
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }
