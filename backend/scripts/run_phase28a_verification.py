import os
import sys
import time
import json
import logging
from datetime import datetime

# Setup root path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sentinel.phase28a_verify")

import torch
from streaming.rtsp_client import RTSPClient
from streaming.frame_reader import FrameReader
from streaming.pts import PTSTracker
from ai_engine.config import DetectorConfig
from ai_engine.detector import VehicleDetector
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import VehicleTracker, TrackerConfig
from ai_engine.anpr import (
    ANPRCoordinator,
    ANPRConfig,
    EasyOCRPlateRecognizer,
    PlateDetector,
    normalize_plate,
)
from ai_engine.pipeline import CameraPipeline
from ai_engine.persistence_dispatcher import PersistenceDispatcher

from app.services.anpr_worker import ANPRPersistenceWorker
from app.database.connection import SessionLocal
from app.models.camera import Camera
from app.models.vehicle import Vehicle
from app.models.event import Event
from app.models.alert import Alert
from app.models.watchlist import Watchlist
from app.routes.cameras import _resolve_authenticated_rtsp_url


def main():
    logger.info("==================================================")
    logger.info("PHASE 28A — REAL CAM-001 END-TO-END VERIFICATION")
    logger.info("==================================================")

    # 1. Verify secrets presence (NEVER print values)
    rtsp_user_set = bool(os.environ.get("RTSP_USER"))
    rtsp_pass_set = bool(os.environ.get("RTSP_PASSWORD"))
    logger.info("Credentials check: RTSP_USER set=%s, RTSP_PASSWORD set=%s", rtsp_user_set, rtsp_pass_set)
    if not (rtsp_user_set and rtsp_pass_set):
        logger.error("FATAL: Server RTSP credentials missing.")
        return 1

    # 2. Verify CUDA and PyTorch
    cuda_avail = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_avail else "CPU"
    logger.info("PyTorch version: %s | CUDA available: %s (%s)", torch.__version__, cuda_avail, device_name)

    # 3. Database session & camera resolution
    db = SessionLocal()
    cam = db.query(Camera).filter(Camera.camera_code == "CAM-001").first()
    if not cam:
        logger.error("FATAL: CAM-001 not found in database.")
        db.close()
        return 1

    logger.info("Camera record resolved: code=%s, name=%s, stream_url=%s", cam.camera_code, cam.name, cam.stream_url)
    auth_stream_url = _resolve_authenticated_rtsp_url(cam)
    # Check that authenticated URL was resolved without printing secret
    logger.info("Authenticated RTSP URL resolved successfully in-memory.")

    # Check active watchlist in DB
    watch_entries = db.query(Watchlist).filter(Watchlist.is_active == True).all()
    active_watchlist_plates = [w.plate_number for w in watch_entries]
    logger.info("Active database watchlist plates count: %d (%s)", len(active_watchlist_plates), active_watchlist_plates)

    # Initial DB counts
    init_veh_count = db.query(Vehicle).count()
    init_evt_count = db.query(Event).count()
    init_alt_count = db.query(Alert).count()
    logger.info("Baseline DB counts -> Vehicles: %d, Events: %d, Alerts: %d", init_veh_count, init_evt_count, init_alt_count)

    # 4. Initialize AI components
    logger.info("Initializing VehicleDetector...")
    detector = VehicleDetector(config=DetectorConfig(device="auto", confidence=0.35))

    logger.info("Initializing PlateDetector...")
    plate_detector = PlateDetector(model_path="models/license_plate_detector.pt", confidence_threshold=0.25)
    logger.info("PlateDetector initialized: is_ready=%s, device=%s", plate_detector.is_ready, plate_detector.device)

    logger.info("Initializing EasyOCRPlateRecognizer...")
    recognizer = EasyOCRPlateRecognizer(languages=["en"], gpu=cuda_avail, allow_invalid_candidates=True, min_confidence=0.15)
    logger.info("EasyOCRPlateRecognizer status: %s, device: %s", recognizer.status, recognizer.device)

    logger.info("Initializing ANPRCoordinator...")
    anpr_coord = ANPRCoordinator(
        recognizer=recognizer,
        plate_detector=plate_detector,
        config=ANPRConfig(min_confidence=0.30, max_attempts=5, min_attempt_spacing_ms=300.0)
    )

    logger.info("Initializing StreamProcessor and VehicleTracker...")
    stream_proc = StreamProcessor(detector=detector, config=StreamProcessorConfig(frame_stride=1))
    tracker = VehicleTracker(config=TrackerConfig())

    logger.info("Initializing CameraPipeline...")
    pipeline = CameraPipeline(
        camera_id="CAM-001",
        stream_processor=stream_proc,
        vehicle_tracker=tracker,
        anpr_coordinator=anpr_coord
    )

    logger.info("Initializing ANPRPersistenceWorker & PersistenceDispatcher...")
    worker = ANPRPersistenceWorker(session_factory=SessionLocal)
    worker.start()
    dispatcher = PersistenceDispatcher(worker=worker, default_watchlist=active_watchlist_plates)

    # 5. Connect RTSP Client
    logger.info("Connecting RTSP Client via TCP...")
    pts_tracker = PTSTracker()
    client = RTSPClient(rtsp_url=auth_stream_url, camera_id="CAM-001", transport="tcp")
    opened = client.open()
    if not opened:
        logger.error("FATAL: RTSPClient failed to open stream.")
        worker.stop()
        db.close()
        return 1

    reader = FrameReader(client=client, pts_tracker=pts_tracker)
    logger.info("RTSP stream opened successfully: width=%s, height=%s, fps=%s", client.width, client.height, client.fps_hint)

    # 6. Real Camera Ingestion & Processing Loop
    evidence = {
        "camera_id": "CAM-001",
        "start_time": datetime.utcnow().isoformat(),
        "frames_total": 0,
        "frames_with_detections": 0,
        "total_vehicle_detections": 0,
        "unique_tracks": set(),
        "plate_evaluations": 0,
        "plates_detected": 0,
        "plate_detections_detail": [],
        "ocr_invocations": 0,
        "ocr_candidates_detail": [],
        "valid_plates_count": 0,
        "valid_plates_list": [],
        "pts_history": [],
        "dispatch_calls": 0,
        "errors": []
    }

    TARGET_FRAMES = 150
    t_start = time.time()
    logger.info("Starting frame processing loop (Target: %d frames)...", TARGET_FRAMES)

    frame_idx = 0
    while frame_idx < TARGET_FRAMES and (time.time() - t_start < 45.0):
        success, packet = reader.read_packet()
        if not success or packet is None or packet.frame is None:
            time.sleep(0.01)
            continue

        frame_idx += 1
        evidence["frames_total"] += 1
        evidence["pts_history"].append(packet.pts_ms)

        # Run pipeline pass
        pipe_result = pipeline.process_frame(packet)

        # Detection telemetry
        if pipe_result.detection_result and pipe_result.detection_result.count > 0:
            evidence["frames_with_detections"] += 1
            evidence["total_vehicle_detections"] += pipe_result.detection_result.count

        # Tracking telemetry
        if pipe_result.tracking_result:
            for trk in pipe_result.tracking_result.active_tracks:
                evidence["unique_tracks"].add(trk.track_id)

        # ANPR Telemetry
        if pipe_result.anpr_results:
            for ar in pipe_result.anpr_results:
                evidence["plate_evaluations"] += 1
                det_info = {
                    "frame": frame_idx,
                    "pts_ms": packet.pts_ms,
                    "track_id": ar.track_id,
                    "status": ar.status,
                    "plate_bbox": ar.plate_bbox,
                    "raw_text": ar.raw_text,
                    "normalized_plate": ar.normalized_plate,
                    "confidence": ar.confidence,
                    "is_valid_format": ar.is_valid_format,
                }
                if ar.plate_bbox:
                    evidence["plates_detected"] += 1
                    evidence["plate_detections_detail"].append(det_info)

                if ar.raw_text:
                    evidence["ocr_invocations"] += 1
                    evidence["ocr_candidates_detail"].append(det_info)

                if ar.is_valid_format and ar.status == "RECOGNIZED":
                    evidence["valid_plates_count"] += 1
                    evidence["valid_plates_list"].append(ar.normalized_plate)

        # Dispatch if recognized plates exist
        if pipe_result.recognized_plates:
            evidence["dispatch_calls"] += 1
            dispatcher.dispatch(
                pipeline_result=pipe_result,
                camera_code="CAM-001",
                watchlist=active_watchlist_plates
            )

        if frame_idx % 25 == 0:
            logger.info(
                "Progress: %d/%d frames | Detections: %d | Tracks: %d | Plate Evals: %d | OCR Invocations: %d | Valid: %d",
                frame_idx, TARGET_FRAMES,
                evidence["total_vehicle_detections"],
                len(evidence["unique_tracks"]),
                evidence["plate_evaluations"],
                evidence["ocr_invocations"],
                evidence["valid_plates_count"]
            )

    # 7. Cleanup streaming
    client.close()
    logger.info("RTSP stream closed. Waiting for persistence queue flush...")
    time.sleep(2.0)
    worker.stop(timeout=5.0)

    # 8. Post-run DB query
    post_veh_count = db.query(Vehicle).count()
    post_evt_count = db.query(Event).count()
    post_alt_count = db.query(Alert).count()

    evidence["unique_tracks"] = list(evidence["unique_tracks"])
    evidence["db_summary"] = {
        "vehicles_before": init_veh_count,
        "vehicles_after": post_veh_count,
        "vehicles_created": post_veh_count - init_veh_count,
        "events_before": init_evt_count,
        "events_after": post_evt_count,
        "events_created": post_evt_count - init_evt_count,
        "alerts_before": init_alt_count,
        "alerts_after": post_alt_count,
        "alerts_created": post_alt_count - init_alt_count,
    }

    # Query recent records
    recent_events = db.query(Event).order_by(Event.id.desc()).limit(5).all()
    evidence["recent_events"] = [
        {"id": e.id, "plate_number": e.plate_number, "confidence": e.confidence, "timestamp": e.timestamp.isoformat() if e.timestamp else None}
        for e in recent_events
    ]
    recent_alerts = db.query(Alert).order_by(Alert.id.desc()).limit(5).all()
    evidence["recent_alerts"] = [
        {"id": a.id, "plate_number": a.plate_number, "severity": a.severity, "created_at": a.created_at.isoformat() if a.created_at else None}
        for a in recent_alerts
    ]

    db.close()

    # Save evidence file
    evidence_path = os.path.join(PROJECT_ROOT, "backend", "scripts", "phase28a_evidence.json")
    with open(evidence_path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2)

    logger.info("==================================================")
    logger.info("EVALUATION FINISHED")
    logger.info("Frames processed: %d", evidence["frames_total"])
    logger.info("Vehicle detections: %d", evidence["total_vehicle_detections"])
    logger.info("Unique tracks: %d", len(evidence["unique_tracks"]))
    logger.info("Plate evaluations: %d", evidence["plate_evaluations"])
    logger.info("Plates detected: %d", evidence["plates_detected"])
    logger.info("OCR invocations: %d", evidence["ocr_invocations"])
    logger.info("Valid plates: %d", evidence["valid_plates_count"])
    logger.info("Vehicles created: %d", evidence["db_summary"]["vehicles_created"])
    logger.info("Events created: %d", evidence["db_summary"]["events_created"])
    logger.info("Alerts created: %d", evidence["db_summary"]["alerts_created"])
    logger.info("Evidence written to %s", evidence_path)
    logger.info("==================================================")

    return 0

if __name__ == "__main__":
    sys.exit(main())
