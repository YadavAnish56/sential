"""
Sentinel — Phase 6D.4 Controlled End-to-End Pipeline Validation Script.

Exercises the complete single-camera processing-to-database path:
    Synthetic FramePacket
        ↓
    CameraPipeline (StreamProcessor → VehicleTracker → ANPRCoordinator)
        ↓
    ANPRResult
        ↓
    ANPRPersistenceService
        ↓
    In-memory SQLite database
        ↓
    Vehicle + Event
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# Ensure project root and backend root are in sys.path
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for path_str in [str(ROOT), str(BACKEND)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from streaming.frame_reader import FramePacket
from ai_engine.schemas import Detection, DetectionResult
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import VehicleTracker, TrackerConfig
from ai_engine.anpr import ANPRCoordinator, ANPRConfig
from ai_engine.anpr.schemas import PlateCandidate, ANPRResult
from ai_engine.anpr.recognizer import MockPlateRecognizer
from ai_engine.pipeline import CameraPipeline, PipelineResult

from app.database.connection import Base
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.services.anpr_service import ANPRPersistenceService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sentinel.scripts.test_e2e_pipeline")


class DeterministicDetectorStub:
    """Deterministic vehicle detector test double."""

    def __init__(self, detections: list[Detection]) -> None:
        self.detections = detections

    def detect(self, packet: FramePacket) -> DetectionResult:
        current_dets = [
            Detection(
                class_id=d.class_id,
                class_name=d.class_name,
                confidence=d.confidence,
                x1=d.x1,
                y1=d.y1,
                x2=d.x2,
                y2=d.y2,
                camera_id=packet.camera_id,
                pts_ms=packet.pts_ms,
            )
            for d in self.detections
        ]
        return DetectionResult(
            camera_id=packet.camera_id,
            pts_ms=packet.pts_ms,
            detections=current_dets,
            inference_time_ms=2.0,
            device="cpu",
            model_name="deterministic-stub",
            is_discontinuity=packet.is_discontinuity,
        )


def run_e2e_validation() -> bool:
    print("=" * 65)
    print("SENTINEL -- PHASE 6D.4: CONTROLLED E2E PIPELINE VALIDATION")
    print("=" * 65)

    # 1. Setup SQLite In-Memory Database with Foreign Keys
    print("\n[1/5] Initializing in-memory SQLite database...")
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()

    # Seed test camera
    test_cam = Camera(
        camera_code="CAM-01",
        name="Main Gate North",
        location="North Gate",
        status="active",
    )
    db.add(test_cam)
    db.commit()
    db.refresh(test_cam)
    print(f"  [OK] Seeded Camera: id={test_cam.id}, code='{test_cam.camera_code}'")

    # 2. Build Pipeline
    print("\n[2/5] Constructing CameraPipeline with real orchestrators...")
    stub_detector = DeterministicDetectorStub(
        detections=[
            Detection(
                class_id=2,
                class_name="car",
                confidence=0.92,
                x1=200.0,
                y1=200.0,
                x2=600.0,
                y2=500.0,
                camera_id="CAM-01",
                pts_ms=1000.0,
            )
        ]
    )
    stream_processor = StreamProcessor(
        detector=stub_detector,
        config=StreamProcessorConfig(frame_stride=1),
    )
    vehicle_tracker = VehicleTracker(config=TrackerConfig(min_hits=1))
    plate_recognizer = MockPlateRecognizer(
        default_candidate=PlateCandidate(
            raw_text="GJ05AB1234",
            normalized_plate="GJ05AB1234",
            confidence=0.94,
            is_valid_format=True,
            pts_ms=1000.0,
        )
    )
    anpr_coordinator = ANPRCoordinator(
        recognizer=plate_recognizer,
        config=ANPRConfig(min_confidence=0.70, min_crop_quality=0.10),
    )
    pipeline = CameraPipeline(
        camera_id="CAM-01",
        stream_processor=stream_processor,
        vehicle_tracker=vehicle_tracker,
        anpr_coordinator=anpr_coordinator,
    )
    print("  [OK] Pipeline assembled: StreamProcessor + VehicleTracker + ANPRCoordinator")

    # 3. Process Frame 1
    print("\n[3/5] Processing synthetic FramePacket through pipeline...")
    frame_array = np.random.RandomState(42).randint(0, 255, (720, 1280, 3), dtype=np.uint8)
    packet1 = FramePacket(
        frame=frame_array,
        pts_ms=1000.0,
        received_at=time.time(),
        width=1280,
        height=720,
        camera_id="CAM-01",
    )
    result1: PipelineResult = pipeline.process_frame(packet1)
    print(f"  [OK] Frame processed in {result1.pipeline_time_ms:.2f} ms")
    print(f"    - has_detections: {result1.has_detections} (count: {result1.detection_result.count if result1.detection_result else 0})")
    print(f"    - has_tracks: {result1.has_tracks} (active: {result1.tracking_result.active_count if result1.tracking_result else 0})")
    print(f"    - has_plates: {result1.has_plates} (recognized: {len(result1.recognized_plates)})")

    if not result1.recognized_plates:
        print("  [FAIL] ERROR: No recognized plates produced!")
        return False

    anpr1 = result1.recognized_plates[0]
    print(f"  [OK] ANPR Result: plate='{anpr1.normalized_plate}', conf={anpr1.confidence:.2f}, status='{anpr1.status}'")

    # 4. Persistence Service
    print("\n[4/5] Persisting ANPR result to SQLite database...")
    persistence_service = ANPRPersistenceService(db)
    persist_outcome1 = persistence_service.persist_result(
        anpr_result=anpr1,
        snapshot_path="/snapshots/cam01/1000.jpg",
    )
    if not persist_outcome1:
        print("  [FAIL] ERROR: Persistence service returned None!")
        return False

    print(f"  [OK] Persisted Event id={persist_outcome1.event_id}, Vehicle id={persist_outcome1.vehicle_id}")

    # Process Frame 2 (Deduplication check)
    print("\n[5/5] Processing Frame 2 to verify vehicle deduplication...")
    # Release lock so recognizer evaluates second frame
    track_state = anpr_coordinator.get_track_state("CAM-01", 1)
    if track_state:
        track_state.locked = False
        track_state.last_attempt_pts_ms = 0.0

    packet2 = FramePacket(
        frame=frame_array,
        pts_ms=1500.0,
        received_at=time.time(),
        width=1280,
        height=720,
        camera_id="CAM-01",
    )
    result2 = pipeline.process_frame(packet2)
    persist_outcome2 = persistence_service.persist_result(result2.recognized_plates[0])

    # Database Verification
    vehicles = db.query(Vehicle).all()
    events = db.query(Event).all()
    print(f"  [OK] Total Vehicles in database: {len(vehicles)} (expected: 1)")
    print(f"  [OK] Total Events in database: {len(events)} (expected: 2)")

    assert len(vehicles) == 1, f"Expected 1 vehicle, got {len(vehicles)}"
    assert len(events) == 2, f"Expected 2 events, got {len(events)}"
    assert events[0].vehicle_id == vehicles[0].id
    assert events[1].vehicle_id == vehicles[0].id
    assert vehicles[0].plate_number == "GJ05AB1234"

    print("\n" + "=" * 65)
    print("CONTROLLED E2E PIPELINE VALIDATION SUCCESSFUL!")
    print("=" * 65)
    return True


if __name__ == "__main__":
    success = run_e2e_validation()
    sys.exit(0 if success else 1)
