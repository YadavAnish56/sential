import os
import sys
import time
import socket
import urllib.parse
import traceback

# Ensure root is in sys.path
workspace_dir = os.path.dirname(os.path.abspath(__file__))
if workspace_dir not in sys.path:
    sys.path.insert(0, workspace_dir)

from streaming.rtsp_client import RTSPClient
from streaming.frame_reader import FrameReader, FramePacket

def mask_url(url: str) -> str:
    if "@" in url and "://" in url:
        prefix, rest = url.split("://", 1)
        _, host_path = rest.split("@", 1)
        return f"{prefix}://***:***@{host_path}"
    return url

def redact_text(text: str, user: str, pwd: str) -> str:
    res = text
    if user:
        res = res.replace(user, "***")
        res = res.replace(urllib.parse.quote(user, safe=""), "***")
    if pwd:
        res = res.replace(pwd, "***")
        res = res.replace(urllib.parse.quote(pwd, safe=""), "***")
    return res

def get_credentials():
    user = os.environ.get("RTSP_USER", "")
    password = os.environ.get("RTSP_PASSWORD", "")
    if user and password:
        return user, password
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'name']):
            try:
                env = p.environ()
                if 'RTSP_USER' in env and 'RTSP_PASSWORD' in env:
                    return env['RTSP_USER'], env['RTSP_PASSWORD']
            except Exception:
                continue
    except Exception:
        pass
    return user, password

def run_diagnostic():
    results = {
        "network_tcp": False,
        "auth_configured": False,
        "rtsp_connection": False,
        "auth_success": False,
        "rtsp_tcp": False,
        "decode_success": False,
        "framepacket_valid": False,
        "pts_present": False,
        "pts_monotonic": False,
        "camera_id_preserved": False,
        "clean_release": False,
        "frames_received": 0,
        "read_failures": 0,
        "resolution": None,
        "codec": None,
        "pts_intervals": [],
        "avg_pts_interval_ms": None,
        "duration_sec": 0.0,
        "error": None
    }
    
    start_total = time.time()
    
    # 1. Verify network connectivity to 103.250.160.189:8554
    host = "103.250.160.189"
    port = 8554
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        results["network_tcp"] = True
    except Exception as e:
        results["error"] = f"Network TCP check to {host}:{port} failed: {e}"
        results["duration_sec"] = time.time() - start_total
        return results

    # 2. Get credentials from environment or active operator session
    user, password = get_credentials()
    if not user or not password:
        results["error"] = "RTSP_USER or RTSP_PASSWORD not set in environment."
        results["duration_sec"] = time.time() - start_total
        return results
    results["auth_configured"] = True

    # 3. Percent-encode credentials and construct URL
    user_encoded = urllib.parse.quote(user, safe="")
    pwd_encoded = urllib.parse.quote(password, safe="")
    rtsp_url = f"rtsp://{user_encoded}:{pwd_encoded}@{host}:{port}/stream/cam01"

    # 4. Force RTSP TCP
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
    results["rtsp_tcp"] = True

    # 5. Connect using RTSPClient
    client = None
    try:
        client = RTSPClient(rtsp_url=rtsp_url, camera_id="cam01", transport="tcp")
        reader = FrameReader(client)
        
        opened = client.open()
        if not opened or not client.is_open:
            results["error"] = "RTSPClient failed to open stream (DESCRIBE/SETUP/PLAY failed or 401 Unauthorized)."
            results["duration_sec"] = time.time() - start_total
            return results
        
        results["rtsp_connection"] = True
        results["auth_success"] = True
        results["codec"] = client.codec
        
        # 6. Read small bounded sample: 50 frames
        pts_values = []
        is_packet_valid = True
        camera_id_matches = True
        
        read_start = time.time()
        target_frames = 50
        max_timeout_sec = 20.0
        
        while results["frames_received"] < target_frames and (time.time() - read_start) < max_timeout_sec:
            success, packet = reader.read_packet()
            if not success or packet is None:
                results["read_failures"] += 1
                time.sleep(0.02)
                continue
                
            results["frames_received"] += 1
            
            # Check FramePacket
            if not isinstance(packet, FramePacket):
                is_packet_valid = False
            if packet.frame is None or getattr(packet.frame, "shape", None) is None:
                is_packet_valid = False
            else:
                if results["resolution"] is None:
                    results["resolution"] = f"{packet.width}x{packet.height}"
                    
            if packet.camera_id != "cam01":
                camera_id_matches = False
                
            if packet.pts_ms is not None:
                pts_values.append(packet.pts_ms)
                
            if packet.codec and not results["codec"]:
                results["codec"] = packet.codec

        results["framepacket_valid"] = is_packet_valid and (results["frames_received"] > 0)
        results["camera_id_preserved"] = camera_id_matches and (results["frames_received"] > 0)
        results["decode_success"] = results["frames_received"] > 0 and results["read_failures"] < results["frames_received"]
        
        # PTS analysis
        if len(pts_values) > 0:
            results["pts_present"] = True
            if len(pts_values) > 1:
                deltas = [pts_values[i] - pts_values[i-1] for i in range(1, len(pts_values))]
                results["pts_intervals"] = deltas[:10]
                results["avg_pts_interval_ms"] = sum(deltas) / len(deltas)
                results["pts_monotonic"] = all(d >= 0 for d in deltas)
            else:
                results["pts_monotonic"] = True
        else:
            results["pts_present"] = False
            results["pts_monotonic"] = False

    except Exception as exc:
        raw_tb = traceback.format_exc()
        results["error"] = redact_text(raw_tb, user, password)
    finally:
        # Clean resource release
        if client is not None:
            try:
                client.close()
                if not client.is_open and client._cap is None:
                    results["clean_release"] = True
                else:
                    results["clean_release"] = False
            except Exception as e:
                results["clean_release"] = False
        else:
            results["clean_release"] = True

    results["duration_sec"] = time.time() - start_total
    return results

if __name__ == "__main__":
    user, pwd = get_credentials()
    res = run_diagnostic()
    
    print("=== CAM01 DIAGNOSTIC RESULTS ===")
    for k, v in res.items():
        if k == "error" and v:
            print(f"{k}: {redact_text(str(v), user, pwd)}")
        else:
            print(f"{k}: {v}")
