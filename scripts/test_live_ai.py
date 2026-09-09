"""
Sentinel — Phase 7.1: Live RTSP -> YOLO AI Diagnostic.

Full pipeline diagnostic:
    RTSP -> RTSPClient -> FrameReader -> FramePacket
      -> StreamProcessor -> VehicleDetector (YOLOv8)
      -> VehicleTracker -> diagnostic output

Failure Classification (A-I):
    A. Network unreachable / TCP connection failure
    B. RTSP connection timeout
    C. RTSP handshake/protocol failure
    D. Decoder failure
    E. Stream opens but no frames arrive
    F. Frames arrive but frame decoding fails
    G. Frames decode successfully but YOLO fails
    H. YOLO succeeds and detections are produced
    I. YOLO succeeds but no vehicles are present (NOT a failure)

Rules:
- Never save/download footage, frames, screenshots, snapshots, or recordings.
- Never print RTSP credentials or passwords.
- Zero detections is NOT classified as an AI failure.
- Uses existing streaming, AI, and tracking components unmodified.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Reconfigure stdout for UTF-8 on Windows to support diagnostic symbols
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel.scripts.test_live_ai")

DEFAULT_RTSP_URL = "rtsp://live.corp8.cloud:8554/stream/7"
DEFAULT_CAMERA_ID = "CAM-DEV-007"
DEFAULT_TIMEOUT_SEC = 15
DEFAULT_MAX_FRAMES = 30
DEFAULT_MAX_RECONNECT = 3


DEFAULT_MAX_RUNTIME_SEC = 60.0


def _mask_url(url: str) -> str:
    """Mask user credentials in RTSP URL for safe logging."""
    if "@" in url and "://" in url:
        prefix, rest = url.split("://", 1)
        _, host_path = rest.split("@", 1)
        return f"{prefix}://***:***@{host_path}"
    return url


def run_diagnostic(
    rtsp_url: str = DEFAULT_RTSP_URL,
    camera_id: str = DEFAULT_CAMERA_ID,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_reconnect: int = DEFAULT_MAX_RECONNECT,
    max_runtime_sec: float = DEFAULT_MAX_RUNTIME_SEC,
) -> int:
    """
    Execute the full RTSP -> YOLO AI diagnostic pipeline.

    Returns 0 if the pipeline completes (even with zero detections),
    non-zero if a failure is encountered.
    """
    safe_url = _mask_url(rtsp_url)

    # Parse hostname and port from URL
    from urllib.parse import urlparse
    parsed = urlparse(rtsp_url)
    hostname = parsed.hostname or "live.corp8.cloud"
    port = parsed.port or 8554

    # Tracking variables
    failure_class = None
    failure_detail = None
    dns_result = None
    tcp_result = None
    rtsp_connected = False
    stream_resolution = None
    stream_codec = None
    stream_fps_hint = None
    frames_received = 0
    frames_decoded = 0
    frames_processed = 0
    frames_skipped = 0
    inference_errors = 0
    total_detections = 0
    tracked_vehicle_count = 0
    pts_observations = []
    reconnect_attempts = 0
    final_health_state = "N/A"
    cuda_used = False
    detector_device = "N/A"
    elapsed_sec = 0.0
    footage_written = False

    # Phase 7.3 tracking & AI statistics
    detections_by_class = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
    detection_confidences: list[float] = []
    latencies_ms: list[float] = []
    all_seen_tracks: dict[int, Any] = {}
    track_history: dict[int, list[int]] = {}
    pts_discontinuities = 0

    print("=" * 72)
    print("SENTINEL -- RTSP -> YOLO -> TRACKING DIAGNOSTIC")
    print("=" * 72)
    print(f"  Camera ID        : {camera_id}")
    print(f"  Target URL       : {safe_url}")
    print(f"  Transport        : TCP (enforced)")
    print(f"  Timeout          : {timeout_sec}s")
    print(f"  Max Frames       : {max_frames}")
    print(f"  Max Runtime      : {max_runtime_sec}s")
    print(f"  Max Reconnect    : {max_reconnect}")
    print(f"  Video Storage    : DISABLED (zero footage saved)")
    print(f"  PTS Source       : CAP_PROP_POS_MSEC (authoritative)")
    print("=" * 72)

    overall_start = time.monotonic()

    # ---------------------------------------------------------------
    # PHASE 1: DNS Resolution
    # ---------------------------------------------------------------
    print(f"\n[PHASE 1] DNS Resolution for {hostname}")
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        ips = list({info[4][0] for info in addr_info})
        dns_result = f"Resolved to {len(ips)} address(es): {', '.join(ips[:4])}"
        print(f"  [OK] {dns_result}")
    except socket.gaierror as e:
        dns_result = f"DNS lookup failed: {e}"
        failure_class = "A"
        failure_detail = f"Network unreachable: DNS resolution failed for {hostname}: {e}"
        print(f"  [FAIL] {dns_result}")
        return _print_report(locals())

    # ---------------------------------------------------------------
    # PHASE 2: TCP Port Reachability
    # ---------------------------------------------------------------
    print(f"\n[PHASE 2] TCP Port Reachability ({hostname}:{port})")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        tcp_start = time.monotonic()
        sock.connect((hostname, port))
        tcp_elapsed = time.monotonic() - tcp_start
        tcp_result = f"TCP connection succeeded in {tcp_elapsed:.2f}s"
        print(f"  [OK] {tcp_result}")
    except socket.timeout:
        tcp_result = f"TCP connection timed out after {timeout_sec}s"
        failure_class = "A"
        failure_detail = (
            f"Network unreachable: TCP connection to {hostname}:{port} timed out "
            f"after {timeout_sec}s. This is a NETWORK issue, not a Sentinel issue."
        )
        print(f"  [FAIL] {tcp_result}")
        # Demonstrate reconnect backoff before exiting
        _demonstrate_reconnect(camera_id, max_reconnect)
        reconnect_attempts = max_reconnect
        final_health_state = "offline"
        elapsed_sec = time.monotonic() - overall_start
        return _print_report(locals())
    except OSError as e:
        tcp_result = f"TCP connection failed: {e}"
        failure_class = "A"
        failure_detail = f"Network unreachable: TCP connection to {hostname}:{port} failed: {e}"
        print(f"  [FAIL] {tcp_result}")
        _demonstrate_reconnect(camera_id, max_reconnect)
        reconnect_attempts = max_reconnect
        final_health_state = "offline"
        elapsed_sec = time.monotonic() - overall_start
        return _print_report(locals())
    finally:
        sock.close()

    # ---------------------------------------------------------------
    # PHASE 3: RTSP Handshake
    # ---------------------------------------------------------------
    print(f"\n[PHASE 3] RTSP Handshake via RTSPClient (TCP enforced)")
    from streaming.rtsp_client import RTSPClient
    from streaming.frame_reader import FrameReader
    from streaming.health import StreamHealthTracker
    from streaming.reconnect import ReconnectManager

    health = StreamHealthTracker(camera_id=camera_id)
    reconnect_mgr = ReconnectManager(camera_id=camera_id, initial_delay=2.0, max_delay=30.0)
    client = RTSPClient(rtsp_url=rtsp_url, camera_id=camera_id, transport="tcp")
    reader = FrameReader(client=client)

    health.set_connecting()

    # Use a thread with timeout for the blocking cv2.VideoCapture.open()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.open)
        try:
            rtsp_start = time.monotonic()
            rtsp_connected = future.result(timeout=timeout_sec)
            rtsp_elapsed = time.monotonic() - rtsp_start
        except FuturesTimeoutError:
            rtsp_connected = False
            rtsp_elapsed = timeout_sec
            try:
                client.close()
            except Exception:
                pass

    if not rtsp_connected:
        failure_class = "B"
        failure_detail = (
            f"RTSP connection to {safe_url} timed out or failed after {rtsp_elapsed:.2f}s. "
            "The RTSP endpoint is not serving streams at this time."
        )
        print(f"  [FAIL] RTSP handshake failed after {rtsp_elapsed:.2f}s")

        # Demonstrate bounded reconnect behavior
        print(f"\n[PHASE 3b] Reconnect Demonstration (max {max_reconnect} attempts)")
        for attempt in range(1, max_reconnect + 1):
            delay = reconnect_mgr.record_failure()
            health.set_reconnecting(attempt=attempt)
            print(f"  -> Attempt {attempt}: backoff={delay:.1f}s (capture released)")
            client.close()
        reconnect_attempts = max_reconnect
        health.set_offline(reason="RTSP handshake failed, max reconnect reached")
        final_health_state = health.status.value
        elapsed_sec = time.monotonic() - overall_start
        return _print_report(locals())

    # Connection succeeded
    health.set_online({
        "width": client.width,
        "height": client.height,
        "fps_hint": client.fps_hint,
        "codec": client.codec,
    })
    stream_resolution = f"{client.width}x{client.height}"
    stream_codec = client.codec or "unknown"
    stream_fps_hint = client.fps_hint

    print(f"  [OK] RTSP handshake succeeded in {rtsp_elapsed:.2f}s")
    print(f"       Resolution    : {stream_resolution}")
    print(f"       Codec         : {stream_codec}")
    print(f"       Advisory FPS  : {stream_fps_hint or 'unspecified'} (NOT used as authoritative timing)")

    # ---------------------------------------------------------------
    # PHASE 4: Frame Decode + StreamProcessor + YOLO + Tracker
    # ---------------------------------------------------------------
    print(f"\n[PHASE 4] Frame Read + AI Pipeline (max {max_frames} frames)")

    # Initialize StreamProcessor with VehicleDetector
    from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
    from ai_engine.detector import VehicleDetector, ModelNotAvailableError
    from ai_engine.config import DetectorConfig
    from ai_engine.tracking.tracker import VehicleTracker, TrackerConfig

    # Initialize YOLO detector
    print("  Initializing VehicleDetector...")
    try:
        det_config = DetectorConfig()
        detector = VehicleDetector(config=det_config)
    except Exception as e:
        failure_class = "G"
        failure_detail = f"VehicleDetector initialization failed: {e}"
        print(f"  [FAIL] {failure_detail}")
        client.close()
        health.set_offline(reason="Detector init failed")
        final_health_state = health.status.value
        elapsed_sec = time.monotonic() - overall_start
        return _print_report(locals())

    if not detector.is_ready:
        failure_class = "G"
        failure_detail = f"VehicleDetector not ready: {detector.error_message}"
        print(f"  [FAIL] {failure_detail}")
        client.close()
        health.set_offline(reason="Detector not ready")
        final_health_state = health.status.value
        elapsed_sec = time.monotonic() - overall_start
        return _print_report(locals())

    detector_device = detector.resolved_device
    cuda_used = detector_device == "cuda"
    print(f"  [OK] VehicleDetector ready: model={os.path.basename(det_config.model_path)}, "
          f"device={detector_device}, CUDA={cuda_used}")
    print(f"       Target classes: {dict(detector.class_id_to_name)}")

    # Initialize StreamProcessor
    sp_config = StreamProcessorConfig(frame_stride=1, target_fps=None)
    stream_proc = StreamProcessor(detector=detector, config=sp_config)

    # Initialize VehicleTracker
    tracker_config = TrackerConfig()
    tracker = VehicleTracker(config=tracker_config)

    # Read frames
    print(f"\n  {'Frame':<8} {'Status':<10} {'PTS (ms)':<14} {'Dets':<6} "
          f"{'Tracks':<8} {'Inference':<14} {'Notes'}")
    print(f"  {'-' * 74}")

    read_attempts = 0
    consecutive_read_failures = 0
    max_read_attempts = max_frames * 3  # Allow up to 3x attempts to account for read failures

    try:
        while frames_decoded < max_frames and read_attempts < max_read_attempts:
            if (time.monotonic() - overall_start) >= max_runtime_sec:
                print(f"\n  [LIMIT] Max runtime of {max_runtime_sec}s reached.")
                break

            read_attempts += 1
            success, packet = reader.read_packet()

            if not success or packet is None:
                frames_received += 1
                consecutive_read_failures += 1
                health.record_read_failure("Frame read failed")

                if consecutive_read_failures >= 10:
                    # Too many consecutive failures
                    if frames_decoded == 0:
                        failure_class = "E"
                        failure_detail = (
                            f"Stream opened but {consecutive_read_failures} consecutive read "
                            "failures with 0 frames decoded. Stream may be empty."
                        )
                    else:
                        failure_class = "F"
                        failure_detail = (
                            f"{consecutive_read_failures} consecutive read failures after "
                            f"{frames_decoded} successful frames. Decoder may have failed."
                        )
                    print(f"  [FAIL] {failure_detail}")
                    break
                continue

            # Successful frame decode
            frames_received += 1
            frames_decoded += 1
            consecutive_read_failures = 0
            health.record_frame(frames_decoded, packet.pts_ms)

            if packet.pts_ms is not None:
                pts_observations.append(packet.pts_ms)

            # Run through StreamProcessor (sampling + detection)
            det_result = stream_proc.process_packet(packet)

            if det_result is None:
                # Frame was skipped by sampling or errored
                frames_skipped += 1
                pts_str = f"{packet.pts_ms:.2f}" if packet.pts_ms is not None else "N/A"
                print(f"  {frames_decoded:<8} {'SKIP':<10} {pts_str:<14} {'-':<6} "
                      f"{'-':<8} {'-':<14} {'sampled out'}")
                continue

            frames_processed += 1

            # Accumulate detection latencies and class statistics
            latencies_ms.append(det_result.inference_time_ms)
            for d in det_result.detections:
                cname = d.class_name.lower()
                if cname in detections_by_class:
                    detections_by_class[cname] += 1
                detection_confidences.append(d.confidence)

            # Run through VehicleTracker
            tracking_result = tracker.update(det_result)
            active_tracks = len(tracking_result.active_tracks)
            det_count = det_result.count
            total_detections += det_count

            if tracking_result.is_discontinuity:
                pts_discontinuities += 1

            for t in tracking_result.active_tracks:
                all_seen_tracks[t.track_id] = t
                track_history.setdefault(t.track_id, []).append(frames_decoded)

            for t in tracking_result.lost_tracks:
                all_seen_tracks[t.track_id] = t

            pts_str = f"{packet.pts_ms:.2f}" if packet.pts_ms is not None else "N/A"
            inf_str = f"{det_result.inference_time_ms:.1f}ms"

            notes_parts = []
            if det_count > 0:
                classes = [d.class_name for d in det_result.detections]
                notes_parts.append(", ".join(classes))
            if tracking_result.new_tracks:
                notes_parts.append(f"+{len(tracking_result.new_tracks)} new")
            if tracking_result.lost_tracks:
                notes_parts.append(f"-{len(tracking_result.lost_tracks)} lost")
            notes = " | ".join(notes_parts) if notes_parts else ""

            print(f"  {frames_decoded:<8} {'OK':<10} {pts_str:<14} {det_count:<6} "
                  f"{active_tracks:<8} {inf_str:<14} {notes}")

    except KeyboardInterrupt:
        print("\n  [INTERRUPTED] User cancelled.")
    except ModelNotAvailableError as e:
        failure_class = "G"
        failure_detail = f"YOLO model not available during inference: {e}"
        print(f"  [FAIL] {failure_detail}")
    except Exception as e:
        logger.error("Unexpected error during pipeline: %s", e, exc_info=True)
        failure_class = "G"
        failure_detail = f"Unexpected pipeline error: {e}"
        print(f"  [FAIL] {failure_detail}")
    finally:
        print(f"\n[PHASE 5] Cleanup")
        client.close()
        health.set_offline(reason="Diagnostic completed")
        final_health_state = health.status.value
        print(f"  [OK] Stream resources released cleanly")

    # Update stats from StreamProcessor
    sp_stats = stream_proc.get_stats()
    inference_errors = sp_stats["inference_errors"]
    frames_skipped = sp_stats["frames_skipped"]

    # Tracking state aggregation
    from ai_engine.tracking.schemas import TrackState
    total_tracks_created = 0
    active_tracks_end = 0
    lost_tracks_end = 0
    for cam_id, cam_tracker in tracker._camera_trackers.items():
        total_tracks_created = cam_tracker.next_track_id - 1
        active_tracks_end = len([t for t in cam_tracker._active_tracks.values() if t.state in (TrackState.CONFIRMED, TrackState.TENTATIVE)])
        lost_tracks_end = len([t for t in cam_tracker._active_tracks.values() if t.state == TrackState.LOST])

    terminated_tracks_end = max(0, total_tracks_created - (active_tracks_end + lost_tracks_end))

    track_spans = [
        (frames[-1] - frames[0] + 1)
        for frames in track_history.values()
    ] if track_history else [0]
    min_track_age = min(track_spans) if track_spans else 0
    max_track_age = max(track_spans) if track_spans else 0

    # Stable track IDs across consecutive frames
    stable_tracks = [
        tid for tid, frames in track_history.items()
        if len(frames) >= 2 and any(frames[i+1] - frames[i] == 1 for i in range(len(frames) - 1))
    ]
    track_ids_stable = len(stable_tracks) > 0

    # Validation verdict determination
    if total_detections == 0:
        validation_verdict = "Vehicle detection not validated on this media."
        detection_validated = False
        tracking_validated = False
    elif track_ids_stable:
        validation_verdict = "Vehicle detection and tracking validated."
        detection_validated = True
        tracking_validated = True
    else:
        validation_verdict = "YOLO detection validated; tracking validation inconclusive."
        detection_validated = True
        tracking_validated = False

    elapsed_sec = time.monotonic() - overall_start

    # Classify success result
    if failure_class is None:
        if inference_errors > 0 and frames_processed == 0:
            failure_class = "G"
            failure_detail = (
                f"All {inference_errors} inference attempts failed. "
                "YOLO may have a model or GPU issue."
            )
        elif total_detections > 0:
            failure_class = "H"
            failure_detail = (
                f"Pipeline completed successfully. "
                f"{total_detections} vehicle detections produced across "
                f"{frames_processed} processed frames."
            )
        else:
            failure_class = "I"
            failure_detail = (
                f"Pipeline completed successfully with 0 detections. "
                "This is NOT an AI failure -- the scene may contain no target vehicles. "
                "The inference pipeline is functioning correctly."
            )

    return _print_report(locals())


def _demonstrate_reconnect(camera_id: str, max_attempts: int) -> None:
    """Demonstrate exponential backoff reconnect behavior without actual delays."""
    from streaming.reconnect import ReconnectManager

    mgr = ReconnectManager(camera_id=camera_id, initial_delay=2.0, max_delay=30.0)
    print(f"\n  Reconnect Backoff Demonstration ({max_attempts} attempts):")
    for attempt in range(1, max_attempts + 1):
        delay = mgr.record_failure()
        print(f"    Attempt {attempt}: backoff_delay={delay:.1f}s")


def _print_report(ctx: dict) -> int:
    """Print the final structured diagnostic report."""
    fc = ctx.get("failure_class")
    fd = ctx.get("failure_detail", "")

    # Determine if this is a success or failure
    is_success = fc in ("H", "I")

    print(f"\n{'=' * 72}")
    print("DIAGNOSTIC REPORT")
    print(f"{'=' * 72}")

    print(f"\n  FAILURE CLASSIFICATION")
    class_labels = {
        "A": "A - Network unreachable / TCP connection failure",
        "B": "B - RTSP connection timeout",
        "C": "C - RTSP handshake/protocol failure",
        "D": "D - Decoder failure",
        "E": "E - Stream opens but no frames arrive",
        "F": "F - Frames arrive but frame decoding fails",
        "G": "G - Frames decode but YOLO fails",
        "H": "H - YOLO succeeds, detections produced",
        "I": "I - YOLO succeeds, no vehicles present (not a failure)",
    }
    label = class_labels.get(fc, f"{fc} - Unknown")
    icon = "[PASS]" if is_success else "[FAIL]"
    print(f"  {icon} {label}")
    print(f"  Detail: {fd}")

    print(f"\n  CONNECTIVITY")
    print(f"    DNS Resolution      : {ctx.get('dns_result', 'N/A')}")
    print(f"    TCP Port Test       : {ctx.get('tcp_result', 'N/A')}")
    print(f"    RTSP Connected      : {ctx.get('rtsp_connected', False)}")
    print(f"    Stream Resolution   : {ctx.get('stream_resolution', 'N/A')}")
    print(f"    Stream Codec        : {ctx.get('stream_codec', 'N/A')}")
    print(f"    Advisory FPS Hint   : {ctx.get('stream_fps_hint', 'N/A')}")

    print(f"\n  FRAME STATISTICS")
    print(f"    Frames Received     : {ctx.get('frames_received', 0)}")
    print(f"    Frames Decoded      : {ctx.get('frames_decoded', 0)}")
    print(f"    Frames Processed    : {ctx.get('frames_processed', 0)}")
    print(f"    Frames Skipped      : {ctx.get('frames_skipped', 0)}")
    print(f"    Inference Errors    : {ctx.get('inference_errors', 0)}")

    print(f"\n  AI / DETECTION")
    print(f"    CUDA Used           : {ctx.get('cuda_used', False)}")
    print(f"    Detector Device     : {ctx.get('detector_device', 'N/A')}")
    print(f"    Total Detections    : {ctx.get('total_detections', 0)}")
    by_class = ctx.get("detections_by_class", {})
    print(f"    Detections By Class : car={by_class.get('car', 0)}, "
          f"motorcycle={by_class.get('motorcycle', 0)}, "
          f"bus={by_class.get('bus', 0)}, "
          f"truck={by_class.get('truck', 0)}")
    confs = ctx.get("detection_confidences", [])
    if confs:
        print(f"    Confidence Stats    : min={min(confs):.3f}, max={max(confs):.3f}, avg={sum(confs)/len(confs):.3f}")
    else:
        print(f"    Confidence Stats    : N/A (no detections)")

    latencies = ctx.get("latencies_ms", [])
    if latencies:
        warmup = latencies[0]
        steady = latencies[1:] if len(latencies) > 1 else [warmup]
        print(f"    Warmup Latency      : {warmup:.1f} ms")
        print(f"    Steady-State Latency: avg={sum(steady)/len(steady):.1f} ms (min={min(steady):.1f} ms, max={max(steady):.1f} ms)")
    else:
        print(f"    Latency Stats       : N/A")

    print(f"\n  TRACKING")
    print(f"    Total Tracks Created: {ctx.get('total_tracks_created', 0)}")
    print(f"    Active Tracks (End) : {ctx.get('active_tracks_end', 0)}")
    print(f"    Lost Tracks (End)   : {ctx.get('lost_tracks_end', 0)}")
    print(f"    Terminated Tracks   : {ctx.get('terminated_tracks_end', 0)}")
    print(f"    Track Age Range     : min={ctx.get('min_track_age', 0)}, max={ctx.get('max_track_age', 0)} frames")
    print(f"    Track ID Stability  : {'YES - stable across consecutive frames' if ctx.get('track_ids_stable') else 'NO - unstable or sparse'}")

    print(f"\n  PTS OBSERVATIONS")
    pts_obs = ctx.get("pts_observations", [])
    if pts_obs:
        print(f"    Total PTS samples   : {len(pts_obs)}")
        print(f"    First PTS           : {pts_obs[0]:.2f} ms")
        print(f"    Last PTS            : {pts_obs[-1]:.2f} ms")
        if len(pts_obs) > 1:
            deltas = [pts_obs[i+1] - pts_obs[i] for i in range(len(pts_obs) - 1)]
            avg_delta = sum(deltas) / len(deltas)
            print(f"    Avg PTS delta       : {avg_delta:.2f} ms")
            print(f"    Min/Max PTS delta   : {min(deltas):.2f} / {max(deltas):.2f} ms")
        print(f"    PTS Discontinuities : {ctx.get('pts_discontinuities', 0)}")
    else:
        print(f"    No PTS observations (stream did not produce frames)")

    print(f"\n  VALIDATION VERDICT")
    print(f"    Verdict: {ctx.get('validation_verdict', 'N/A')}")

    print(f"\n  OPERATIONAL")
    print(f"    Reconnect Attempts  : {ctx.get('reconnect_attempts', 0)}")
    print(f"    Final Health State  : {ctx.get('final_health_state', 'N/A')}")
    print(f"    Elapsed Time        : {ctx.get('elapsed_sec', 0):.2f}s")
    print(f"    Footage Written     : {ctx.get('footage_written', False)}")

    print(f"\n{'=' * 72}")

    return 0 if is_success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sentinel RTSP -> YOLO -> Tracking Diagnostic"
    )
    parser.add_argument(
        "--url", default=DEFAULT_RTSP_URL,
        help=f"RTSP stream URL (default: {DEFAULT_RTSP_URL})"
    )
    parser.add_argument(
        "--camera-id", default=DEFAULT_CAMERA_ID,
        help=f"Camera identifier (default: {DEFAULT_CAMERA_ID})"
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_SEC,
        help=f"Timeout in seconds for network/RTSP checks (default: {DEFAULT_TIMEOUT_SEC})"
    )
    parser.add_argument(
        "--max-frames", type=int, default=DEFAULT_MAX_FRAMES,
        help=f"Maximum frames to read and process (default: {DEFAULT_MAX_FRAMES})"
    )
    parser.add_argument(
        "--max-runtime", type=float, default=DEFAULT_MAX_RUNTIME_SEC,
        help=f"Maximum runtime in seconds (default: {DEFAULT_MAX_RUNTIME_SEC})"
    )
    parser.add_argument(
        "--max-reconnect", type=int, default=DEFAULT_MAX_RECONNECT,
        help=f"Maximum reconnect attempts for demonstration (default: {DEFAULT_MAX_RECONNECT})"
    )
    args = parser.parse_args()

    sys.exit(run_diagnostic(
        rtsp_url=args.url,
        camera_id=args.camera_id,
        timeout_sec=args.timeout,
        max_frames=args.max_frames,
        max_reconnect=args.max_reconnect,
        max_runtime_sec=args.max_runtime,
    ))
