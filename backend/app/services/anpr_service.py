"""
ANPR Persistence Service — Database persistence and deduplication for license plate recognition.

Accepts completed ANPR results, resolves camera identity against the cameras table,
deduplicates vehicles by canonical plate number, and creates linked detection events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.camera import Camera
from app.models.event import Event
from app.models.vehicle import Vehicle
from app.services.alert_service import AlertService

try:
    from ai_engine.anpr.normalization import normalize_plate
    from ai_engine.anpr.schemas import ANPRResult
except ModuleNotFoundError:
    import sys
    from pathlib import Path
    _sentinel_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    if _sentinel_root not in sys.path:
        sys.path.insert(0, _sentinel_root)
    from ai_engine.anpr.normalization import normalize_plate
    from ai_engine.anpr.schemas import ANPRResult


@dataclass(slots=True)
class ANPRPersistenceResult:
    """
    Structured outcome of an ANPR persistence operation.

    Attributes:
        event_id: Primary key of the newly created detection event.
        vehicle_id: Primary key of the matched or newly created vehicle.
        camera_id: Database primary key of the recording camera.
        camera_code: Unique code identifier of the camera.
        plate_number: Canonical uppercase normalized registration plate.
        confidence: OCR recognition confidence score.
        timestamp: Recorded event UTC timestamp.
        event_type: Classification of the event.
        snapshot_path: Optional storage path to the detection snapshot.
        event: The persisted Event SQLAlchemy model instance.
        vehicle: The persisted/reused Vehicle SQLAlchemy model instance.
        alert_id: Primary key of the newly created Alert, or None if no alert triggered.
        alert: The persisted Alert SQLAlchemy model instance, or None.
    """

    event_id: int
    vehicle_id: int
    camera_id: int
    camera_code: str
    plate_number: str
    confidence: float
    timestamp: datetime
    event_type: str
    snapshot_path: str | None = None
    event: Event | None = None
    vehicle: Vehicle | None = None
    alert_id: int | None = None
    alert: Alert | None = None


class ANPRPersistenceService:
    """
    Service responsible for persisting validated ANPR results into Sentinel database.

    Maintains vehicle identity deduplication across observations, resolves camera
    identities, records detection events, and enforces transaction isolation.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def persist_result(
        self,
        anpr_result: ANPRResult | dict[str, Any],
        camera_code: str | None = None,
        timestamp: datetime | None = None,
        snapshot_path: str | None = None,
        event_type: str = "anpr_detection",
        watchlist: Iterable[str] | None = None,
    ) -> ANPRPersistenceResult | None:
        """
        Persist a recognized ANPR result and optionally evaluate watchlist alerts.

        Args:
            anpr_result: Canonical ANPRResult dataclass (or dictionary adapter).
            camera_code: Camera code identifier; falls back to anpr_result.camera_id if omitted.
            timestamp: Explicit UTC datetime for the event. If omitted, current UTC time is used.
                       NOTE: anpr_result.pts_ms represents stream timing, not wall-clock UTC.
            snapshot_path: Optional file path or URL of the vehicle snapshot.
            event_type: Type identifier for the created Event record (default: "anpr_detection").
            watchlist: Optional collection of watchlist plate strings. If provided and matched,
                       creates an Alert atomically within the same database transaction.

        Returns:
            ANPRPersistenceResult with recorded IDs, details, and optional Alert on success,
            or None if the result is unpersistable, invalid, or the camera does not exist.
        """
        # 1. Unpack input and perform strict persistability checks
        if anpr_result is None:
            return None

        if isinstance(anpr_result, ANPRResult):
            status = anpr_result.status
            is_valid_format = anpr_result.is_valid_format
            raw_plate = anpr_result.normalized_plate
            confidence = float(anpr_result.confidence)
            source_camera = anpr_result.camera_id
        elif isinstance(anpr_result, dict):
            status = anpr_result.get("status")
            is_valid_format = bool(anpr_result.get("is_valid_format", False))
            raw_plate = anpr_result.get("normalized_plate", "")
            confidence = float(anpr_result.get("confidence", 0.0))
            source_camera = anpr_result.get("camera_id")
        else:
            return None

        # Accept only recognized, validly formatted results with non-empty plate
        if status != "RECOGNIZED" or not is_valid_format:
            return None

        if not raw_plate or not str(raw_plate).strip():
            return None

        # 2. Canonical plate normalization using existing Indian plate logic
        canonical_plate, valid = normalize_plate(str(raw_plate))
        if not valid or not canonical_plate:
            return None

        # 3. Camera identity resolution
        target_camera_code = camera_code or source_camera
        if not target_camera_code:
            return None

        camera = (
            self.db.query(Camera)
            .filter(Camera.camera_code == target_camera_code)
            .first()
        )
        if not camera:
            # Missing camera: create nothing, leave session clean
            return None

        # 4. Resolve event timestamp (explicit UTC or current UTC; NEVER convert pts_ms)
        event_timestamp = timestamp if timestamp is not None else datetime.utcnow()

        # 5. Vehicle upsert / reuse with concurrent IntegrityError safety
        try:
            vehicle = (
                self.db.query(Vehicle)
                .filter(Vehicle.plate_number == canonical_plate)
                .first()
            )
            if not vehicle:
                try:
                    with self.db.begin_nested():
                        vehicle = Vehicle(plate_number=canonical_plate)
                        self.db.add(vehicle)
                        self.db.flush()
                except IntegrityError:
                    # Nested transaction rolled back on collision; recover existing vehicle
                    vehicle = (
                        self.db.query(Vehicle)
                        .filter(Vehicle.plate_number == canonical_plate)
                        .first()
                    )
                    if not vehicle:
                        raise

            # 6. Event record creation linked to camera and vehicle
            event = Event(
                camera_id=camera.id,
                vehicle_id=vehicle.id,
                event_type=event_type,
                object_type="vehicle",
                confidence=confidence,
                timestamp=event_timestamp,
                snapshot_path=snapshot_path,
            )
            self.db.add(event)

            # 7. Optional Watchlist Alert evaluation within the same atomic transaction
            alert = None
            if watchlist:
                alert_service = AlertService(self.db)
                alert = alert_service.check_and_create_watchlist_alert(
                    plate_number=canonical_plate,
                    camera_id=camera.id,
                    vehicle_id=vehicle.id,
                    camera_code=camera.camera_code,
                    watchlist=watchlist,
                    timestamp=event_timestamp,
                    commit=False,
                )

            self.db.commit()
            self.db.refresh(event)
            self.db.refresh(vehicle)
            if alert is not None:
                self.db.refresh(alert)

            return ANPRPersistenceResult(
                event_id=event.id,
                vehicle_id=vehicle.id,
                camera_id=camera.id,
                camera_code=camera.camera_code,
                plate_number=vehicle.plate_number,
                confidence=event.confidence if event.confidence is not None else confidence,
                timestamp=event.timestamp,
                event_type=event.event_type,
                snapshot_path=event.snapshot_path,
                event=event,
                vehicle=vehicle,
                alert_id=alert.id if alert is not None else None,
                alert=alert,
            )
        except Exception:
            self.db.rollback()
            raise


def persist_anpr_result(
    db: Session,
    anpr_result: ANPRResult | dict[str, Any],
    camera_code: str | None = None,
    timestamp: datetime | None = None,
    snapshot_path: str | None = None,
    event_type: str = "anpr_detection",
    watchlist: Iterable[str] | None = None,
) -> ANPRPersistenceResult | None:
    """
    Convenience functional interface to persist an ANPR result using an active DB session.
    """
    service = ANPRPersistenceService(db)
    return service.persist_result(
        anpr_result=anpr_result,
        camera_code=camera_code,
        timestamp=timestamp,
        snapshot_path=snapshot_path,
        event_type=event_type,
        watchlist=watchlist,
    )
