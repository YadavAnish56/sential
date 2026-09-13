import os
import sys
import time
import json
import logging
import urllib.parse
from datetime import datetime
from collections import defaultdict
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("sentinel.phase29c_calibration")

import torch
from streaming.rtsp_client import RTSPClient
from streaming.frame_reader import FrameReader
from streaming.pts import PTSTracker
from ai_engine.config import DetectorConfig
from ai_engine.detector import VehicleDetector
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import VehicleTracker, TrackerConfig
from ai_engine.anpr import (
    PlateDetector,
    EasyOCRPlateRecognizer,
    normalize_plate,
    preprocess_crop_for_ocr,
)
from ai_engine.anpr.preprocessor import extract_vehicle_crop, extract_plate_crop, compute_plate_sharpness
from ai_engine.anpr.coordinator import resolve_temporal_consensus, TrackPlateObservation


def run_calibration(target_frames: int = 250, max_seconds: float = 40.0):
    logger.info("==================================================")
    logger.info("PHASE 29C — ANPR CONFIDENCE THRESHOLD CALIBRATION")
    logger.info("INSPECTION ONLY — ZERO CODE OR CONFIG CHANGES")
    logger.info("==================================================")

    user = os.environ.get("RTSP_USER", "")
    pwd = os.environ.get("RTSP_PASSWORD", "")
    safe_user = urllib.parse.quote(user, safe="")
    safe_pwd = urllib.parse.quote(pwd, safe="")
    stream_url = f"rtsp://{safe_user}:{safe_pwd}@103.250.160.189:8554/stream/cam06"

    cuda_avail = torch.cuda.is_available()
    logger.info("Initializing inspection engines on CUDA: %s...", cuda_avail)

    detector = VehicleDetector(config=DetectorConfig(device="auto", confidence=0.35))
    plate_detector = PlateDetector(model_path="models/license_plate_detector.pt", confidence_threshold=0.20)
    # Set min_confidence=0.05 on the reader to capture all sub-threshold candidates down to <0.15
    recognizer = EasyOCRPlateRecognizer(languages=["en"], gpu=cuda_avail, allow_invalid_candidates=True, min_confidence=0.05)

    stream_proc = StreamProcessor(detector=detector, config=StreamProcessorConfig(frame_stride=1))
    tracker = VehicleTracker(config=TrackerConfig())

    logger.info("Connecting to CAM-006 RTSP stream via TCP...")
    pts_tracker = PTSTracker()
    client = RTSPClient(rtsp_url=stream_url, camera_id="cam06", transport="tcp")
    opened = client.open()
    if not opened:
        logger.error("FATAL: Failed to connect to CAM-006")
        return None

    reader = FrameReader(client=client, pts_tracker=pts_tracker)
    logger.info("Connected to CAM-006: %sx%s, advisory_fps=%.1f", client.width, client.height, client.fps_hint)

    # In-memory candidate storage (NO DB PERSISTENCE)
    all_candidates = []
    track_observations = defaultdict(list)
    frames_processed = 0
    vehicle_detections = 0
    plate_detections = 0

    t_start = time.time()
    logger.info("Sampling live frames (Target: %d frames)...", target_frames)

    while frames_processed < target_frames and (time.time() - t_start < max_seconds):
        success, packet = reader.read_packet()
        if not success or packet is None or packet.frame is None:
            time.sleep(0.01)
            continue

        frames_processed += 1
        pts_ms = packet.pts_ms

        det_result = stream_proc.process_packet(packet)
        if not det_result or det_result.count == 0:
            continue

        vehicle_detections += det_result.count
        tracking_result = tracker.update(det_result)
        if not tracking_result:
            continue

        # Evaluate confirmed tracks for license plates
        for trk in tracking_result.active_tracks:
            # Stage 1: Vehicle Crop
            veh_crop = extract_vehicle_crop(packet.frame, trk.bbox, min_width=60, min_height=40)
            if veh_crop is None or veh_crop.size == 0:
                continue

            # Stage 2: Plate Detection
            detected_plates = plate_detector.detect(vehicle_crop=veh_crop, vehicle_bbox=trk.bbox)
            if not detected_plates:
                continue

            plate_detections += len(detected_plates)
            best_det = detected_plates[0]

            # Stage 3: Plate Crop
            plate_crop = extract_plate_crop(veh_crop, best_det.bbox_crop, margin_ratio=0.15)
            if plate_crop is None or plate_crop.size == 0:
                continue

            pw = round(best_det.width, 1)
            ph = round(best_det.height, 1)
            psh = round(compute_plate_sharpness(plate_crop), 1)

            # Stage 4: EasyOCR raw text inference
            # Query reader directly to inspect all raw boxes and confidences
            try:
                raw_results = recognizer._reader.readtext(plate_crop)
            except Exception as e:
                raw_results = []

            if not raw_results:
                # Contrast-enhanced retry
                try:
                    prep = preprocess_crop_for_ocr(plate_crop)
                    raw_results = recognizer._reader.readtext(prep)
                except Exception:
                    raw_results = []

            if not raw_results:
                continue

            # Evaluate each raw OCR detection box
            for item in raw_results:
                if len(item) < 3:
                    continue
                raw_txt = str(item[1]).strip()
                conf = float(item[2])
                if not raw_txt:
                    continue

                norm_txt, is_valid = normalize_plate(raw_txt)

                cand_record = {
                    "frame": frames_processed,
                    "pts_ms": pts_ms,
                    "track_id": trk.track_id,
                    "plate_crop_dim": f"{pw}x{ph}px",
                    "plate_sharpness": psh,
                    "raw_ocr_text": raw_txt,
                    "normalized_text": norm_txt,
                    "confidence": round(conf, 4),
                    "is_valid_format": is_valid,
                }
                all_candidates.append(cand_record)

                # Store for track temporal consensus evaluation
                obs = TrackPlateObservation(
                    pts_ms=pts_ms,
                    plate_bbox_crop=best_det.bbox_crop,
                    plate_bbox_frame=best_det.bbox_frame or (0.0, 0.0, 0.0, 0.0),
                    raw_text=raw_txt,
                    normalized_plate=norm_txt,
                    ocr_confidence=conf,
                    plate_detector_confidence=best_det.confidence,
                    sharpness=psh,
                    is_valid_format=is_valid,
                    aspect_ratio=best_det.aspect_ratio,
                )
                track_observations[trk.track_id].append(obs)

        if frames_processed % 50 == 0:
            logger.info(
                "Progress: %d/%d frames | Vehicles: %d | Plate Crops: %d | OCR Cand: %d",
                frames_processed, target_frames, vehicle_detections, plate_detections, len(all_candidates)
            )

    client.close()
    elapsed = max(0.1, time.time() - t_start)
    logger.info(
        "Sampling complete in %.1fs: %d frames (%.1f fps), %d OCR candidates captured.",
        elapsed, frames_processed, frames_processed / elapsed, len(all_candidates)
    )

    # -------------------------------------------------------------
    # EMPIRICAL BAND ANALYSIS
    # Bands: <0.15, 0.15–0.19, 0.20–0.24, 0.25–0.29, >=0.30
    # -------------------------------------------------------------
    bands_def = [
        ("<0.15", lambda c: c < 0.15),
        ("0.15-0.19", lambda c: 0.15 <= c < 0.20),
        ("0.20-0.24", lambda c: 0.20 <= c < 0.25),
        ("0.25-0.29", lambda c: 0.25 <= c < 0.30),
        (">=0.30", lambda c: c >= 0.30),
    ]

    band_stats = {}
    for band_name, _ in bands_def:
        band_stats[band_name] = {
            "total_candidates": 0,
            "passing_validation": 0,
            "repeated_on_same_track": 0,
            "stable_normalized_plate": 0,
            "inconsistent_or_noisy": 0,
            "samples": []
        }

    # Analyze stability per track
    # A candidate is "repeated on same track" if the track has >=2 observations
    # A candidate is "stable normalized plate" if >=2 observations on the same track produced the same normalized text
    for cand in all_candidates:
        conf = cand["confidence"]
        norm = cand["normalized_text"]
        trk_id = cand["track_id"]
        is_valid = cand["is_valid_format"]

        # Find matching band
        target_band = None
        for bname, predicate in bands_def:
            if predicate(conf):
                target_band = bname
                break

        if not target_band:
            continue

        bs = band_stats[target_band]
        bs["total_candidates"] += 1
        if is_valid:
            bs["passing_validation"] += 1

        trk_all_obs = track_observations[trk_id]
        if len(trk_all_obs) >= 2:
            bs["repeated_on_same_track"] += 1
            # Check agreement with other observations on this track
            matching_norms = [o for o in trk_all_obs if o.normalized_plate == norm]
            if len(matching_norms) >= 2:
                bs["stable_normalized_plate"] += 1
            else:
                bs["inconsistent_or_noisy"] += 1
        else:
            bs["inconsistent_or_noisy"] += 1

        bs["samples"].append({
            "track_id": trk_id,
            "raw": cand["raw_ocr_text"],
            "norm": norm,
            "conf": conf,
            "valid": is_valid,
            "crop": cand["plate_crop_dim"]
        })

    # Evaluate temporal consensus across tracks
    track_consensus = {}
    for trk_id, obs_list in track_observations.items():
        if len(obs_list) >= 2:
            resolved, method = resolve_temporal_consensus(
                observations=obs_list,
                min_observations=2,
                agreement_ratio=0.50,
                fast_lock_confidence=0.80
            )
            track_consensus[trk_id] = {
                "obs_count": len(obs_list),
                "resolved_plate": resolved,
                "lock_method": method,
                "all_norms": [o.normalized_plate for o in obs_list],
                "confidences": [round(o.ocr_confidence, 3) for o in obs_list]
            }

    report_payload = {
        "metadata": {
            "camera_id": "CAM-006",
            "timestamp": datetime.utcnow().isoformat(),
            "frames_processed": frames_processed,
            "total_candidates": len(all_candidates),
            "unique_tracks_with_ocr": len(track_observations)
        },
        "band_statistics": band_stats,
        "track_consensus_summary": track_consensus,
        "all_candidates": all_candidates
    }

    output_path = os.path.join(PROJECT_ROOT, "backend", "scripts", "phase29c_calibration_evidence.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2)

    logger.info(f"Calibration evidence saved to {output_path}")

    # Print summary table
    print("\n" + "=" * 110)
    print(f"{'Band':<12} | {'Total Cand':<11} | {'Valid Indian':<13} | {'Repeated':<9} | {'Stable Text':<12} | {'Noisy/Frag':<11} | {'Sample Text'}")
    print("-" * 110)
    for bname, _ in bands_def:
        bs = band_stats[bname]
        sample_str = ", ".join([f"{s['norm']}({s['conf']})" for s in bs["samples"][:3]])
        print(f"{bname:<12} | {bs['total_candidates']:<11} | {bs['passing_validation']:<13} | {bs['repeated_on_same_track']:<9} | {bs['stable_normalized_plate']:<12} | {bs['inconsistent_or_noisy']:<11} | {sample_str}")
    print("=" * 110 + "\n")

    return report_payload


if __name__ == "__main__":
    run_calibration(target_frames=250, max_seconds=40.0)
