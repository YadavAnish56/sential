"""
Sentinel SQLAlchemy models package.

Importing this package registers every model on `Base.metadata`, which is what
Alembic autogenerate and `Base.metadata.create_all()` rely on.
"""

from app.models.alert import Alert
from app.models.camera import Camera
from app.models.camera_status import CameraStatusHistory
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.models.watchlist import Watchlist

__all__ = [
    "Alert",
    "Camera",
    "CameraStatusHistory",
    "Event",
    "Vehicle",
    "Watchlist",
]
