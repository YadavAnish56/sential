"""Camera SQLAlchemy model — registry of physical CCTV cameras."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from app.database.connection import Base


class Camera(Base):
    """
    A registered CCTV camera and its installation metadata.

    `latitude` / `longitude` are WGS84 decimal degrees and are optional:
    a camera may be registered before its survey coordinates are known.
    """

    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True)
    camera_code = Column(String(50), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    location = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    stream_url = Column(String(500), nullable=True)
    status = Column(String(50), nullable=False, default="offline")
    vendor = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<Camera {self.camera_code} ({self.name})>"
