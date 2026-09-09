"""
Camera Catalogue Abstraction for Sentinel.

Provides dynamic retrieval, parsing, and management of available camera feeds
from the centralized catalogue endpoint without hardcoding camera lists.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("sentinel.streaming.catalog")


@dataclass
class CameraCatalogItem:
    """Represents a camera stream entry returned by the catalogue."""
    camera_id: str
    name: str = ""
    location: str | None = None
    codec: str | None = None
    is_live: bool = False
    rtsp_url: str | None = None
    webrtc_url: str | None = None
    hls_url: str | None = None
    width: int | None = None
    height: int | None = None
    fps_hint: float | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CameraCatalogItem:
        """Parse raw dictionary into a structured CameraCatalogItem."""
        # Detect camera ID from common conventions
        cam_id = str(
            data.get("camera_id")
            or data.get("id")
            or data.get("camera_code")
            or data.get("stream_id")
            or ""
        )

        name = str(data.get("name") or data.get("camera_name") or f"Camera {cam_id}")
        location = data.get("location")
        codec = data.get("codec") or data.get("video_codec")

        # Determine live status
        live_val = data.get("live") or data.get("is_live") or data.get("status")
        is_live = False
        if isinstance(live_val, bool):
            is_live = live_val
        elif isinstance(live_val, str):
            is_live = live_val.lower() in ("live", "online", "active", "true", "1")

        # Extract URLs
        rtsp_url = (
            data.get("rtsp_url")
            or data.get("rtsp")
            or data.get("stream_url")
        )
        webrtc_url = (
            data.get("webrtc_url")
            or data.get("whep_url")
            or data.get("webrtc")
        )
        hls_url = (
            data.get("hls_url")
            or data.get("hls")
        )

        # Extract stream properties if available
        props = data.get("properties") or data.get("stream_properties") or {}
        width = props.get("width") or data.get("width")
        height = props.get("height") or data.get("height")
        fps_hint = props.get("fps") or data.get("fps")

        return cls(
            camera_id=cam_id,
            name=name,
            location=location,
            codec=str(codec) if codec else None,
            is_live=is_live,
            rtsp_url=str(rtsp_url) if rtsp_url else None,
            webrtc_url=str(webrtc_url) if webrtc_url else None,
            hls_url=str(hls_url) if hls_url else None,
            width=int(width) if width is not None else None,
            height=int(height) if height is not None else None,
            fps_hint=float(fps_hint) if fps_hint is not None else None,
            properties=props if isinstance(props, dict) else {},
            raw_data=data,
        )


@dataclass
class CatalogFetchResult:
    """Result of attempting to fetch and parse the catalogue."""
    success: bool
    status_code: int | None = None
    cameras: list[CameraCatalogItem] = field(default_factory=list)
    error_message: str | None = None
    requires_auth: bool = False
    redirect_url: str | None = None


class CameraCatalog:
    """Authoritative client for querying the dynamic camera catalogue."""

    def __init__(
        self,
        catalog_url: str | None = None,
        auth_token: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.catalog_url = catalog_url or os.environ.get(
            "RTSP_CATALOG_URL", "http://live.corp8.cloud/api/ingest"
        )
        self.auth_token = auth_token or os.environ.get("RTSP_CATALOG_TOKEN")
        self.timeout_seconds = timeout_seconds

    def fetch(self, custom_url: str | None = None) -> CatalogFetchResult:
        """
        Fetch and parse camera catalogue from the configured or supplied URL.
        Never throws unhandled exceptions; returns structured result.
        """
        target_url = custom_url or self.catalog_url

        headers = {
            "User-Agent": "Sentinel-Streaming/1.0",
            "Accept": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        req = urllib.request.Request(target_url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                status_code = response.getcode()
                content_type = response.headers.get("Content-Type", "")
                final_url = response.geturl()

                # Check if redirected to a login page
                if "/auth/login" in final_url:
                    logger.warning("Catalogue redirected to login page: %s", final_url)
                    return CatalogFetchResult(
                        success=False,
                        status_code=status_code,
                        requires_auth=True,
                        redirect_url=final_url,
                        error_message="Authentication required (redirected to login page)",
                    )

                body = response.read().decode("utf-8")
                
                # Check for HTML responses returned as 200 OK
                if "text/html" in content_type or body.strip().startswith("<!DOCTYPE") or body.strip().startswith("<html"):
                    return CatalogFetchResult(
                        success=False,
                        status_code=status_code,
                        requires_auth=True,
                        redirect_url=final_url,
                        error_message="Catalogue endpoint returned HTML login/auth page instead of JSON",
                    )

                items = self.parse_catalog_json(body)
                logger.info("Successfully fetched %d cameras from catalogue", len(items))
                return CatalogFetchResult(
                    success=True,
                    status_code=status_code,
                    cameras=items,
                )

        except urllib.error.HTTPError as e:
            requires_auth = e.code in (401, 403)
            logger.warning("HTTP error querying catalogue %s: %s", target_url, e)
            return CatalogFetchResult(
                success=False,
                status_code=e.code,
                requires_auth=requires_auth,
                error_message=f"HTTP {e.code}: {e.reason}",
            )
        except urllib.error.URLError as e:
            logger.warning("Network error querying catalogue %s: %s", target_url, e)
            return CatalogFetchResult(
                success=False,
                error_message=f"Network error: {e.reason}",
            )
        except Exception as e:
            logger.error("Unexpected error fetching catalogue: %s", e)
            return CatalogFetchResult(
                success=False,
                error_message=f"Unexpected error: {str(e)}",
            )

    @staticmethod
    def parse_catalog_json(json_str: str) -> list[CameraCatalogItem]:
        """Parse JSON catalogue string into a list of CameraCatalogItem objects."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse catalogue JSON: %s", e)
            return []

        raw_list: list[dict[str, Any]] = []
        if isinstance(data, list):
            raw_list = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict):
            # Might be wrapped in "cameras", "streams", or "data"
            for key in ("cameras", "streams", "data", "items"):
                if key in data and isinstance(data[key], list):
                    raw_list = [item for item in data[key] if isinstance(item, dict)]
                    break
            if not raw_list:
                # Dict might represent a single camera
                raw_list = [data]

        return [CameraCatalogItem.from_dict(item) for item in raw_list]
