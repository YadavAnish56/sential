"""
Phase 31B — Provision Legitimate Government Cameras into Sentinel
Safely provisions and reconciles CAM-001 through CAM-008 in PostgreSQL.
Enforces:
1. Zero credential leakage: stream URLs are sanitized.
2. No coordinate fabrication: CAM-007 coordinates set to NULL; only CAM-001 has verified coords.
3. Runtime health check: Status is determined via real stream probe, not forced to 'online'.
4. Idempotency: Running multiple times preserves integrity without duplicates.
5. CAM-CRUD preserved as clearly identified TEST NODE.
"""

import os
import sys
import time
import urllib.parse
from datetime import datetime

# Ensure project and backend on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

import cv2
from app.database.connection import SessionLocal
from app.models.camera import Camera

GATEWAY_HOST = "103.250.160.189"
GATEWAY_RTSP_PORT = "8554"

# Verified cameras specification
CAMERAS_SPEC = [
    {
        "camera_code": "CAM-001",
        "stream_id": "cam01",
        "name": "Camera 01",
        "location": "Surat",
        "latitude": 21.1702,
        "longitude": 72.8311,
        "vendor": "Gujarat Police Traffic Branch"
    },
    {
        "camera_code": "CAM-002",
        "stream_id": "cam02",
        "name": "Camera 02",
        "location": None,
        "latitude": None,
        "longitude": None,
        "vendor": None
    },
    {
        "camera_code": "CAM-003",
        "stream_id": "cam03",
        "name": "Camera 03",
        "location": None,
        "latitude": None,
        "longitude": None,
        "vendor": None
    },
    {
        "camera_code": "CAM-004",
        "stream_id": "cam04",
        "name": "Camera 04",
        "location": None,
        "latitude": None,
        "longitude": None,
        "vendor": None
    },
    {
        "camera_code": "CAM-005",
        "stream_id": "cam05",
        "name": "Camera 05",
        "location": None,
        "latitude": None,
        "longitude": None,
        "vendor": None
    },
    {
        "camera_code": "CAM-006",
        "stream_id": "cam06",
        "name": "Camera 06",
        "location": None,
        "latitude": None,
        "longitude": None,
        "vendor": None
    },
    {
        "camera_code": "CAM-007",
        "stream_id": "cam07",
        "name": "Camera 07",
        "location": "Ahmedabad",
        "latitude": None,  # Explicitly NULL per User Correction 1
        "longitude": None, # Explicitly NULL per User Correction 1
        "vendor": None
    },
    {
        "camera_code": "CAM-008",
        "stream_id": "cam08",
        "name": "Camera 08",
        "location": None,
        "latitude": None,
        "longitude": None,
        "vendor": None
    }
]


def probe_runtime_stream_status(stream_id: str) -> tuple[str, dict]:
    """
    Probe the upstream RTSP stream in real-time to determine authentic runtime health.
    Uses credentials strictly in-memory; returns status ('online' | 'degraded' | 'offline').
    """
    user = os.environ.get("RTSP_USER", "")
    pwd = os.environ.get("RTSP_PASSWORD", "")
    safe_user = urllib.parse.quote(user, safe="")
    safe_pwd = urllib.parse.quote(pwd, safe="")
    
    rtsp_url = f"rtsp://{safe_user}:{safe_pwd}@{GATEWAY_HOST}:{GATEWAY_RTSP_PORT}/stream/{stream_id}"
    
    t0 = time.time()
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
    
    opened = cap.isOpened()
    frame_read = False
    width, height = None, None
    
    if opened:
        ret, frame = cap.read()
        if ret and frame is not None:
            frame_read = True
            height, width = frame.shape[:2]
    
    cap.release()
    elapsed = round(time.time() - t0, 2)
    
    if opened and frame_read:
        status = "online"
    elif opened and not frame_read:
        status = "degraded"
    else:
        status = "offline"
        
    diag = {
        "opened": opened,
        "frame_read": frame_read,
        "resolution": f"{width}x{height}" if width else "N/A",
        "probe_time_s": elapsed,
        "status": status
    }
    return status, diag


def provision_cameras():
    print("================================================================================")
    print("PHASE 31B: PROVISIONING LEGITIMATE GOVERNMENT CAMERAS INTO SENTINEL")
    print("================================================================================")
    
    db = SessionLocal()
    try:
        # Step 1: Probe runtime health for each government camera feed
        print("\n--- STEP 1: Probing Real-Time Stream Health (cam01 .. cam08) ---")
        stream_health = {}
        for spec in CAMERAS_SPEC:
            sid = spec["stream_id"]
            code = spec["camera_code"]
            status, diag = probe_runtime_stream_status(sid)
            stream_health[code] = (status, diag)
            print(f"[{code} / {sid}] Status: {status.upper()} | Res: {diag['resolution']} | Probe: {diag['probe_time_s']}s")

        # Step 2: Database Provisioning / Reconciliation
        print("\n--- STEP 2: Database Registry Transaction ---")
        now = datetime.utcnow()
        created_count = 0
        updated_count = 0

        for spec in CAMERAS_SPEC:
            code = spec["camera_code"]
            sid = spec["stream_id"]
            sanitized_url = f"rtsp://{GATEWAY_HOST}:{GATEWAY_RTSP_PORT}/stream/{sid}"
            status, diag = stream_health[code]

            existing = db.query(Camera).filter(Camera.camera_code == code).first()
            if existing:
                # Reconcile existing record
                existing.name = spec["name"]
                if spec["location"] is not None:
                    existing.location = spec["location"]
                existing.latitude = spec["latitude"]
                existing.longitude = spec["longitude"]
                existing.stream_url = sanitized_url
                existing.status = status
                if spec["vendor"]:
                    existing.vendor = spec["vendor"]
                existing.updated_at = now
                updated_count += 1
                print(f"[RECONCILED] {code} (ID {existing.id}) -> status={status}, coords=({existing.latitude}, {existing.longitude})")
            else:
                # Create new legitimate camera record
                new_cam = Camera(
                    camera_code=code,
                    name=spec["name"],
                    location=spec["location"],
                    latitude=spec["latitude"],
                    longitude=spec["longitude"],
                    stream_url=sanitized_url,
                    status=status,
                    vendor=spec["vendor"],
                    created_at=now,
                    updated_at=now,
                )
                db.add(new_cam)
                created_count += 1
                print(f"[CREATED] {code} -> status={status}, coords=({spec['latitude']}, {spec['longitude']})")

        # Step 3: Identify CAM-CRUD as TEST NODE without schema changes
        cam_crud = db.query(Camera).filter(Camera.camera_code == "CAM-CRUD").first()
        if cam_crud:
            cam_crud.name = "CRUD Test (Test Node)"
            cam_crud.location = "Test Environment"
            cam_crud.vendor = "Test Node"
            cam_crud.status = "offline"
            cam_crud.updated_at = now
            print(f"[PRESERVED] CAM-CRUD (ID {cam_crud.id}) -> explicitly labelled Test Node (offline)")

        db.commit()
        print(f"\nTransaction committed successfully: {created_count} created, {updated_count} updated.")

        # Step 4: Verification Query
        print("\n--- STEP 3: Current PostgreSQL Camera Registry ---")
        rows = db.query(Camera).order_by(Camera.id).all()
        print(f"Total cameras in database: {len(rows)}")
        print(f"{'ID':<4} | {'Code':<10} | {'Name':<22} | {'Status':<8} | {'Lat':<8} | {'Lon':<8} | {'Stream URL'}")
        print("-" * 95)
        for r in rows:
            lat_s = f"{r.latitude:.4f}" if r.latitude is not None else "NULL"
            lon_s = f"{r.longitude:.4f}" if r.longitude is not None else "NULL"
            print(f"{r.id:<4} | {r.camera_code:<10} | {r.name:<22} | {r.status:<8} | {lat_s:<8} | {lon_s:<8} | {r.stream_url}")

    except Exception as e:
        db.rollback()
        print(f"ERROR: Transaction failed and rolled back: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    provision_cameras()
