"""
Stream Manager for Sentinel.

Coordinates one or more camera streams on-demand, linking RTSP ingestion,
frame reading with PTS tracking, exponential backoff reconnection, and operational health.
"""

from __future__ import annotations

import logging
from typing import Any

from streaming.frame_reader import FramePacket, FrameReader
from streaming.health import StreamHealth, StreamHealthTracker, StreamStatus
from streaming.pts import PTSTracker
from streaming.reconnect import ReconnectManager
from streaming.rtsp_client import RTSPClient

logger = logging.getLogger("sentinel.streaming.stream_manager")


class CameraStreamSession:
    """
    Manages the lifecycle of an individual camera stream.
    Combines client, reader, reconnect manager, and health tracker.
    """

    def __init__(
        self,
        camera_id: str,
        rtsp_url: str,
        transport: str = "tcp",
        codec_hint: str | None = None,
        initial_backoff: float = 2.0,
        max_backoff: float = 30.0,
    ) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.transport = transport
        self.codec_hint = codec_hint

        self.client = RTSPClient(
            rtsp_url=rtsp_url,
            camera_id=camera_id,
            transport=transport,
            codec_hint=codec_hint,
        )
        self.pts_tracker = PTSTracker()
        self.reader = FrameReader(client=self.client, pts_tracker=self.pts_tracker)
        self.reconnect_mgr = ReconnectManager(
            camera_id=camera_id,
            initial_delay=initial_backoff,
            max_delay=max_backoff,
        )
        self.health_tracker = StreamHealthTracker(camera_id=camera_id)

    @property
    def is_active(self) -> bool:
        return self.client.is_open

    @property
    def health(self) -> StreamHealth:
        return self.health_tracker.current

    def start(self) -> bool:
        """Start the camera stream connection."""
        self.health_tracker.set_connecting()
        opened = self.client.open()

        if opened:
            props = {
                "width": self.client.width,
                "height": self.client.height,
                "fps_hint": self.client.fps_hint,
                "codec": self.client.codec,
            }
            self.health_tracker.set_online(stream_props=props)
            self.reconnect_mgr.record_success()
            return True
        else:
            self.reconnect_mgr.record_failure()
            self.health_tracker.set_reconnecting(self.reconnect_mgr.attempts)
            return False

    def read_packet(self) -> tuple[bool, FramePacket | None]:
        """
        Read next frame packet from stream.
        Handles intermittent read failures, stall checks, and non-blocking reconnect attempts.
        """
        if not self.client.is_open:
            if self.reconnect_mgr.can_attempt_now():
                logger.info("Attempting non-blocking reconnect for camera %s", self.camera_id)
                self.start()
            if not self.client.is_open:
                return False, None

        success, packet = self.reader.read_packet()

        if success and packet is not None:
            self.health_tracker.record_frame(
                frame_number=self.reader.total_frames_read,
                pts_ms=packet.pts_ms,
            )
            return True, packet

        # Read failed
        self.health_tracker.record_read_failure("Empty or failed frame read")

        # If failures exceed threshold, trigger reconnect cycle
        if self.health_tracker.status == StreamStatus.ERROR:
            logger.warning("Camera %s reached failure threshold. Initiating reconnect.", self.camera_id)
            self.client.close()
            self.reconnect_mgr.record_failure()
            self.health_tracker.set_reconnecting(self.reconnect_mgr.attempts)

        return False, None

    def stop(self) -> None:
        """Stop stream and cleanly release all resources."""
        self.client.close()
        self.reader.reset()
        self.reconnect_mgr.record_success()
        self.health_tracker.set_offline(reason="Stopped by user or manager")


class StreamManager:
    """
    Coordinates active camera streams on demand.

    Ensures only requested streams are opened, avoids duplicate connections,
    and releases unused captures cleanly.
    """

    def __init__(self, default_transport: str = "tcp") -> None:
        self.default_transport = default_transport
        self._streams: dict[str, CameraStreamSession] = {}

    @property
    def active_stream_count(self) -> int:
        return sum(1 for s in self._streams.values() if s.is_active)

    def add_camera(
        self,
        camera_id: str,
        rtsp_url: str,
        transport: str | None = None,
        codec_hint: str | None = None,
        auto_start: bool = False,
    ) -> CameraStreamSession:
        """
        Add a camera stream. If already present, returns existing session
        to prevent duplicate captures to the same camera.
        """
        if camera_id in self._streams:
            logger.info("Camera %s already registered in stream manager", camera_id)
            session = self._streams[camera_id]
            if auto_start and not session.is_active:
                session.start()
            return session

        session = CameraStreamSession(
            camera_id=camera_id,
            rtsp_url=rtsp_url,
            transport=transport or self.default_transport,
            codec_hint=codec_hint,
        )
        self._streams[camera_id] = session

        if auto_start:
            session.start()

        return session

    def get_stream(self, camera_id: str) -> CameraStreamSession | None:
        """Retrieve stream session by camera ID."""
        return self._streams.get(camera_id)

    def read_frame(self, camera_id: str) -> tuple[bool, FramePacket | None]:
        """Read frame packet from a specific camera."""
        session = self.get_stream(camera_id)
        if session is None:
            logger.warning("Attempted to read from non-existent camera stream: %s", camera_id)
            return False, None
        return session.read_packet()

    def remove_camera(self, camera_id: str) -> None:
        """Stop and remove a camera stream, releasing capture resources cleanly."""
        session = self._streams.pop(camera_id, None)
        if session:
            logger.info("Removing and releasing camera stream: %s", camera_id)
            session.stop()

    def get_health(self, camera_id: str) -> StreamHealth | None:
        """Get health info for a specific camera."""
        session = self.get_stream(camera_id)
        return session.health if session else None

    def get_all_health(self) -> dict[str, dict[str, Any]]:
        """Get operational health summary for all registered streams."""
        return {cam_id: sess.health.to_dict() for cam_id, sess in self._streams.items()}

    def stop_all(self) -> None:
        """Release and stop all active streams."""
        for cam_id in list(self._streams.keys()):
            self.remove_camera(cam_id)
