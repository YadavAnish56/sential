"""
Service for synchronizing authoritative government camera catalogue into PostgreSQL.
Ensures zero credential leakage, idempotent upserts, field preservation, and clean error handling.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.config import settings
from app.models.camera import Camera
from app.schemas.camera import CatalogSyncResponse, CatalogSyncStats, sanitize_stream_url
from streaming.camera_catalog import CameraCatalog, CameraCatalogItem, CatalogFetchResult

logger = logging.getLogger("sentinel.services.catalog_sync")


class CameraCatalogSyncService:
    """Manages transactional synchronization from the authoritative camera catalogue into PostgreSQL."""

    def __init__(self, db: Session, catalog: CameraCatalog | None = None) -> None:
        self.db = db
        self.catalog = catalog or CameraCatalog(
            catalog_url=settings.RTSP_CATALOG_URL,
            auth_token=settings.RTSP_CATALOG_TOKEN,
        )

    def sync(
        self,
        custom_url: str | None = None,
        auth_token: str | None = None,
        raw_items: Sequence[CameraCatalogItem] | None = None,
    ) -> CatalogSyncResponse:
        """
        Synchronize cameras from authoritative catalogue.

        If raw_items is supplied, syncs those directly without an HTTP fetch (e.g. for testing).
        Otherwise fetches from custom_url or configured RTSP_CATALOG_URL.

        Credentials are never logged, returned, or persisted.
        """
        effective_token = auth_token or settings.RTSP_CATALOG_TOKEN
        target_url = custom_url or self.catalog.catalog_url

        if raw_items is not None:
            # Direct items provided (e.g., test or pre-parsed catalogue)
            items = list(raw_items)
            fetch_result = CatalogFetchResult(success=True, status_code=200, cameras=items)
        else:
            # Reconfigure catalog instance if custom token/url provided
            client = self.catalog
            if custom_url != client.catalog_url or effective_token != client.auth_token:
                client = CameraCatalog(catalog_url=target_url, auth_token=effective_token)

            fetch_result = client.fetch()

        stats = CatalogSyncStats()

        if not fetch_result.success:
            auth_msg = "CATALOGUE ACCESS BLOCKED — Authentication required" if fetch_result.requires_auth else (fetch_result.error_message or "Catalogue fetch failed")
            logger.warning(
                "Catalogue synchronization failed: requires_auth=%s, status=%s, error=%s",
                fetch_result.requires_auth,
                fetch_result.status_code,
                fetch_result.error_message,
            )
            return CatalogSyncResponse(
                success=False,
                status_code=fetch_result.status_code,
                message=auth_msg,
                requires_auth=fetch_result.requires_auth,
                redirect_url=fetch_result.redirect_url,
                stats=stats,
                source_url=target_url,
            )

        items = fetch_result.cameras
        stats.total_received = len(items)

        # Track seen normalized codes within the current batch to prevent duplicates in payload
        seen_codes: set[str] = set()
        now = datetime.utcnow()

        try:
            for item in items:
                # 1. Validate mandatory identifier
                raw_code = (item.camera_id or "").strip()
                if not raw_code:
                    stats.skipped += 1
                    continue

                code_upper = raw_code.upper()
                if code_upper in seen_codes:
                    # Duplicate entry within same payload
                    stats.skipped += 1
                    continue
                seen_codes.add(code_upper)

                # Collect capability statistics
                if item.latitude is not None and item.longitude is not None:
                    stats.with_coordinates += 1
                if item.rtsp_url:
                    stats.with_rtsp += 1
                if item.webrtc_url:
                    stats.with_whep += 1

                # 2. Sanitize stream URL (strip any user:pass)
                safe_stream_url = sanitize_stream_url(item.rtsp_url)

                # 3. Lookup existing camera in database (case-insensitive)
                existing = (
                    self.db.query(Camera)
                    .filter(func.lower(Camera.camera_code) == raw_code.lower())
                    .first()
                )

                if existing:
                    # Check for actual changes and update while preserving existing values
                    changed = False

                    # Name: update only if non-empty and changed
                    if item.name and item.name.strip() and item.name.strip() != existing.name:
                        existing.name = item.name.strip()
                        changed = True

                    # Location: preserve existing if catalogue is None/empty
                    if item.location is not None and item.location.strip():
                        loc_clean = item.location.strip()
                        if loc_clean != existing.location:
                            existing.location = loc_clean
                            changed = True

                    # Latitude: preserve existing if catalogue is None
                    if item.latitude is not None and item.latitude != existing.latitude:
                        existing.latitude = item.latitude
                        changed = True

                    # Longitude: preserve existing if catalogue is None
                    if item.longitude is not None and item.longitude != existing.longitude:
                        existing.longitude = item.longitude
                        changed = True

                    # Stream URL: update only if authoritative URL is provided and differs
                    if safe_stream_url is not None and safe_stream_url != existing.stream_url:
                        existing.stream_url = safe_stream_url
                        changed = True

                    # Vendor / Department: preserve existing if catalogue is None
                    if item.department is not None and item.department.strip():
                        dept_clean = item.department.strip()
                        if dept_clean != existing.vendor:
                            existing.vendor = dept_clean
                            changed = True

                    if changed:
                        existing.updated_at = now
                        stats.updated += 1
                    else:
                        stats.unchanged += 1

                else:
                    # 4. Create new camera with honest 'registered' status
                    new_cam = Camera(
                        camera_code=raw_code,
                        name=item.name.strip() if (item.name and item.name.strip()) else f"Camera {raw_code}",
                        location=item.location.strip() if (item.location and item.location.strip()) else None,
                        latitude=item.latitude,
                        longitude=item.longitude,
                        stream_url=safe_stream_url,
                        status="registered",
                        vendor=item.department.strip() if (item.department and item.department.strip()) else None,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(new_cam)
                    stats.created += 1

            self.db.commit()

        except Exception as exc:
            self.db.rollback()
            logger.error("Database error during catalogue sync: %s", exc, exc_info=True)
            return CatalogSyncResponse(
                success=False,
                status_code=500,
                message=f"Database synchronization error: {str(exc)}",
                requires_auth=False,
                stats=stats,
                source_url=target_url,
            )

        return CatalogSyncResponse(
            success=True,
            status_code=200,
            message=(
                f"Synchronized {stats.total_received} catalogue items "
                f"({stats.created} created, {stats.updated} updated, "
                f"{stats.unchanged} unchanged, {stats.skipped} skipped)"
            ),
            requires_auth=False,
            stats=stats,
            source_url=target_url,
        )
