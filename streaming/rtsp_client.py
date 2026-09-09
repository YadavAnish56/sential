"""
RTSP Ingestion Client for Sentinel.

Encapsulates low-level RTSP stream capture, strictly enforces TCP transport,
probes stream metadata (dimensions, codec, fourcc), and reads raw frames with
source PTS (CAP_PROP_POS_MSEC).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("sentinel.streaming.rtsp_client")


def _fourcc_to_string(fourcc_int: float | int) -> str | None:
    """Convert OpenCV fourcc integer property to a readable string."""
    try:
        val = int(fourcc_int)
        if val <= 0:
            return None
        chars = [chr((val >> (8 * i)) & 0xFF) for i in range(4)]
        codec_str = "".join(chars).strip()
        # Clean printable ASCII only
        if all(32 <= ord(c) <= 126 for c in codec_str):
            return codec_str
        return None
    except Exception:
        return None


class RTSPClient:
    """
    RTSP stream consumer encapsulating OpenCV FFmpeg backend with forced TCP.
    """

    def __init__(
        self,
        rtsp_url: str,
        camera_id: str = "unknown",
        transport: str = "tcp",
        codec_hint: str | None = None,
    ) -> None:
        self.rtsp_url = rtsp_url
        self.camera_id = camera_id
        self.transport = transport.lower()
        self.codec_hint = codec_hint

        self._cap: Any = None
        self._is_open: bool = False
        self._width: int | None = None
        self._height: int | None = None
        self._fps_hint: float | None = None
        self._codec: str | None = codec_hint

    @property
    def is_open(self) -> bool:
        return self._is_open and self._cap is not None and self._cap.isOpened()

    @property
    def width(self) -> int | None:
        return self._width

    @property
    def height(self) -> int | None:
        return self._height

    @property
    def fps_hint(self) -> float | None:
        return self._fps_hint

    @property
    def codec(self) -> str | None:
        return self._codec

    def open(self) -> bool:
        """
        Open the RTSP stream forcing TCP transport.
        Returns True if opened successfully, False otherwise.
        """
        self.close()

        # Enforce TCP transport via OpenCV FFmpeg options
        # Note: Never use UDP for reliable video ingestion
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{self.transport}"

        try:
            import cv2
        except ImportError as exc:
            logger.error("opencv-python is required for RTSPClient: %s", exc)
            return False

        safe_url = self._mask_credentials(self.rtsp_url)
        logger.info(
            "Connecting to camera %s at %s (transport=%s)",
            self.camera_id, safe_url, self.transport
        )

        try:
            self._cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            if not self._cap.isOpened():
                logger.warning(
                    "Failed to open RTSP stream for camera %s (%s)",
                    self.camera_id, safe_url
                )
                self.close()
                return False

            self._is_open = True

            # Inspect stream properties (dimensions, advisory FPS, fourcc)
            raw_w = self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            raw_h = self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            raw_fps = self._cap.get(cv2.CAP_PROP_FPS)
            raw_fourcc = self._cap.get(cv2.CAP_PROP_FOURCC)

            if raw_w and raw_w > 0:
                self._width = int(raw_w)
            if raw_h and raw_h > 0:
                self._height = int(raw_h)
            if raw_fps and raw_fps > 0:
                self._fps_hint = float(raw_fps)

            detected_fourcc = _fourcc_to_string(raw_fourcc)
            if detected_fourcc:
                self._codec = detected_fourcc
            elif self.codec_hint:
                self._codec = self.codec_hint

            logger.info(
                "Connected to camera %s: %sx%s, advisory_fps=%.1f, codec=%s",
                self.camera_id,
                self._width or "unknown",
                self._height or "unknown",
                self._fps_hint or 0.0,
                self._codec or "unknown",
            )
            return True

        except Exception as e:
            logger.error(
                "Exception opening RTSP stream for camera %s: %s",
                self.camera_id, e
            )
            self.close()
            return False

    def read_frame(self) -> tuple[bool, Any, float | None]:
        """
        Read a single frame from the capture stream.

        Returns:
            tuple of (success: bool, frame: np.ndarray | None, pts_ms: float | None)
        """
        if not self.is_open:
            return False, None, None

        try:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                return False, None, None

            # Retrieve PTS from backend in milliseconds
            import cv2
            raw_pts = self._cap.get(cv2.CAP_PROP_POS_MSEC)
            pts_ms = float(raw_pts) if (raw_pts is not None and raw_pts >= 0) else None

            # Lazily update dimensions from actual frame if not provided by backend metadata
            if self._width is None or self._height is None:
                if hasattr(frame, "shape") and len(frame.shape) >= 2:
                    self._height, self._width = frame.shape[0], frame.shape[1]

            return True, frame, pts_ms

        except Exception as e:
            logger.error("Error reading frame from camera %s: %s", self.camera_id, e)
            return False, None, None

    def close(self) -> None:
        """Release capture and free resources cleanly."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception as e:
                logger.debug("Error releasing VideoCapture for camera %s: %s", self.camera_id, e)
            finally:
                self._cap = None
        self._is_open = False

    def __enter__(self) -> RTSPClient:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @staticmethod
    def _mask_credentials(url: str) -> str:
        """Mask user credentials in RTSP URL for safe logging."""
        if "@" in url and "://" in url:
            prefix, rest = url.split("://", 1)
            auth, host_path = rest.split("@", 1)
            return f"{prefix}://***:***@{host_path}"
        return url
