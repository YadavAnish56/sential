import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database.connection import SessionLocal
from app.models.camera import Camera

def seed_camera():
    db = SessionLocal()
    try:
        existing = db.query(Camera).filter(Camera.camera_code == "CAM-001").first()
        if not existing:
            new_camera = Camera(
                camera_code="CAM-001",
                name="Camera 01",
                location="Surat",
                latitude=21.1702,
                longitude=72.8311,
                status="offline",
                vendor="unknown",
                stream_url=None
            )
            db.add(new_camera)
            db.commit()
            print("Camera seeded successfully.")
        else:
            print("Camera CAM-001 already exists. Skipping.")
    finally:
        db.close()

if __name__ == "__main__":
    seed_camera()
