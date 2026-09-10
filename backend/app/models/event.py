"""Event SQLAlchemy model — detection events recorded against a camera."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from app.database.connection import Base


class Event(Base):
    """
    A single detection event observed by a camera.

    An event has no coordinates of its own: its geographic position is that of
    the camera that recorded it, resolved via `camera_id`.
    """

    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
    event_type = Column(String(100), nullable=False, index=True)
    object_type = Column(String(100), nullable=True)
    confidence = Column(Float, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    snapshot_path = Column(String(500), nullable=True)

    def __repr__(self) -> str:
        return f"<Event {self.id} {self.event_type} cam={self.camera_id}>"
