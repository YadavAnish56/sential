"""
Alert Service for Sentinel.

Provides business logic for watchlist matching and alert generation:
- Canonicalizes watchlist entries using existing Indian plate normalization logic.
- Evaluates recognized Indian license plates against the supplied watchlist.
- Creates Alert model records linked to camera_id and vehicle_id.
- Operates within existing database transactions for atomic persistence.
- Strictly rejects invalid, non-Indian, low-confidence, or unconfirmed candidates.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Sequence
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.camera import Camera
from app.models.vehicle import Vehicle
from app.models.watchlist import Watchlist

try:
    from ai_engine.anpr.normalization import normalize_plate
except ModuleNotFoundError:
    import sys
    from pathlib import Path
    _sentinel_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    if _sentinel_root not in sys.path:
        sys.path.insert(0, _sentinel_root)
    from ai_engine.anpr.normalization import normalize_plate

logger = logging.getLogger("sentinel.backend.services.alert_service")

DEFAULT_WATCHLIST_ALERT_TYPE = "ANPR_WATCHLIST"
DEFAULT_WATCHLIST_SEVERITY = "high"


def canonicalize_watchlist(watchlist: Iterable[str] | None) -> set[str]:
    """
    Sanitize and canonicalize a collection of watchlist plate strings.

    Reuses existing Indian plate normalization logic (normalize_plate).
    Any malformed, non-Indian, or non-string entries are strictly filtered out,
    ensuring that only valid Indian registration numbers participate in matching.
    """
    if not watchlist:
        return set()

    canonical_set: set[str] = set()
    for item in watchlist:
        if not item or not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned:
            continue
        norm_plate, valid = normalize_plate(cleaned)
        if valid and norm_plate:
            canonical_set.add(norm_plate)
    return canonical_set


class AlertService:
    """
    Service responsible for watchlist evaluation and Alert record generation.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def check_and_create_watchlist_alert(
        self,
        plate_number: str,
        camera_id: int,
        vehicle_id: int,
        camera_code: str,
        watchlist: Iterable[str] | None = None,
        timestamp: datetime | None = None,
        severity: str = DEFAULT_WATCHLIST_SEVERITY,
        alert_type: str = DEFAULT_WATCHLIST_ALERT_TYPE,
        commit: bool = False,
    ) -> Alert | None:
        """
        Evaluate whether a plate matches the supplied or database watchlist and create an Alert.

        Args:
            plate_number: Raw or normalized plate string (will be re-validated via normalize_plate).
            camera_id: Primary key (int) of the recording camera.
            vehicle_id: Primary key (int) of the vehicle.
            camera_code: Human-readable camera code for the alert message.
            watchlist: Optional iterable of watchlist plate strings. If None, queries active
                       entries from the database watchlists table.
            timestamp: Explicit UTC datetime for the alert (defaults to datetime.utcnow()).
            severity: Default alert severity string (defaults to "high").
            alert_type: Classification string (defaults to "ANPR_WATCHLIST").
            commit: If True, commits the transaction immediately. If False,
                    flushes within the active transaction for outer atomic commit.

        Returns:
            Created Alert model instance on match, or None if no match or invalid input.
        """
        if not plate_number:
            return None

        # Strict validation: Canonical plate must be a valid Indian registration format
        canonical_plate, valid = normalize_plate(str(plate_number))
        if not valid or not canonical_plate:
            return None

        alert_severity = severity
        alert_message = None

        if watchlist is not None:
            # Explicit watchlist supplied (e.g. test overrides)
            target_watchlist = canonicalize_watchlist(watchlist)
            if not target_watchlist or canonical_plate not in target_watchlist:
                return None
            alert_message = f"Watchlist vehicle {canonical_plate} detected on camera {camera_code}"
        else:
            # Query active watchlist from database
            matched_entry = (
                self.db.query(Watchlist)
                .filter(Watchlist.plate_number == canonical_plate, Watchlist.is_active == True)
                .first()
            )
            if not matched_entry:
                return None
            if matched_entry.severity:
                alert_severity = matched_entry.severity
            if matched_entry.description:
                alert_message = (
                    f"Watchlist vehicle {canonical_plate} detected on camera {camera_code} "
                    f"({matched_entry.description})"
                )
            else:
                alert_message = f"Watchlist vehicle {canonical_plate} detected on camera {camera_code}"

        alert_time = timestamp if timestamp is not None else datetime.utcnow()

        alert = Alert(
            camera_id=camera_id,
            vehicle_id=vehicle_id,
            alert_type=alert_type,
            severity=alert_severity,
            message=alert_message,
            timestamp=alert_time,
            status="new",
        )

        self.db.add(alert)
        if commit:
            self.db.commit()
            self.db.refresh(alert)
        else:
            self.db.flush()

        logger.info(
            "Created %s alert for vehicle %s on camera %s (severity=%s)",
            alert_type,
            canonical_plate,
            camera_code,
            alert_severity,
        )
        return alert
