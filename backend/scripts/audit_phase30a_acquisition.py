"""
Phase 30A — Real Successful ANPR Acquisition Audit.
INSPECTION ONLY — ZERO CODE/CONFIGURATION CHANGES.

Audits real provisioned government CCTV streams across multiple bounded observation windows.
Captures full end-to-end telemetry:
- Stream metrics (resolution, codec, fps)
- Vehicle detections & confirmed tracks
- License plate detections (width, height, aspect ratio)
- Sharpness scores & quality gate pass/fail
- Raw OCR hypotheses (readtext boxes, text, confidence)
- Normalized text & strict Indian plate validation
- Candidate buffer admission (floor 0.20)
- Multi-frame track observations & temporal consensus evaluation
- Recognition events & failure diagnosis
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

import numpy as np
import torch

# Ensure workspace root and backend are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
for path_str in [str(PROJECT_ROOT), str(BACKEND_DIR)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.core.config import settings
from ai_engine.anpr.coordinator import ANPRCoordinator
from ai_engine.anpr.normalization import normalize_plate, validate_indian_plate_format
from ai_engine.anpr.plate_detector import PlateDetector
from ai_engine.anpr.preprocessor import (
    extract_vehicle_crop,
    extract_plate_crop,
    compute_plate_sharpness,
)
from ai_engine.anpr.recognizer import EasyOCRPlateRecognizer
from ai_engine.anpr.schemas import ANPRConfig
from ai_engine.config import DetectorConfig
from ai_engine.detector import VehicleDetector
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
logger = logging.getLogger("sentinel.phase30a")


def audit_window(
    stream_id: str,
    window_name: str,
    target_frames: int = 150,
    max_seconds: float = 40.0,
    shared_engines: dict = None,
) -> dict:
    logger.info("==================================================")
    logger.info("AUDITING %s | WINDOW: %s (Target: %d frames)", stream_id.upper(), window_name, target_frames)
    logger.info("==================================================")

    user = settings.RTSP_USER or os.environ.get("RTSP_USER", "")
    pwd = settings.RTSP_PASSWORD or os.environ.get("RTSP_PASSWORD", "")
    if not user or not pwd:
        logger.error("RTSP credentials missing.")
        return {"error": "Missing credentials", "stream_id": stream_id, "window_name": window_name}

    safe_user = urllib.parse.quote(user, safe="")
    safe_pwd = urllib.parse.quote(pwd, safe="")
    stream_url = f"rtsp://{safe_user}:{safe_pwd}@103.250.160.189:8554/stream/{stream_id}"

    detector = shared_engines["detector"]
    plate_detector = shared_engines["plate_detector"]
    recognizer = shared_engines["recognizer"]

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
        camera_id=stream_id.upper(),
        stream_processor=stream_proc,
        vehicle_tracker=tracker,
        anpr_coordinator=anpr_coord,
    )

    pts_tracker = PTSTracker()
    client = RTSPClient(rtsp_url=stream_url, camera_id=stream_id, transport="tcp")
    t_open_start = time.time()
    connected = client.open()
    connect_time = round(time.time() - t_open_start, 2)

    window_report = {
        "camera_id": stream_id.upper(),
        "stream_id": stream_id,
        "window_name": window_name,
        "rtsp_connected": connected,
        "connect_time_sec": connect_time,
        "resolution": f"{client.width}x{client.height}" if connected else "N/A",
        "codec": client.codec if connected else "N/A",
        "advisory_fps": client.fps_hint if connected else 0.0,
        "frames_processed": 0,
        "vehicle_detections": 0,
        "confirmed_tracks": 0,
        "unique_track_ids": [],
        "plate_detections": 0,
        "plate_crops_evaluated": [],
        "ocr_candidates": [],
        "repeated_observations_by_track": {},
        "consensus_result": {},
        "recognition_events": [],
        "readable_candidates": [],
        "failure_analysis": [],
    }

    if not connected:
        logger.error("Failed to connect to %s", stream_id)
        window_report["failure_analysis"].append("RTSP connection failure / timeout")
        return window_report

    reader = FrameReader(client=client, pts_tracker=pts_tracker)
    logger.info("Connected to %s (%sx%s, %s, %.1f fps)", stream_id, client.width, client.height, client.codec, client.fps_hint)

    frame_idx = 0
    t_start = time.time()
    active_track_set = set()

    while frame_idx < target_frames and (time.time() - t_start < max_seconds):
        success, packet = reader.read_packet()
        if not success or packet is None or packet.frame is None:
            time.sleep(0.01)
            continue

        frame_idx += 1
        window_report["frames_processed"] += 1

        # Diagnostic plate inspection: track localized plate dimensions & sharpness
        if detector and plate_detector:
            try:
                raw_veh_dets = detector.detect(packet.frame)
                for vd in raw_veh_dets:
                    vc = extract_vehicle_crop(packet.frame, vd.bbox)
                    if vc is not None and vc.size > 0:
                        pds = plate_detector.detect(vc, vd.bbox)
                        for pd in pds:
                            pc = extract_plate_crop(vc, pd.bbox_crop)
                            psh = compute_plate_sharpness(pc) if pc is not None else 0.0
                            
                            # Also run direct EasyOCR readtext on crop to see raw characters
                            raw_boxes = []
                            if pc is not None and recognizer.reader:
                                try:
                                    raw_boxes = recognizer.reader.readtext(pc)
                                except Exception:
                                    pass

                            crop_info = {
                                "frame": frame_idx,
                                "pts_ms": round(packet.pts_ms, 2) if packet.pts_ms else None,
                                "w": round(pd.width, 1),
                                "h": round(pd.height, 1),
                                "aspect_ratio": round(pd.aspect_ratio, 2),
                                "sharpness": round(psh, 1),
                                "passed_quality_gate": psh >= 15.0 and pd.width >= 30 and pd.height >= 12,
                                "raw_easyocr_detections": [
                                    {"text": b[1], "conf": round(float(b[2]), 4)}
                                    for b in raw_boxes if len(b) >= 3
                                ],
                            }
                            window_report["plate_crops_evaluated"].append(crop_info)
                            if crop_info["raw_easyocr_detections"]:
                                logger.info(
                                    "[%s|%s] Plate Crop Frame %d (%s px, sh=%.1f): EasyOCR raw -> %s",
                                    stream_id, window_name, frame_idx,
                                    f"{pd.width:.1f}x{pd.height:.1f}", psh,
                                    crop_info["raw_easyocr_detections"]
                                )
            except Exception as exc:
                logger.debug("Crop diagnostic error: %s", exc)

        # Run main production CameraPipeline
        pipe_res = pipeline.process_frame(packet)

        if pipe_res.detection_result and pipe_res.detection_result.count > 0:
            window_report["vehicle_detections"] += pipe_res.detection_result.count

        if pipe_res.tracking_result:
            for trk in pipe_res.tracking_result.active_tracks:
                active_track_set.add(trk.track_id)

        if pipe_res.anpr_results:
            for ar in pipe_res.anpr_results:
                if ar.plate_bbox:
                    window_report["plate_detections"] += 1

                if ar.raw_text:
                    cand_entry = {
                        "frame": frame_idx,
                        "pts_ms": round(ar.pts_ms, 2) if ar.pts_ms else None,
                        "track_id": ar.track_id,
                        "raw_text": ar.raw_text,
                        "confidence": round(ar.confidence, 4),
                        "normalized_plate": ar.normalized_plate,
                        "is_valid_format": ar.is_valid_format,
                        "status": ar.status,
                        "admitted_to_buffer": ar.confidence >= 0.20,
                    }
                    window_report["ocr_candidates"].append(cand_entry)

                    logger.info(
                        "[%s|%s] Frame %d | Track %d | Raw: '%s' | Norm: '%s' | Valid: %s | Conf: %.3f | Status: %s",
                        stream_id, window_name, frame_idx, ar.track_id,
                        ar.raw_text, ar.normalized_plate, ar.is_valid_format, ar.confidence, ar.status,
                    )

                    if ar.is_valid_format and ar.confidence >= 0.20:
                        window_report["readable_candidates"].append(cand_entry)

                if ar.status == "RECOGNIZED":
                    rec_entry = {
                        "frame": frame_idx,
                        "pts_ms": round(ar.pts_ms, 2) if ar.pts_ms else None,
                        "track_id": ar.track_id,
                        "plate": ar.normalized_plate,
                        "confidence": round(ar.confidence, 4),
                    }
                    window_report["recognition_events"].append(rec_entry)
                    logger.info(">>> RECOGNITION LOCK on %s: %s <<<", stream_id, rec_entry)

    client.close()
    window_report["confirmed_tracks"] = len(active_track_set)
    window_report["unique_track_ids"] = list(active_track_set)

    # Compile repeated observations by track from coordinator state
    for (c_id, t_id), state in anpr_coord._track_states.items():
        obs_summary = [
            {
                "pts_ms": obs.pts_ms,
                "raw_text": obs.raw_text,
                "plate": obs.normalized_plate,
                "conf": round(obs.ocr_confidence, 4),
                "valid": obs.is_valid_format,
                "sharpness": round(obs.sharpness, 1),
            }
            for obs in state.observations
        ]
        window_report["repeated_observations_by_track"][str(t_id)] = {
            "observation_count": len(state.observations),
            "observations": obs_summary,
            "locked": state.locked,
            "lock_method": state.lock_method,
            "recognized_plate": state.recognized_plate,
        }
        if state.locked:
            window_report["consensus_result"][str(t_id)] = {
                "plate": state.recognized_plate,
                "lock_method": state.lock_method,
                "observation_count": len(state.observations),
            }

    # Analyze failure causes
    crops = window_report["plate_crops_evaluated"]
    if len(crops) == 0 and window_report["vehicle_detections"] > 0:
        window_report["failure_analysis"].append("Camera distance/angle: Vehicles detected but plate detector found 0 plate regions")
    elif len(crops) > 0:
        avg_h = np.mean([p["h"] for p in crops])
        max_h = max([p["h"] for p in crops])
        if max_h < 20:
            window_report["failure_analysis"].append(f"Plate resolution too low: Max height {max_h:.1f}px (< 20px readable threshold)")
        
        ocr_crops = [c for c in crops if c["raw_easyocr_detections"]]
        if len(ocr_crops) == 0:
            window_report["failure_analysis"].append(f"Character contrast/sharpness: EasyOCR found 0 characters across {len(crops)} localized plate crops")
        else:
            invalid_ocr = [
                d["text"] for c in ocr_crops for d in c["raw_easyocr_detections"]
                if not validate_indian_plate_format(normalize_plate(d["text"])[0])[1]
            ]
            valid_ocr = [
                d["text"] for c in ocr_crops for d in c["raw_easyocr_detections"]
                if validate_indian_plate_format(normalize_plate(d["text"])[0])[1]
            ]
            if valid_ocr:
                window_report["failure_analysis"].append(f"Valid plate detected ({valid_ocr}), but observation count < 3 required for consensus")
            elif invalid_ocr:
                window_report["failure_analysis"].append(f"Plate character fragmentation: OCR hypotheses {list(set(invalid_ocr))} failed Indian registration syntax")

    logger.info(
        "Window %s Complete: Frames=%d, Veh=%d, Tracks=%d, Plates=%d, Crops=%d, OCR=%d, Rec=%d",
        window_name, window_report["frames_processed"],
        window_report["vehicle_detections"], window_report["confirmed_tracks"],
        window_report["plate_detections"], len(window_report["plate_crops_evaluated"]),
        len(window_report["ocr_candidates"]), len(window_report["recognition_events"]),
    )
    return window_report


def main():
    logger.info("==================================================")
    logger.info("PHASE 30A — REAL SUCCESSFUL ANPR ACQUISITION AUDIT")
    logger.info("INSPECTION ONLY — ZERO CODE/CONFIGURATION CHANGES")
    logger.info("==================================================")

    cuda_avail = torch.cuda.is_available()
    logger.info("Loading shared AI models (CUDA: %s)...", cuda_avail)

    detector = VehicleDetector(config=DetectorConfig(device="auto", confidence=0.35))
    plate_detector = PlateDetector(model_path="models/license_plate_detector.pt", confidence_threshold=0.20)
    recognizer = EasyOCRPlateRecognizer(
        languages=["en"],
        gpu=cuda_avail,
        allow_invalid_candidates=True,
        min_confidence=0.15,
    )

    shared_engines = {
        "detector": detector,
        "plate_detector": plate_detector,
        "recognizer": recognizer,
    }

    # Bounded sampling across multiple windows and cameras
    audit_plan = [
        ("cam06", "Window-1-Daylight", 150, 35.0),
        ("cam06", "Window-2-Afternoon", 150, 35.0),
        ("cam01", "Window-1-Daylight", 150, 35.0),
        ("cam01", "Window-2-Afternoon", 150, 35.0),
        ("cam04", "Window-1-Live", 120, 30.0),
        ("cam02", "Window-1-Live", 120, 30.0),
        ("cam03", "Window-1-Live", 120, 30.0),
    ]

    all_reports = []

    for stream_id, window_name, target_f, max_s in audit_plan:
        try:
            rep = audit_window(
                stream_id=stream_id,
                window_name=window_name,
                target_frames=target_f,
                max_seconds=max_s,
                shared_engines=shared_engines,
            )
            all_reports.append(rep)
        except Exception as exc:
            logger.error("Audit window error for %s (%s): %s", stream_id, window_name, exc, exc_info=True)
            all_reports.append({
                "camera_id": stream_id.upper(),
                "stream_id": stream_id,
                "window_name": window_name,
                "error": str(exc),
            })

    output_path = os.path.join(PROJECT_ROOT, "backend", "scripts", "phase30a_audit_report.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_reports, f, indent=2)

    logger.info("Audit report written to %s", output_path)

    # Print summary
    print("\n" + "=" * 140)
    print(f"{'Camera':<10} | {'Window':<18} | {'Resolution':<11} | {'Codec':<5} | {'Frames':<6} | {'Vehicles':<8} | {'Tracks':<6} | {'Plates':<6} | {'Crops':<6} | {'OCR':<5} | {'Locks':<5} | {'Failure Analysis'}")
    print("-" * 140)
    for r in all_reports:
        cam = r.get("camera_id", "N/A")
        win = r.get("window_name", "N/A")
        res = r.get("resolution", "N/A")
        cdc = r.get("codec", "N/A")
        frm = r.get("frames_processed", 0)
        veh = r.get("vehicle_detections", 0)
        trk = r.get("confirmed_tracks", 0)
        plt = r.get("plate_detections", 0)
        crp = len(r.get("plate_crops_evaluated", []))
        ocr = len(r.get("ocr_candidates", []))
        lck = len(r.get("recognition_events", []))
        fail = "; ".join(r.get("failure_analysis", [])) or "None"
        print(f"{cam:<10} | {win:<18} | {res:<11} | {cdc:<5} | {frm:<6} | {veh:<8} | {trk:<6} | {plt:<6} | {crp:<6} | {ocr:<5} | {lck:<5} | {fail[:42]}")
    print("=" * 140 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
