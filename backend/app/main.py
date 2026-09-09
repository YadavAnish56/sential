"""
Sentinel CCTV API — Main FastAPI application.

This is the central application entry point that registers all routers,
configures middleware, and sets up the API.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routes import health, cameras, events, vehicles, alerts
from app.database.connection import SessionLocal
from app.services.anpr_worker import ANPRPersistenceWorker

try:
    from ai_engine.pipeline_manager import CameraPipelineManager
    from ai_engine.persistence_dispatcher import PersistenceDispatcher
    from ai_engine.detector import VehicleDetector
except ImportError:
    CameraPipelineManager = None
    PersistenceDispatcher = None
    VehicleDetector = None

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
        
        # We can hardcode a small demo watchlist for the POC, or leave it None.
        # The frontend/AI assumes a watchlist triggers alerts.
        # Let's provide a default demo watchlist so testing triggers alerts if needed.
        dispatcher = PersistenceDispatcher(worker=worker, default_watchlist=["MH12AB1234", "ABC-123", "XYZ-999"])
        
        app.state.pipeline_manager = CameraPipelineManager(detector=shared_detector, dispatcher=dispatcher)
        app.state.anpr_worker = worker
        logger.info("CameraPipelineManager initialized with shared VehicleDetector and PersistenceDispatcher in app.state.")
    else:
        app.state.pipeline_manager = None
        app.state.anpr_worker = None
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


@app.get("/", tags=["Root"])
def root():
    """Root endpoint — basic service info."""
    return {
        "message": "Sentinel CCTV API is running",
        "version": settings.APP_VERSION,
        "docs": "/docs",
    }
