"""
Phase 29D — Calibrated ANPR Consensus Threshold Validation on Real CAM-006.

Executes a bounded live test against the real CAM-006 stream using:
- candidate admission floor = 0.20
- minimum agreeing observations = 3
- minimum agreement ratio = 60%
- high-confidence FAST_LOCK = 0.85
- strict Indian plate validation
- PostgreSQL persistence and active database watchlist

Records telemetry:
- frames processed
- OCR candidates
- confidence distribution
- normalized candidates
- consensus groups
- recognition events
- persisted vehicles
- persisted events
- watchlist matches
- alerts
"""

import json
import logging
import os
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import torch

# Ensure workspace root and backend are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
for path_str in [str(PROJECT_ROOT), str(BACKEND_DIR)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.core.config import settings
from app.database.connection import SessionLocal
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.models.watchlist import Watchlist
from app.services.anpr_worker import ANPRPersistenceWorker

from ai_engine.anpr.coordinator import ANPRCoordinator
from ai_engine.anpr.normalization import normalize_plate
from ai_engine.anpr.plate_detector import PlateDetector
from ai_engine.anpr.recognizer import EasyOCRPlateRecognizer
from ai_engine.anpr.schemas import ANPRConfig
from ai_engine.detector import DetectorConfig, VehicleDetector
from ai_engine.persistence_dispatcher import PersistenceDispatcher
from ai_engine.pipeline import CameraPipeline
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import TrackerConfig, VehicleTracker
from streaming.frame_reader import FrameReader
from streaming.pts import PTSTracker
from streaming.rtsp_client import RTSPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel.phase29d")


def main() -> int:
    logger.info("==================================================")
    logger.info("PHASE 29D — CALIBRATED ANPR CONSENSUS EVALUATION")
    logger.info("CAMERA: CAM-006 (Live RTSP)")
    logger.info("POLICY: admission floor=0.20, min_obs=3, agr_ratio=60%%, fast_lock=0.85")
    logger.info("==================================================")

    # 1. Resolve RTSP Stream URL securely
    user = os.environ.get("RTSP_USER", "")
    pwd = os.environ.get("RTSP_PASSWORD", "")
    if not user or not pwd:
        logger.error("RTSP credentials not set in environment.")
        return 1

    safe_user = urllib.parse.quote(user, safe="")
    safe_pwd = urllib.parse.quote(pwd, safe="")
    stream_url = f"rtsp://{safe_user}:{safe_pwd}@103.250.160.189:8554/stream/cam06"
    logger.info("RTSP URL prepared: rtsp://***:***@103.250.160.189:8554/stream/cam06")

    # 2. Database baseline check
    db = SessionLocal()
    cam = db.query(Camera).filter(Camera.camera_code == "CAM-006").first()
    if not cam:
        cam = db.query(Camera).first()
    camera_code_to_use = cam.camera_code if cam else "CAM-006"
    logger.info("Using camera code for attribution: %s", camera_code_to_use)

    watch_entries = db.query(Watchlist).filter(Watchlist.is_active == True).all()
    active_watchlist_plates = [w.plate_number for w in watch_entries]
    logger.info("Active database watchlist count: %d (%s)", len(active_watchlist_plates), active_watchlist_plates)

    init_veh_count = db.query(Vehicle).count()
    init_evt_count = db.query(Event).count()
    init_alt_count = db.query(Alert).count()
    logger.info("Baseline DB counts -> Vehicles: %d, Events: %d, Alerts: %d", init_veh_count, init_evt_count, init_alt_count)

    # 3. Engines initialization
    cuda_avail = torch.cuda.is_available()
    logger.info("Initializing engines (CUDA: %s)...", cuda_avail)
    detector = VehicleDetector(config=DetectorConfig(device="auto", confidence=0.35))
    plate_detector = PlateDetector(model_path="models/license_plate_detector.pt", confidence_threshold=0.20)
    
    # Reader min_confidence=0.15 lets us capture sub-threshold observations to verify exclusion in coordinator
    recognizer = EasyOCRPlateRecognizer(
        languages=["en"],
        gpu=cuda_avail,
        allow_invalid_candidates=True,
        min_confidence=0.15,
    )
    logger.info("EasyOCR status: %s, device: %s", recognizer.status, recognizer.device)

    # Calibrated ANPR Policy Configuration
    anpr_config = ANPRConfig(
        min_candidate_confidence=0.20,
        consensus_min_observations=3,
        consensus_agreement_ratio=0.60,
        early_lock_confidence=0.85,
        observation_buffer_size=7,
        max_attempts=30,
        min_attempt_spacing_ms=150.0,
        enable_plate_detector=True,
        plate_detector_model_path="models/license_plate_detector.pt",
        plate_detector_confidence=0.20,
    )

    anpr_coord = ANPRCoordinator(
        recognizer=recognizer,
        plate_detector=plate_detector,
        config=anpr_config,
    )

    stream_proc = StreamProcessor(detector=detector, config=StreamProcessorConfig(frame_stride=1))
    tracker = VehicleTracker(config=TrackerConfig())
    pipeline = CameraPipeline(
        camera_id=camera_code_to_use,
        stream_processor=stream_proc,
        vehicle_tracker=tracker,
        anpr_coordinator=anpr_coord,
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

    # 5. Telemetry Tracking Structures
    telemetry = {
        "camera_id": "CAM-006",
        "start_time": datetime.utcnow().isoformat(),
        "frames_total": 0,
        "frames_with_detections": 0,
        "total_vehicle_detections": 0,
        "unique_tracks": set(),
        "plate_evaluations": 0,
        "plate_detections": 0,
        "ocr_candidates": [],
        "confidence_distribution": {
            "<0.20 (excluded)": 0,
            "0.20-0.29 (admitted)": 0,
            "0.30-0.49 (admitted)": 0,
            "0.50-0.84 (admitted)": 0,
            ">=0.85 (fast-lock eligible)": 0,
        },
        "normalized_candidates": [],
        "consensus_groups": {},
        "recognition_events": [],
        "persisted_dispatches": 0,
    }

    TARGET_FRAMES = 200
    MAX_SECONDS = 45.0
    t_start = time.time()
    logger.info("Processing live frames (Target: %d frames, timeout: %.1fs)...", TARGET_FRAMES, MAX_SECONDS)

    frame_idx = 0
    while frame_idx < TARGET_FRAMES and (time.time() - t_start < MAX_SECONDS):
        success, packet = reader.read_packet()
        if not success or packet is None or packet.frame is None:
            time.sleep(0.01)
            continue

        frame_idx += 1
        telemetry["frames_total"] += 1

        pipe_res = pipeline.process_frame(packet)

        if pipe_res.detection_result and pipe_res.detection_result.count > 0:
            telemetry["frames_with_detections"] += 1
            telemetry["total_vehicle_detections"] += pipe_res.detection_result.count

        if pipe_res.tracking_result:
            for trk in pipe_res.tracking_result.active_tracks:
                telemetry["unique_tracks"].add(trk.track_id)

        if pipe_res.anpr_results:
            for ar in pipe_res.anpr_results:
                telemetry["plate_evaluations"] += 1
                if ar.plate_bbox:
                    telemetry["plate_detections"] += 1

                if ar.raw_text:
                    conf = ar.confidence
                    cand_rec = {
                        "frame": frame_idx,
                        "track_id": ar.track_id,
                        "raw_text": ar.raw_text,
                        "normalized_plate": ar.normalized_plate,
                        "confidence": round(conf, 4),
                        "is_valid_format": ar.is_valid_format,
                        "status": ar.status,
                        "admitted_to_buffer": conf >= 0.20,
                    }
                    telemetry["ocr_candidates"].append(cand_rec)
                    telemetry["normalized_candidates"].append(ar.normalized_plate)

                    # Bin confidence
                    if conf < 0.20:
                        telemetry["confidence_distribution"]["<0.20 (excluded)"] += 1
                    elif conf < 0.30:
                        telemetry["confidence_distribution"]["0.20-0.29 (admitted)"] += 1
                    elif conf < 0.50:
                        telemetry["confidence_distribution"]["0.30-0.49 (admitted)"] += 1
                    elif conf < 0.85:
                        telemetry["confidence_distribution"]["0.50-0.84 (admitted)"] += 1
                    else:
                        telemetry["confidence_distribution"][">=0.85 (fast-lock eligible)"] += 1

                    logger.info(
                        "Frame %d | Track %d | Raw: '%s' -> Norm: '%s' | Valid: %s | Conf: %.3f | Status: %s | Buffer: %s",
                        frame_idx, ar.track_id, ar.raw_text, ar.normalized_plate,
                        ar.is_valid_format, conf, ar.status,
                        "ADMITTED" if conf >= 0.20 else "EXCLUDED",
                    )

                if ar.status == "RECOGNIZED":
                    rec_event = {
                        "frame": frame_idx,
                        "track_id": ar.track_id,
                        "plate": ar.normalized_plate,
                        "confidence": round(ar.confidence, 4),
                        "pts_ms": ar.pts_ms,
                    }
                    telemetry["recognition_events"].append(rec_event)
                    logger.info(">>> RECOGNITION EVENT: %s <<<", rec_event)

        # Record consensus groups from coordinator track states
        for (c_id, t_id), state in anpr_coord._track_states.items():
            obs_list = [
                {
                    "plate": obs.normalized_plate,
                    "conf": round(obs.ocr_confidence, 3),
                    "valid": obs.is_valid_format,
                }
                for obs in state.observations
            ]
            telemetry["consensus_groups"][str(t_id)] = {
                "observation_count": len(state.observations),
                "locked": state.locked,
                "lock_method": state.lock_method,
                "recognized_plate": state.recognized_plate,
                "observations": obs_list,
            }

        # Dispatch recognized plates to persistence worker
        if pipe_res.recognized_plates:
            telemetry["persisted_dispatches"] += len(pipe_res.recognized_plates)
            dispatcher.dispatch(
                pipeline_result=pipe_res,
                camera_code=camera_code_to_use,
                watchlist=active_watchlist_plates,
            )

        if frame_idx % 25 == 0:
            logger.info(
                "Progress: %d/%d frames | Detections: %d | Tracks: %d | OCR Cands: %d | Recognitions: %d",
                frame_idx, TARGET_FRAMES,
                telemetry["total_vehicle_detections"],
                len(telemetry["unique_tracks"]),
                len(telemetry["ocr_candidates"]),
                len(telemetry["recognition_events"]),
            )

    client.close()
    logger.info("CAM-006 stream closed. Waiting for worker to flush...")
    time.sleep(3.0)
    worker.stop(timeout=5.0)

    # Query DB outcomes
    post_veh_count = db.query(Vehicle).count()
    post_evt_count = db.query(Event).count()
    post_alt_count = db.query(Alert).count()

    telemetry["unique_tracks"] = list(telemetry["unique_tracks"])
    telemetry["db_outcomes"] = {
        "vehicles_before": init_veh_count,
        "vehicles_after": post_veh_count,
        "vehicles_persisted": post_veh_count - init_veh_count,
        "events_before": init_evt_count,
        "events_after": post_evt_count,
        "events_persisted": post_evt_count - init_evt_count,
        "alerts_before": init_alt_count,
        "alerts_after": post_alt_count,
        "alerts_created": post_alt_count - init_alt_count,
    }

    # Retrieve recent DB entries for verification
    recent_vehicles = db.query(Vehicle).order_by(Vehicle.id.desc()).limit(5).all()
    telemetry["recent_vehicles"] = [
        {"id": v.id, "plate_number": v.plate_number, "first_seen": v.first_seen.isoformat() if v.first_seen else None}
        for v in recent_vehicles
    ]

    recent_events = db.query(Event).order_by(Event.id.desc()).limit(5).all()
    telemetry["recent_events"] = [
        {"id": e.id, "plate_number": e.plate_number, "confidence": e.confidence, "timestamp": e.timestamp.isoformat() if e.timestamp else None}
        for e in recent_events
    ]

    recent_alerts = db.query(Alert).order_by(Alert.id.desc()).limit(5).all()
    telemetry["recent_alerts"] = [
        {"id": a.id, "plate_number": a.plate_number, "severity": a.severity, "created_at": a.created_at.isoformat() if a.created_at else None}
        for a in recent_alerts
    ]

    db.close()

    # Save evidence file
    output_path = os.path.join(PROJECT_ROOT, "backend", "scripts", "phase29d_cam06_evidence.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=2)

    logger.info("==================================================")
    logger.info("PHASE 29D CAM-006 EVALUATION COMPLETE")
    logger.info("Frames processed: %d", telemetry["frames_total"])
    logger.info("Vehicle detections: %d", telemetry["total_vehicle_detections"])
    logger.info("Unique tracks: %d", len(telemetry["unique_tracks"]))
    logger.info("Plate evaluations: %d", telemetry["plate_evaluations"])
    logger.info("OCR candidates count: %d", len(telemetry["ocr_candidates"]))
    logger.info("Confidence distribution: %s", telemetry["confidence_distribution"])
    logger.info("Normalized candidates: %s", list(set(telemetry["normalized_candidates"])))
    logger.info("Recognition events count: %d", len(telemetry["recognition_events"]))
    logger.info("Persisted vehicles: %d", telemetry["db_outcomes"]["vehicles_persisted"])
    logger.info("Persisted events: %d", telemetry["db_outcomes"]["events_persisted"])
    logger.info("Watchlist alerts created: %d", telemetry["db_outcomes"]["alerts_created"])
    logger.info("Evidence written to: %s", output_path)
    logger.info("==================================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
