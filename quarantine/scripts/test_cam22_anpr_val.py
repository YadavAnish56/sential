"""
Sentinel — CAM22 Real ANPR Validation (25-35 sec segment)
Strictly Controlled Validation Script
"""

import os
import sys
import time
import subprocess
import signal
import statistics
import cv2
import numpy as np

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from streaming.rtsp_client import RTSPClient
from streaming.frame_reader import FrameReader, FramePacket
from streaming.pts import PTSTracker
from ai_engine.config import DetectorConfig
from ai_engine.detector import VehicleDetector
from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
from ai_engine.tracking import VehicleTracker, TrackerConfig
from ai_engine.tracking.schemas import TrackState
from ai_engine.anpr import ANPRCoordinator, ANPRConfig, EasyOCRPlateRecognizer
from ai_engine.pipeline import CameraPipeline


def main():
    print("=== CAM22 Real ANPR Validation (25-35 sec segment) ===")
    video_path = os.path.join(PROJECT_ROOT, "Screen Recording 2026-09-10 223457.mp4")
    mediamtx_exe = os.path.join(PROJECT_ROOT, "mediamtx", "mediamtx.exe")
    mediamtx_yml = os.path.join(PROJECT_ROOT, "mediamtx", "mediamtx.yml")
    rtsp_url = "rtsp://127.0.0.1:8554/cam22-test"

    # Step 1: Video File Verification
    if not os.path.exists(video_path):
        print(f"ERROR: Video file not found at {video_path}")
        return 1
    file_size = os.path.getsize(video_path)

    # Step 2: Start MediaMTX
    print("[1/5] Starting local MediaMTX...")
    mediamtx_proc = subprocess.Popen(
        [mediamtx_exe, mediamtx_yml],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.path.join(PROJECT_ROOT, "mediamtx")
    )
    time.sleep(1.5)
    if mediamtx_proc.poll() is not None:
        print("ERROR: MediaMTX failed to start.")
        return 1
    print("MediaMTX started successfully (PID: {})".format(mediamtx_proc.pid))

    ffmpeg_proc = None
    rtsp_client = None
    try:
        # Pre-initialize AI Pipeline before FFmpeg starts so model load latency does not exhaust the 10s stream
        print("[2/5] Pre-initializing Real Sentinel AI Pipeline (YOLO + Tracker + EasyOCR)...")
        pts_tracker = PTSTracker()

        detector_config = DetectorConfig(device="auto", confidence=0.35)
        detector = VehicleDetector(config=detector_config)
        stream_processor = StreamProcessor(
            detector=detector,
            config=StreamProcessorConfig(frame_stride=1)
        )
        tracker = VehicleTracker(config=TrackerConfig())
        
        ocr_recognizer = EasyOCRPlateRecognizer(
            languages=["en"],
            gpu=True,
            allow_invalid_candidates=True,
            min_confidence=0.15
        )
        print("EasyOCR Recognizer Status: {}, Device: {}".format(ocr_recognizer.status, ocr_recognizer.device))

        anpr_config = ANPRConfig(
            min_confidence=0.40,
            max_attempts=5,
            min_attempt_spacing_ms=300.0,
            min_crop_width=30,
            min_crop_height=20,
            min_crop_quality=5.0
        )
        anpr_coordinator = ANPRCoordinator(recognizer=ocr_recognizer, config=anpr_config)

        pipeline = CameraPipeline(
            camera_id="cam22",
            stream_processor=stream_processor,
            vehicle_tracker=tracker,
            anpr_coordinator=anpr_coordinator
        )
        print("AI Pipeline successfully pre-warmed.")

        # Step 3: Start FFmpeg streaming segment 25-35s over RTSP TCP
        print("[3/5] Starting FFmpeg publish (25s - 35s, TCP)...")
        ffmpeg_cmd = [
            "ffmpeg",
            "-re",
            "-ss", "25.0",
            "-t", "10.0",
            "-i", video_path,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-an",
            "-rtsp_transport", "tcp",
            "-f", "rtsp",
            rtsp_url
        ]
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        if ffmpeg_proc.poll() is not None:
            print("ERROR: FFmpeg exited early. Check syntax or file.")
            return 1
        print("FFmpeg publishing successfully (PID: {})".format(ffmpeg_proc.pid))

        # Step 4: Verify RTSP client connection
        print("[4/5] Connecting Sentinel RTSPClient over TCP...")
        rtsp_client = RTSPClient(
            rtsp_url=rtsp_url,
            camera_id="cam22",
            transport="tcp"
        )
        opened = rtsp_client.open()
        if not opened:
            print("ERROR: RTSPClient failed to open stream.")
            return 1
        print("RTSPClient connected successfully. Resolution: {}x{}, Codec: {}".format(
            rtsp_client.width, rtsp_client.height, rtsp_client.codec
        ))
        frame_reader = FrameReader(client=rtsp_client, pts_tracker=pts_tracker)

        # Step 6: Process Frames & Collect Real Metrics
        print("[5/5] Processing 25-35s stream through Sentinel pipeline...")
        
        frames_received = 0
        read_failures = 0
        clean_frames = 0
        corrupted_frames = 0
        pts_list = []
        pts_deltas = []
        
        yolo_frames_with_det = 0
        yolo_total_detections = 0
        yolo_classes_seen = set()
        yolo_confs = []
        yolo_latencies_ms = []

        tracks_seen = set()
        confirmed_tracks_seen = set()
        max_simultaneous_tracks = 0

        anpr_invocations = 0
        vehicle_crops_processed = 0
        anpr_raw_candidates = []
        ocr_latencies_ms = []
        recognized_plates = []
        anpr_all_results = []

        # Hook recognizer to measure latency and vehicle crop metrics accurately
        orig_recognize = ocr_recognizer.recognize
        def timed_recognize(crop, pts_ms=None):
            nonlocal anpr_invocations, vehicle_crops_processed
            anpr_invocations += 1
            if crop is not None:
                vehicle_crops_processed += 1
            t0 = time.perf_counter()
            cand = orig_recognize(crop, pts_ms=pts_ms)
            lat = (time.perf_counter() - t0) * 1000.0
            ocr_latencies_ms.append(lat)
            if cand is not None:
                anpr_raw_candidates.append({
                    "raw_text": cand.raw_text,
                    "normalized_plate": cand.normalized_plate,
                    "confidence": cand.confidence,
                    "is_valid": cand.is_valid_format,
                    "pts_ms": pts_ms,
                    "bbox": cand.plate_bbox,
                    "crop_shape": crop.shape if crop is not None else None
                })
            return cand
        ocr_recognizer.recognize = timed_recognize

        consecutive_read_fails = 0
        start_time = time.time()

        while True:
            # Check timeout or process death
            if ffmpeg_proc.poll() is not None and consecutive_read_fails > 10:
                print("FFmpeg finished and stream closed.")
                break
            if time.time() - start_time > 30.0:  # Safety timeout
                print("Execution timed out after 30 seconds.")
                break

            success, packet = frame_reader.read_packet()
            if not success or packet is None or packet.frame is None:
                read_failures += 1
                consecutive_read_fails += 1
                time.sleep(0.01)
                continue

            consecutive_read_fails = 0
            frames_received += 1

            # Validate frame data
            frame = packet.frame
            if isinstance(frame, np.ndarray) and frame.size > 0:
                # Check for corruption / gray frame
                mean_val = float(np.mean(frame))
                std_val = float(np.std(frame))
                if std_val < 3.0: # Virtually solid color
                    corrupted_frames += 1
                else:
                    clean_frames += 1
            else:
                corrupted_frames += 1

            # PTS Tracking
            if packet.pts_ms is not None:
                pts_list.append(packet.pts_ms)
                if packet.pts_delta_ms is not None:
                    pts_deltas.append(packet.pts_delta_ms)

            # Measure YOLO Latency & Run Pipeline
            t_yolo_start = time.perf_counter()
            pipe_result = pipeline.process_frame(packet)
            t_yolo_end = time.perf_counter()

            # Record YOLO metrics
            det = pipe_result.detection_result
            if det is not None:
                yolo_latencies_ms.append(det.inference_time_ms)
                if det.count > 0:
                    yolo_frames_with_det += 1
                    yolo_total_detections += det.count
                    for d in det.detections:
                        yolo_classes_seen.add(d.class_name)
                        yolo_confs.append(d.confidence)

            # Record Tracking metrics
            trk = pipe_result.tracking_result
            if trk is not None:
                active_cnt = trk.active_count
                if active_cnt > max_simultaneous_tracks:
                    max_simultaneous_tracks = active_cnt
                for t in trk.active_tracks:
                    tracks_seen.add(t.track_id)
                    if t.state == TrackState.CONFIRMED:
                        confirmed_tracks_seen.add(t.track_id)

            # Record ANPR results
            if pipe_result.anpr_results:
                for ar in pipe_result.anpr_results:
                    anpr_all_results.append(ar)
                    if ar.status == "RECOGNIZED":
                        recognized_plates.append(ar)

        # End of processing loop
        print("\nProcessing completed successfully.")

        # Compute Metrics
        monotonic_pass = True
        for i in range(1, len(pts_list)):
            if pts_list[i] < pts_list[i - 1]:
                monotonic_pass = False
                break

        avg_pts_delta = statistics.mean(pts_deltas) if pts_deltas else 0.0
        est_fps = 1000.0 / avg_pts_delta if avg_pts_delta > 0 else 0.0
        yolo_med_latency = statistics.median(yolo_latencies_ms) if yolo_latencies_ms else 0.0
        ocr_med_latency = statistics.median(ocr_latencies_ms) if ocr_latencies_ms else 0.0

        # Print structured metrics
        print("\n" + "="*50)
        print("METRICS COLLECTED:")
        print(f"Frames received: {frames_received}")
        print(f"Read failures: {read_failures}")
        print(f"Clean frames: {clean_frames}")
        print(f"Corrupted frames: {corrupted_frames}")
        print(f"First PTS: {pts_list[0] if pts_list else None}")
        print(f"Last PTS: {pts_list[-1] if pts_list else None}")
        print(f"Monotonic PASS: {monotonic_pass}")
        print(f"Avg PTS delta: {avg_pts_delta:.2f} ms")
        print(f"Estimated FPS: {est_fps:.2f}")
        print(f"YOLO frames with det: {yolo_frames_with_det}")
        print(f"YOLO total detections: {yolo_total_detections}")
        print(f"YOLO classes seen: {list(yolo_classes_seen)}")
        print(f"YOLO confidence range: min={min(yolo_confs) if yolo_confs else 0:.3f}, max={max(yolo_confs) if yolo_confs else 0:.3f}")
        print(f"YOLO median latency: {yolo_med_latency:.2f} ms")
        print(f"Tracks total: {len(tracks_seen)}")
        print(f"Confirmed tracks: {len(confirmed_tracks_seen)}")
        print(f"Max simultaneous tracks: {max_simultaneous_tracks}")
        print(f"ANPR invocations: {anpr_invocations}")
        print(f"Vehicle crops processed: {vehicle_crops_processed}")
        print(f"EasyOCR reached: {'YES' if ocr_recognizer.call_count > 0 else 'NO'}")
        print(f"Raw OCR candidates count: {len(anpr_raw_candidates)}")
        for idx, c in enumerate(anpr_raw_candidates):
            print(f"  Candidate {idx+1}: raw='{c['raw_text']}', norm='{c['normalized_plate']}', conf={c['confidence']:.4f}, valid={c['is_valid']}, crop={c['crop_shape']}")
        print(f"Recognized valid plates: {len(recognized_plates)}")
        for r in recognized_plates:
            print(f"  Recognized: plate={r.normalized_plate}, conf={r.confidence:.4f}, track={r.track_id}")
        print(f"ANPR results total: {len(anpr_all_results)}")
        for ar in anpr_all_results:
            print(f"  ANPRResult: status={ar.status}, raw='{ar.raw_text}', norm='{ar.normalized_plate}', valid={ar.is_valid_format}, conf={ar.confidence:.4f}")
        print(f"OCR median latency: {ocr_med_latency:.2f} ms")
        print("="*50 + "\n")

    finally:
        # Cleanup
        print("Cleaning up resources...")
        if rtsp_client is not None:
            rtsp_client.close()
        if ffmpeg_proc is not None and ffmpeg_proc.poll() is None:
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.wait(timeout=3)
            except Exception:
                ffmpeg_proc.kill()
            print("FFmpeg stopped.")
        if mediamtx_proc is not None and mediamtx_proc.poll() is None:
            mediamtx_proc.terminate()
            try:
                mediamtx_proc.wait(timeout=3)
            except Exception:
                mediamtx_proc.kill()
            print("MediaMTX stopped.")


if __name__ == "__main__":
    sys.exit(main() or 0)
