import os
import sys
import time
import json
import logging
import urllib.parse
from datetime import datetime
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
logger = logging.getLogger("sentinel.phase29a_audit")

import torch
from streaming.rtsp_client import RTSPClient
from streaming.frame_reader import FrameReader, FramePacket
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


def audit_stream(stream_id: str, max_frames: int = 120, max_seconds: float = 20.0, shared_engines: dict = None):
    logger.info(f"--- Auditing {stream_id} ---")
    user = os.environ.get("RTSP_USER", "")
    pwd = os.environ.get("RTSP_PASSWORD", "")
    safe_user = urllib.parse.quote(user, safe="")
    safe_pwd = urllib.parse.quote(pwd, safe="")
    rtsp_url = f"rtsp://{safe_user}:{safe_pwd}@103.250.160.189:8554/stream/{stream_id}"

    client = RTSPClient(rtsp_url=rtsp_url, camera_id=stream_id, transport="tcp")
    t_open_start = time.time()
    connected = client.open()
    connect_time = time.time() - t_open_start

    result = {
        "camera_id": stream_id.upper(),
        "stream_id": stream_id,
        "rtsp_connected": connected,
        "connect_time_sec": round(connect_time, 2),
        "resolution": f"{client.width}x{client.height}" if connected else "N/A",
        "codec": client.codec if connected else "N/A",
        "advisory_fps": client.fps_hint if connected else 0.0,
        "actual_fps": 0.0,
        "pts_monotonic": True,
        "frames_received": 0,
        "vehicle_detections": 0,
        "unique_tracks": 0,
        "plate_detections": 0,
        "typical_vehicle_size": "0x0",
        "typical_plate_size": "0x0",
        "plate_boxes": [],
        "ocr_invocations": 0,
        "raw_candidates": 0,
        "raw_texts": [],
        "valid_plates": 0,
        "valid_plate_strings": [],
        "readable": False,
        "suitability": "UNSUITABLE",
        "notes": ""
    }

    if not connected:
        result["notes"] = "Failed to open RTSP stream"
        return result

    # Use shared engines or initialize
    detector = shared_engines["detector"]
    plate_detector = shared_engines["plate_detector"]
    recognizer = shared_engines["recognizer"]

    stream_proc = StreamProcessor(detector=detector, config=StreamProcessorConfig(frame_stride=1))
    tracker = VehicleTracker(config=TrackerConfig())
    anpr_coord = ANPRCoordinator(
        recognizer=recognizer,
        plate_detector=plate_detector,
        config=ANPRConfig(min_confidence=0.25, max_attempts=5, min_attempt_spacing_ms=250.0)
    )

    pipeline = CameraPipeline(
        camera_id=stream_id.upper(),
        stream_processor=stream_proc,
        vehicle_tracker=tracker,
        anpr_coordinator=anpr_coord
    )

    pts_tracker = PTSTracker()
    reader = FrameReader(client=client, pts_tracker=pts_tracker)

    pts_list = []
    vehicle_sizes = []
    plate_sizes = []
    all_tracks = set()

    t_start = time.time()
    while result["frames_received"] < max_frames and (time.time() - t_start < max_seconds):
        success, packet = reader.read_packet()
        if not success or packet is None or packet.frame is None:
            time.sleep(0.01)
            continue

        result["frames_received"] += 1
        pts_list.append(packet.pts_ms)

        pipe_res = pipeline.process_frame(packet)

        if pipe_res.detection_result and pipe_res.detection_result.count > 0:
            result["vehicle_detections"] += pipe_res.detection_result.count
            for d in pipe_res.detection_result.detections:
                vehicle_sizes.append((d.width, d.height))

        if pipe_res.tracking_result:
            for trk in pipe_res.tracking_result.active_tracks:
                all_tracks.add(trk.track_id)

        if pipe_res.anpr_results:
            for ar in pipe_res.anpr_results:
                if ar.plate_bbox:
                    result["plate_detections"] += 1
                    pw = round(ar.plate_bbox[2] - ar.plate_bbox[0], 1)
                    ph = round(ar.plate_bbox[3] - ar.plate_bbox[1], 1)
                    plate_sizes.append((pw, ph))
                    result["plate_boxes"].append({"track_id": ar.track_id, "bbox": [round(c, 1) for c in ar.plate_bbox], "w": pw, "h": ph})

                result["ocr_invocations"] += 1
                if ar.raw_text:
                    result["raw_candidates"] += 1
                    result["raw_texts"].append(ar.raw_text)

                if ar.is_valid_format and ar.status == "RECOGNIZED":
                    result["valid_plates"] += 1
                    result["valid_plate_strings"].append(ar.normalized_plate)

    client.close()
    elapsed = max(0.1, time.time() - t_start)
    result["actual_fps"] = round(result["frames_received"] / elapsed, 1)
    result["unique_tracks"] = len(all_tracks)

    # Check PTS monotonicity
    if len(pts_list) > 1:
        is_mono = all(pts_list[i] <= pts_list[i+1] for i in range(len(pts_list)-1))
        result["pts_monotonic"] = is_mono

    # Compute typical sizes
    if vehicle_sizes:
        med_vw = int(np.median([s[0] for s in vehicle_sizes]))
        med_vh = int(np.median([s[1] for s in vehicle_sizes]))
        result["typical_vehicle_size"] = f"{med_vw}x{med_vh}px"

    if plate_sizes:
        med_pw = int(np.median([s[0] for s in plate_sizes]))
        med_ph = int(np.median([s[1] for s in plate_sizes]))
        result["typical_plate_size"] = f"{med_pw}x{med_ph}px"
    else:
        result["typical_plate_size"] = "N/A"

    result["readable"] = result["valid_plates"] > 0

    if result["valid_plates"] > 0:
        result["suitability"] = "EXCELLENT"
        result["notes"] = f"Valid plates recognized ({', '.join(set(result['valid_plate_strings']))})"
    elif result["plate_detections"] > 0 and plate_sizes and max(s[1] for s in plate_sizes) >= 28:
        result["suitability"] = "MARGINAL"
        result["notes"] = f"Plates detected ({result['plate_detections']}), pixel height near threshold ({result['typical_plate_size']})"
    elif result["plate_detections"] > 0:
        result["suitability"] = "UNSUITABLE"
        result["notes"] = f"Plates detected but too low resolution ({result['typical_plate_size']}) for OCR"
    elif result["vehicle_detections"] > 0:
        result["suitability"] = "UNSUITABLE"
        result["notes"] = f"Vehicles detected ({result['typical_vehicle_size']}) but no plate regions localized (camera distance/angle)"
    else:
        result["suitability"] = "UNSUITABLE"
        result["notes"] = "Zero vehicle traffic observed in sample"

    logger.info(
        f"Result {stream_id}: Res={result['resolution']} | ActualFPS={result['actual_fps']} | "
        f"Vehicles={result['vehicle_detections']} | Tracks={result['unique_tracks']} | "
        f"Plates={result['plate_detections']} ({result['typical_plate_size']}) | "
        f"OCR Invocations={result['ocr_invocations']} | Valid={result['valid_plates']} | Suitability={result['suitability']}"
    )

    return result


def main():
    logger.info("==================================================")
    logger.info("PHASE 29A — REAL CAMERA ANPR QUALITY & COVERAGE AUDIT")
    logger.info("==================================================")

    # Initialize shared engines once to avoid reloading weights repeatedly
    cuda_avail = torch.cuda.is_available()
    logger.info(f"Loading shared YOLO and EasyOCR engines (CUDA: {cuda_avail})...")
    detector = VehicleDetector(config=DetectorConfig(device="auto", confidence=0.35))
    plate_detector = PlateDetector(model_path="models/license_plate_detector.pt", confidence_threshold=0.20)
    recognizer = EasyOCRPlateRecognizer(languages=["en"], gpu=cuda_avail, allow_invalid_candidates=True, min_confidence=0.15)

    shared_engines = {
        "detector": detector,
        "plate_detector": plate_detector,
        "recognizer": recognizer
    }

    # Candidate streams to inspect as specified by prompt
    candidates = ["cam01", "cam03", "cam06", "cam02", "cam04", "cam05", "cam07"]
    audit_results = []

    for s in candidates:
        try:
            res = audit_stream(stream_id=s, max_frames=100, max_seconds=15.0, shared_engines=shared_engines)
            audit_results.append(res)
        except Exception as e:
            logger.error(f"Error auditing {s}: {e}", exc_info=True)
            audit_results.append({
                "camera_id": s.upper(),
                "stream_id": s,
                "rtsp_connected": False,
                "resolution": "ERROR",
                "actual_fps": 0.0,
                "vehicle_detections": 0,
                "unique_tracks": 0,
                "plate_detections": 0,
                "typical_plate_size": "N/A",
                "ocr_invocations": 0,
                "valid_plates": 0,
                "suitability": "ERROR",
                "notes": str(e)
            })

    output_path = os.path.join(PROJECT_ROOT, "backend", "scripts", "phase29a_audit_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(audit_results, f, indent=2)

    logger.info(f"Audit results written to {output_path}")

    print("\n" + "=" * 120)
    print(f"{'Camera':<10} | {'Resolution':<12} | {'FPS':<6} | {'Vehicles':<9} | {'Tracks':<7} | {'Plates':<7} | {'Typical Plate':<14} | {'OCR':<5} | {'Valid':<6} | {'Suitability':<11} | {'Notes'}")
    print("-" * 120)
    for r in audit_results:
        print(f"{r['camera_id']:<10} | {r['resolution']:<12} | {r['actual_fps']:<6} | {r['vehicle_detections']:<9} | {r['unique_tracks']:<7} | {r['plate_detections']:<7} | {r['typical_plate_size']:<14} | {r['ocr_invocations']:<5} | {r['valid_plates']:<6} | {r['suitability']:<11} | {r['notes']}")
    print("=" * 120 + "\n")


if __name__ == "__main__":
    main()
