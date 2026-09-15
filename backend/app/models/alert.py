"""Alert SQLAlchemy model — operator-facing alerts raised by the platform."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String

from app.database.connection import Base


class Alert(Base):
    """An alert raised against a camera, optionally linked to a vehicle."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    camera_id = Column(Integer, ForeignKey("cameras.id"), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=True)
    alert_type = Column(String(100), nullable=False)
    severity = Column(String(50), nullable=False)
    message = Column(String(500), nullable=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    status = Column(String(50), nullable=False, default="new")

    def __repr__(self) -> str:
        return f"<Alert {self.id} {self.alert_type} severity={self.severity}>"
