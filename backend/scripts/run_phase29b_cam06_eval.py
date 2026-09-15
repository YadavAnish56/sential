import os
import sys
import time
import json
import logging
import urllib.parse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sentinel.phase29b_eval")

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


def main():
    logger.info("==================================================")
    logger.info("PHASE 29B — REAL CAM-006 LIVE EVALUATION")
    logger.info("==================================================")

    # 1. Credentials
    user = os.environ.get("RTSP_USER", "")
    pwd = os.environ.get("RTSP_PASSWORD", "")
    logger.info("Credentials check: RTSP_USER set=%s, RTSP_PASSWORD set=%s", bool(user), bool(pwd))
    if not (user and pwd):
        logger.error("FATAL: Server RTSP credentials missing.")
        return 1

    safe_user = urllib.parse.quote(user, safe="")
    safe_pwd = urllib.parse.quote(pwd, safe="")
    stream_url = f"rtsp://{safe_user}:{safe_pwd}@103.250.160.189:8554/stream/cam06"

    # 2. Database baseline
    db = SessionLocal()
    # Check if a camera record for CAM-006 exists in DB, if not use CAM-001 or camera_code CAM-006
    cam = db.query(Camera).filter(Camera.camera_code == "CAM-006").first()
    camera_code_to_use = cam.camera_code if cam else "CAM-001"
    logger.info("Using camera code for persistence attribution: %s", camera_code_to_use)

    watch_entries = db.query(Watchlist).filter(Watchlist.is_active == True).all()
    active_watchlist_plates = [w.plate_number for w in watch_entries]
    logger.info("Active database watchlist count: %d (%s)", len(active_watchlist_plates), active_watchlist_plates)

    init_veh_count = db.query(Vehicle).count()
    init_evt_count = db.query(Event).count()
    init_alt_count = db.query(Alert).count()
    logger.info("Baseline DB counts -> Vehicles: %d, Events: %d, Alerts: %d", init_veh_count, init_evt_count, init_alt_count)

    # 3. Engines initialization
    cuda_avail = torch.cuda.is_available()
    logger.info("Initializing engines on CUDA: %s...", cuda_avail)
    detector = VehicleDetector(config=DetectorConfig(device="auto", confidence=0.35))
    plate_detector = PlateDetector(model_path="models/license_plate_detector.pt", confidence_threshold=0.20)
    recognizer = EasyOCRPlateRecognizer(languages=["en"], gpu=cuda_avail, allow_invalid_candidates=True, min_confidence=0.15)
    logger.info("EasyOCR status: %s, device: %s", recognizer.status, recognizer.device)

    anpr_coord = ANPRCoordinator(
        recognizer=recognizer,
        plate_detector=plate_detector,
        config=ANPRConfig(
            min_confidence=0.25,
            max_attempts=6,
            min_attempt_spacing_ms=200.0,
            early_lock_confidence=0.80,
        )
    )

    stream_proc = StreamProcessor(detector=detector, config=StreamProcessorConfig(frame_stride=1))
    tracker = VehicleTracker(config=TrackerConfig())
    pipeline = CameraPipeline(
        camera_id=camera_code_to_use,
        stream_processor=stream_proc,
        vehicle_tracker=tracker,
        anpr_coordinator=anpr_coord
    )

    worker = ANPRPersistenceWorker(session_factory=SessionLocal)
    worker.start()
    dispatcher = PersistenceDispatcher(worker=worker, default_watchlist=active_watchlist_plates)

    # 4. Connect RTSP
    logger.info("Connecting to CAM-006 RTSP via TCP...")
    pts_tracker = PTSTracker()
    client = RTSPClient(rtsp_url=stream_url, camera_id="cam06", transport="tcp")
    opened = client.open()
    if not opened:
        logger.error("FATAL: Failed to connect to CAM-006")
        worker.stop()
        db.close()
        return 1

    reader = FrameReader(client=client, pts_tracker=pts_tracker)
    logger.info("Connected to CAM-006: %sx%s, advisory_fps=%.1f, codec=%s", client.width, client.height, client.fps_hint, client.codec)

    evidence = {
        "camera_id": "CAM-006",
        "start_time": datetime.utcnow().isoformat(),
        "frames_total": 0,
        "frames_with_detections": 0,
        "total_vehicle_detections": 0,
        "unique_tracks": set(),
        "plate_evaluations": 0,
        "plate_detections": 0,
        "raw_ocr_candidates": [],
        "normalized_candidates": [],
        "valid_plates": [],
        "consensus_locks": [],
        "persisted_dispatches": 0,
        "track_summaries": {}
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

        pipe_res = pipeline.process_frame(packet)

        if pipe_res.detection_result and pipe_res.detection_result.count > 0:
            evidence["frames_with_detections"] += 1
            evidence["total_vehicle_detections"] += pipe_res.detection_result.count

        if pipe_res.tracking_result:
            for trk in pipe_res.tracking_result.active_tracks:
                evidence["unique_tracks"].add(trk.track_id)

        if pipe_res.anpr_results:
            for ar in pipe_res.anpr_results:
                evidence["plate_evaluations"] += 1
                if ar.plate_bbox:
                    evidence["plate_detections"] += 1

                if ar.raw_text:
                    evidence["raw_ocr_candidates"].append(ar.raw_text)
                    evidence["normalized_candidates"].append(ar.normalized_plate)
                    logger.info(
                        "Frame %d | Track %d | Raw: '%s' -> Norm: '%s' | Valid: %s | Conf: %.3f | Status: %s",
                        frame_idx, ar.track_id, ar.raw_text, ar.normalized_plate, ar.is_valid_format, ar.confidence, ar.status
                    )

                if ar.is_valid_format and ar.status == "RECOGNIZED":
                    evidence["valid_plates"].append(ar.normalized_plate)

        # Track consensus states from anpr_coordinator
        for key, state in anpr_coord._track_states.items():
            cam_id, trk_id = key
            if state.locked and state.recognized_plate:
                lock_info = f"Track {trk_id}: {state.recognized_plate} ({state.lock_method})"
                if lock_info not in evidence["consensus_locks"]:
                    evidence["consensus_locks"].append(lock_info)
                    logger.info(">>> CONSENSUS LOCK ACHIEVED: %s <<<", lock_info)

        # Dispatch recognized plates
        if pipe_res.recognized_plates:
            evidence["persisted_dispatches"] += len(pipe_res.recognized_plates)
            dispatcher.dispatch(
                pipeline_result=pipe_res,
                camera_code=camera_code_to_use,
                watchlist=active_watchlist_plates
            )

        if frame_idx % 25 == 0:
            logger.info(
                "Progress: %d/%d frames | Detections: %d | Tracks: %d | Evals: %d | Raw OCR: %d | Valid: %d | Locks: %d",
                frame_idx, TARGET_FRAMES,
                evidence["total_vehicle_detections"],
                len(evidence["unique_tracks"]),
                evidence["plate_evaluations"],
                len(evidence["raw_ocr_candidates"]),
                len(evidence["valid_plates"]),
                len(evidence["consensus_locks"])
            )

    client.close()
    logger.info("CAM-006 stream closed. Waiting for worker to flush...")
    time.sleep(3.0)
    worker.stop(timeout=5.0)

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

    recent_vehicles = db.query(Vehicle).order_by(Vehicle.id.desc()).limit(5).all()
    evidence["recent_vehicles"] = [{"id": v.id, "plate_number": v.plate_number, "first_seen": v.first_seen.isoformat() if v.first_seen else None} for v in recent_vehicles]

    recent_events = db.query(Event).order_by(Event.id.desc()).limit(5).all()
    evidence["recent_events"] = [{"id": e.id, "plate_number": e.plate_number, "confidence": e.confidence, "timestamp": e.timestamp.isoformat() if e.timestamp else None} for e in recent_events]

    recent_alerts = db.query(Alert).order_by(Alert.id.desc()).limit(5).all()
    evidence["recent_alerts"] = [{"id": a.id, "plate_number": a.plate_number, "severity": a.severity, "created_at": a.created_at.isoformat() if a.created_at else None} for a in recent_alerts]

    db.close()

    output_path = os.path.join(PROJECT_ROOT, "backend", "scripts", "phase29b_cam06_evidence.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2)

    logger.info("==================================================")
    logger.info("CAM-006 EVALUATION COMPLETE")
    logger.info("Frames processed: %d", evidence["frames_total"])
    logger.info("Vehicle detections: %d", evidence["total_vehicle_detections"])
    logger.info("Unique tracks: %d", len(evidence["unique_tracks"]))
    logger.info("Plate evaluations: %d", evidence["plate_evaluations"])
    logger.info("Raw OCR candidates: %d (%s)", len(evidence["raw_ocr_candidates"]), list(set(evidence["raw_ocr_candidates"])))
    logger.info("Normalized candidates: %d (%s)", len(evidence["normalized_candidates"]), list(set(evidence["normalized_candidates"])))
    logger.info("Valid Indian plates: %d (%s)", len(evidence["valid_plates"]), list(set(evidence["valid_plates"])))
    logger.info("Consensus locks: %d (%s)", len(evidence["consensus_locks"]), evidence["consensus_locks"])
    logger.info("Vehicles created in DB: %d", evidence["db_summary"]["vehicles_created"])
    logger.info("Events created in DB: %d", evidence["db_summary"]["events_created"])
    logger.info("Alerts created in DB: %d", evidence["db_summary"]["alerts_created"])
    logger.info("Evidence written to %s", output_path)
    logger.info("==================================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
