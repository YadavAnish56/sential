"""
Seed demo data for the Phase 13 map.

IMPORTANT — this writes SEEDED DEMO DATA, not detections.
The events created here were never produced by YOLO, the tracker, or EasyOCR.
They exist so the camera map and vehicle movement path can be demonstrated
without a live pipeline, and they must never be cited as ANPR evidence.

Idempotent: re-running it will not duplicate rows.

Usage (from the backend/ directory):
    DATABASE_URL="sqlite:///./sentinel_local.db" python scripts/seed_demo_map_data.py
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.connection import Base, SessionLocal, engine
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.models.alert import Alert

# Real surveyed locations in Surat, Gujarat.
DEMO_CAMERAS = [
    ("CAM-001", "Ring Road Junction", "Athwa, Surat", 21.1702, 72.8311, "active"),
    ("CAM-002", "Adajan Gate", "Adajan, Surat", 21.1959, 72.7933, "active"),
    ("CAM-003", "Varachha Checkpoint", "Varachha, Surat", 21.2049, 72.8757, "active"),
    ("CAM-004", "Dumas Road Camera", "Dumas, Surat", 21.0833, 72.7333, "offline"),
    # Deliberately left without coordinates: proves the map skips unsurveyed
    # cameras instead of plotting them at (0, 0).
    ("CAM-005", "Unsurveyed Camera", "Location pending survey", None, None, "offline"),
]

DEMO_PLATE = "GJ05AB1234"
LOCAL_STREAM_URL = "rtsp://127.0.0.1:8554/stream"


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        cameras = {}
        for code, name, location, lat, lon, status in DEMO_CAMERAS:
            camera = db.query(Camera).filter(Camera.camera_code == code).first()
            if camera is None:
                camera = Camera(
                    camera_code=code,
                    name=name,
                    location=location,
                    latitude=lat,
                    longitude=lon,
                    status=status,
                    vendor="demo",
                    stream_url=LOCAL_STREAM_URL if code == "CAM-001" else None,
                )
                db.add(camera)
                db.commit()
                db.refresh(camera)
                print(f"  + camera {code:<9} {name}")
            else:
                print(f"  = camera {code:<9} already present (left untouched)")
            cameras[code] = camera

        vehicle = db.query(Vehicle).filter(Vehicle.plate_number == DEMO_PLATE).first()
        if vehicle is None:
            vehicle = Vehicle(
                plate_number=DEMO_PLATE,
                vehicle_type="car",
                color="white",
                make="demo",
                model="demo",
            )
            db.add(vehicle)
            db.commit()
            db.refresh(vehicle)
            print(f"  + vehicle {DEMO_PLATE} (seeded, not recognized by ANPR)")
        else:
            print(f"  = vehicle {DEMO_PLATE} already present")

        # A west-to-east run across Surat, one sighting every 12 minutes.
        base = datetime(2026, 9, 10, 9, 0, 0)
        route = ["CAM-002", "CAM-001", "CAM-003", "CAM-005"]

        existing = (
            db.query(Event).filter(Event.vehicle_id == vehicle.id).count()
        )
        if existing == 0:
            for index, code in enumerate(route):
                db.add(
                    Event(
                        camera_id=cameras[code].id,
                        vehicle_id=vehicle.id,
                        event_type="seeded_demo_sighting",
                        object_type="vehicle",
                        confidence=0.90,
                        timestamp=base + timedelta(minutes=12 * index),
                    )
                )
            db.commit()
            print(f"  + {len(route)} seeded sightings across {len(route)} cameras")
        else:
            print(f"  = {existing} event(s) already linked to {DEMO_PLATE}")

        if db.query(Alert).count() == 0:
            db.add(
                Alert(
                    camera_id=cameras["CAM-001"].id,
                    vehicle_id=vehicle.id,
                    alert_type="SEEDED_DEMO",
                    severity="high",
                    message=f"Seeded demo alert for {DEMO_PLATE} (not a real detection)",
                    timestamp=base,
                    status="new",
                )
            )
            db.commit()
            print("  + 1 seeded demo alert")
        else:
            print("  = alerts already present")

        print()
        print("SEEDED DEMO DATA — these rows are not ANPR output and are not")
        print("evidence of any detection, recognition, or accuracy claim.")
    finally:
        db.close()


if __name__ == "__main__":
    print(f"Seeding demo map data into: {os.environ.get('DATABASE_URL', '(default)')}")
    seed()
