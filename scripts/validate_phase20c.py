"""
Phase 20C Complete End-to-End Validation Script.

Executes:
1. Checksum verification of models/license_plate_detector.pt.
2. Actual component latency benchmarks with GPU timing.
3. GPU memory utilization measurements.
4. Full video processing of 0911.mp4 with CameraPipeline.
5. Verification of genuine plate recognition (KA02MM9091).
6. Verification of database persistence (Vehicle, Event, Watchlist Alert).
"""

import os
import sys
import time
import hashlib
import json
import statistics
import cv2
import numpy as np
import torch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add Sentinel root and backend to path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from streaming.frame_reader import FramePacket
from ai_engine.detector import VehicleDetector
from ai_engine.config import DetectorConfig
from ai_engine.tracking.tracker import VehicleTracker, TrackerConfig
from ai_engine.anpr.plate_detector import PlateDetector
from ai_engine.anpr.recognizer import EasyOCRPlateRecognizer
from ai_engine.anpr.coordinator import ANPRCoordinator
from ai_engine.anpr.schemas import ANPRConfig
from ai_engine.pipeline import CameraPipeline
from ai_engine.persistence_dispatcher import PersistenceDispatcher
from app.services.anpr_worker import ANPRPersistenceWorker
from app.models.camera import Camera
from app.models.vehicle import Vehicle
from app.models.event import Event
from app.models.alert import Alert

MODEL_PATH = "models/license_plate_detector.pt"
EXPECTED_SHA256 = "2d95861825bb4184404344c9cf809f40fd31dba785fe54e8ba5b9a3583789822"
VIDEO_PATH = "0911.mp4"
DB_PATH = "sqlite:///sentinel_test.db"
CAMERA_ID = "CAM-001"


def verify_model_checksum(path: str, expected_hash: str) -> tuple[bool, str]:
    if not os.path.exists(path):
        return False, "FILE_NOT_FOUND"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    actual_hash = hasher.hexdigest().lower()
    return (actual_hash == expected_hash.lower()), actual_hash


def measure_gpu_latencies(detector, plate_detector, recognizer, test_frame, test_crop, test_plate):
    latencies = {}
    
    # 1. Vehicle YOLO latency
    yolo_times = []
    packet = FramePacket(frame=test_frame, pts_ms=0.0, received_at=time.time(), width=test_frame.shape[1], height=test_frame.shape[0], camera_id=CAMERA_ID)
    # Warmup
    for _ in range(3):
        _ = detector.detect(packet)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    for _ in range(10):
        t0 = time.perf_counter()
        _ = detector.detect(packet)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        yolo_times.append((time.perf_counter() - t0) * 1000.0)
    latencies["vehicle_yolo_ms"] = {
        "mean": round(statistics.mean(yolo_times), 2),
        "min": round(min(yolo_times), 2),
        "max": round(max(yolo_times), 2),
        "std": round(statistics.stdev(yolo_times), 2),
    }

    # 2. Plate detector latency
    plate_times = []
    # Warmup
    for _ in range(3):
        _ = plate_detector.detect(test_crop)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    for _ in range(10):
        t0 = time.perf_counter()
        _ = plate_detector.detect(test_crop)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        plate_times.append((time.perf_counter() - t0) * 1000.0)
    latencies["plate_detector_ms"] = {
        "mean": round(statistics.mean(plate_times), 2),
        "min": round(min(plate_times), 2),
        "max": round(max(plate_times), 2),
        "std": round(statistics.stdev(plate_times), 2),
    }

    # 3. EasyOCR latency on tight plate crop
    ocr_times = []
    # Warmup
    for _ in range(2):
        _ = recognizer.recognize(test_plate)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
    for _ in range(5):
        t0 = time.perf_counter()
        _ = recognizer.recognize(test_plate)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        ocr_times.append((time.perf_counter() - t0) * 1000.0)
    latencies["easyocr_ms"] = {
        "mean": round(statistics.mean(ocr_times), 2),
        "min": round(min(ocr_times), 2),
        "max": round(max(ocr_times), 2),
        "std": round(statistics.stdev(ocr_times), 2),
    }

    # 4. Total cascaded ANPR latency (Plate Detect + Tight Crop + OCR)
    latencies["total_anpr_cascade_ms"] = {
        "mean": round(latencies["plate_detector_ms"]["mean"] + latencies["easyocr_ms"]["mean"], 2)
    }

    # GPU Memory
    if torch.cuda.is_available():
        latencies["gpu_allocated_mb"] = round(torch.cuda.memory_allocated() / (1024 * 1024), 2)
        latencies["gpu_reserved_mb"] = round(torch.cuda.memory_reserved() / (1024 * 1024), 2)
        latencies["gpu_max_allocated_mb"] = round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2)
    else:
        latencies["gpu_allocated_mb"] = 0.0
        latencies["gpu_reserved_mb"] = 0.0

    return latencies


def main():
    print("=" * 80)
    print("SENTINEL PHASE 20C COMPREHENSIVE END-TO-END VALIDATION")
    print("=" * 80)

    # 1. Model Verification
    print("\n[STEP 1] Model Integrity Verification")
    matches, actual_hash = verify_model_checksum(MODEL_PATH, EXPECTED_SHA256)
    print(f"  Model File:    {MODEL_PATH}")
    print(f"  Expected Hash: {EXPECTED_SHA256}")
    print(f"  Actual Hash:   {actual_hash}")
    print(f"  Verified Match: {matches}")
    if not matches:
        print("ERROR: Model hash mismatch or model missing! Aborting.")
        sys.exit(1)

    # 2. Initialize Components
    print("\n[STEP 2] Initializing Components")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  PyTorch device: {device}")
    if torch.cuda.is_available():
        print(f"  GPU Name: {torch.cuda.get_device_name(0)}")

    detector = VehicleDetector(config=DetectorConfig(device=device, confidence=0.35))
    tracker = VehicleTracker(config=TrackerConfig())
    plate_detector = PlateDetector(model_path=MODEL_PATH, confidence_threshold=0.25, device=device)
    recognizer = EasyOCRPlateRecognizer(languages=["en"], gpu=True, min_confidence=0.15, allow_invalid_candidates=True)
    
    anpr_config = ANPRConfig(
        min_confidence=0.40,
        max_attempts=30,
        min_attempt_spacing_ms=150.0,
        early_lock_confidence=0.85,
        min_crop_width=40,
        min_crop_height=30,
        enable_plate_detector=True,
        plate_detector_model_path=MODEL_PATH,
        plate_detector_confidence=0.25,
        min_plate_width=30,
        min_plate_height=12,
        min_plate_sharpness=15.0,
        observation_buffer_size=7,
        consensus_min_observations=3,
        consensus_agreement_ratio=0.60,
    )
    coordinator = ANPRCoordinator(recognizer=recognizer, plate_detector=plate_detector, config=anpr_config)

    from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
    stream_processor = StreamProcessor(detector=detector, config=StreamProcessorConfig(frame_stride=1))

    pipeline = CameraPipeline(
        camera_id=CAMERA_ID,
        stream_processor=stream_processor,
        vehicle_tracker=tracker,
        anpr_coordinator=coordinator,
    )

    # 3. Setup Persistence & Watchlist in DB
    print("\n[STEP 3] Setting up Database and Persistence Worker")
    engine = create_engine(DB_PATH)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Ensure CAM-001 exists in DB
    db = SessionLocal()
    try:
        cam = db.query(Camera).filter(Camera.camera_code == CAMERA_ID).first()
        if not cam:
            cam = Camera(camera_code=CAMERA_ID, name="Entrance Camera", status="online")
            db.add(cam)
            db.commit()
            print(f"  Created camera {CAMERA_ID} in database.")
        else:
            print(f"  Camera {CAMERA_ID} verified in database (id={cam.id}).")
    finally:
        db.close()

    # Watchlist includes genuine test plate KA02MM9091
    watchlist = ["KA02MM9091"]
    worker = ANPRPersistenceWorker(session_factory=SessionLocal, poll_timeout_sec=0.05)
    dispatcher = PersistenceDispatcher(worker=worker, default_watchlist=watchlist)
    worker.start()
    print("  ANPRPersistenceWorker started with watchlist: ['KA02MM9091']")

    # 4. Extract sample frames for latency measurement
    cap = cv2.VideoCapture(VIDEO_PATH)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"\n[STEP 4] Measuring Actual Execution Latencies on {VIDEO_PATH}")
    
    cap.set(cv2.CAP_PROP_POS_FRAMES, 622)
    ret, sample_frame = cap.read()
    test_veh_crop = sample_frame[380:970, 500:1270].copy()
    test_plate_crop = sample_frame[730:785, 870:1010].copy()

    latencies = measure_gpu_latencies(detector, plate_detector, recognizer, sample_frame, test_veh_crop, test_plate_crop)
    print("  Measured Component Latencies:")
    print(f"    - Vehicle YOLO Detection: {latencies['vehicle_yolo_ms']['mean']} ms (min: {latencies['vehicle_yolo_ms']['min']}, max: {latencies['vehicle_yolo_ms']['max']})")
    print(f"    - Plate Detector:         {latencies['plate_detector_ms']['mean']} ms (min: {latencies['plate_detector_ms']['min']}, max: {latencies['plate_detector_ms']['max']})")
    print(f"    - EasyOCR Recognition:    {latencies['easyocr_ms']['mean']} ms (min: {latencies['easyocr_ms']['min']}, max: {latencies['easyocr_ms']['max']})")
    print(f"    - Total Cascaded ANPR:    {latencies['total_anpr_cascade_ms']['mean']} ms")
    print(f"    - GPU Memory Allocated:   {latencies['gpu_allocated_mb']} MB (peak: {latencies.get('gpu_max_allocated_mb', 0)} MB)")

    # 5. Process Full Video 0911.mp4
    print(f"\n[STEP 5] Processing Full Video {VIDEO_PATH} (775 frames, ~25.8s)")
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    frame_idx = 0
    all_anpr_results = []
    locked_tracks = {}

    t_start = time.perf_counter()
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        pts_ms = (frame_idx / fps) * 1000.0

        packet = FramePacket(
            frame=frame,
            pts_ms=pts_ms,
            received_at=time.time(),
            width=frame.shape[1],
            height=frame.shape[0],
            camera_id=CAMERA_ID,
        )

        res = pipeline.process_frame(packet)

        if res.anpr_results:
            dispatcher.dispatch(pipeline_result=res, camera_code=CAMERA_ID)
            for ar in res.anpr_results:
                all_anpr_results.append({
                    "frame_idx": frame_idx,
                    "pts_ms": round(pts_ms, 2),
                    "track_id": ar.track_id,
                    "status": ar.status,
                    "raw_text": ar.raw_text,
                    "normalized_plate": ar.normalized_plate,
                    "confidence": round(ar.confidence, 4),
                    "is_valid_format": ar.is_valid_format,
                })
                # Check for track lock
                st = coordinator.get_track_state(CAMERA_ID, ar.track_id)
                if st and st.locked and ar.track_id not in locked_tracks:
                    locked_tracks[ar.track_id] = {
                        "plate": st.recognized_plate,
                        "method": st.lock_method,
                        "frame": frame_idx,
                        "pts_s": round(pts_ms / 1000.0, 2),
                        "attempts": st.attempt_count,
                        "observations": len(st.observations),
                    }
                    print(f"  >>> LOCK EVENT: Track {ar.track_id} locked with '{st.recognized_plate}' via {st.lock_method} at frame {frame_idx} ({pts_ms/1000.0:.2f}s)")

        if frame_idx % 100 == 0:
            elapsed = time.perf_counter() - t_start
            print(f"  Processed {frame_idx}/{total_frames} frames ({frame_idx/elapsed:.1f} FPS)...")

    cap.release()
    total_processing_time = time.perf_counter() - t_start
    print(f"  Video processing complete in {total_processing_time:.2f}s ({frame_idx/total_processing_time:.1f} FPS avg).")

    # 6. Wait for persistence worker to drain queue
    print("\n[STEP 6] Waiting for ANPRPersistenceWorker to flush database queue...")
    time.sleep(1.0)
    worker.stop()
    print("  Worker drained and stopped cleanly.")

    # 7. Verify Database Persistence
    print("\n[STEP 7] Verifying Database Records in sentinel_test.db")
    db = SessionLocal()
    try:
        # Check Vehicle
        veh_records = db.query(Vehicle).all()
        print(f"  Vehicles in DB ({len(veh_records)}):")
        target_veh = None
        for v in veh_records:
            print(f"    - Vehicle id={v.id}: plate='{v.plate_number}'")
            if v.plate_number == "KA02MM9091":
                target_veh = v

        # Check Event
        event_records = db.query(Event).all()
        print(f"  Events in DB ({len(event_records)}):")
        target_event = None
        for e in event_records:
            print(f"    - Event id={e.id}: camera_id={e.camera_id}, vehicle_id={e.vehicle_id}, conf={e.confidence}, timestamp={e.timestamp}")
            if target_veh and e.vehicle_id == target_veh.id:
                target_event = e

        # Check Alert
        alert_records = db.query(Alert).all()
        print(f"  Alerts in DB ({len(alert_records)}):")
        target_alert = None
        for a in alert_records:
            print(f"    - Alert id={a.id}: vehicle_id={a.vehicle_id}, type={a.alert_type}, severity={a.severity}, message='{a.message}'")
            if target_veh and a.vehicle_id == target_veh.id:
                target_alert = a

    finally:
        db.close()

    # 8. Compile Final Verdict
    print("\n" + "=" * 80)
    print("FINAL ACCEPTANCE VERDICT")
    print("=" * 80)

    recognized_plate_found = False
    for tid, lk in locked_tracks.items():
        if lk["plate"] == "KA02MM9091":
            recognized_plate_found = True
            break

    persistence_passed = (target_veh is not None) and (target_event is not None)
    alert_passed = (target_alert is not None)

    target_track_reached_21s = any(lk["plate"] == "KA02MM9091" and lk["pts_s"] >= 20.0 for lk in locked_tracks.values())
    print(f"  Model Checksum Verified:      {matches}")
    print(f"  Genuine Plate Recognized:     {recognized_plate_found} (Target: KA02MM9091)")
    print(f"  Track Remains Eligible to 21s:{target_track_reached_21s}")
    print(f"  Database Vehicle Persisted:   {target_veh is not None}")
    print(f"  Database Event Persisted:     {target_event is not None}")
    print(f"  Watchlist Alert Generated:    {alert_passed}")

    is_full_pass = matches and recognized_plate_found and persistence_passed and alert_passed

    if is_full_pass:
        print("\n>>> STATUS: FULL PASS")
    else:
        print("\n>>> STATUS: PARTIAL — ANPR IMPROVED, REAL PLATE RECOGNITION NOT PROVEN")

    # Save summary report to JSON
    report = {
        "status": "FULL PASS" if is_full_pass else "PARTIAL",
        "model": {
            "path": MODEL_PATH,
            "expected_sha256": EXPECTED_SHA256,
            "actual_sha256": actual_hash,
            "checksum_verified": matches,
        },
        "latencies_ms": latencies,
        "video": {
            "path": VIDEO_PATH,
            "total_frames": total_frames,
            "fps": fps,
            "processing_time_s": round(total_processing_time, 2),
        },
        "locked_tracks": locked_tracks,
        "database": {
            "target_vehicle_found": target_veh is not None,
            "vehicle_plate": target_veh.plate_number if target_veh else None,
            "target_event_found": target_event is not None,
            "target_alert_found": target_alert is not None,
            "alert_message": target_alert.message if target_alert else None,
        }
    }
    with open("phase20c_validation_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nSaved comprehensive report to phase20c_validation_report.json")


if __name__ == "__main__":
    main()
