"""CameraStatusHistory SQLAlchemy model — audit trail of camera status changes."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database.connection import Base


class CameraStatusHistory(Base):
    """A recorded status transition for a camera."""

    __tablename__ = "camera_status_history"

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False)
    status = Column(String(50), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<CameraStatusHistory cam={self.camera_id} {self.status}>"
