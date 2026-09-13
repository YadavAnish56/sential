"""
Multi-Camera Pipeline Lifecycle Manager for Sentinel AI Engine (Phase 7.7).

Coordinates the concurrent lifecycle of multiple independent CameraPipeline sessions:
- Per-camera runtime state isolation (StreamProcessor, VehicleTracker, ANPRCoordinator, PTS)
- Dynamic registration via PipelineConfig or CameraCatalogItem without hardcoding
- Safe start, stop, pause, and shutdown operations with resource release
- Strict failure isolation: one camera's disconnection or exception never impacts others
- Zero global mutable state
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from streaming.frame_reader import FramePacket
from streaming.stream_manager import CameraStreamSession

try:
    from .pipeline import CameraPipeline, PipelineResult
    from .stream_processor import StreamProcessor, StreamProcessorConfig
    from .tracking import VehicleTracker, TrackerConfig
    from .anpr import ANPRCoordinator, ANPRConfig, BasePlateRecognizer
    from .persistence_dispatcher import PersistenceDispatcher
except ImportError:
    from ai_engine.pipeline import CameraPipeline, PipelineResult
    from ai_engine.stream_processor import StreamProcessor, StreamProcessorConfig
    from ai_engine.tracking import VehicleTracker, TrackerConfig
    from ai_engine.anpr import ANPRCoordinator, ANPRConfig, BasePlateRecognizer
    from ai_engine.persistence_dispatcher import PersistenceDispatcher

try:
    from streaming.camera_catalog import CameraCatalogItem
except ImportError:
    CameraCatalogItem = Any

logger = logging.getLogger("sentinel.ai_engine.pipeline_manager")


class PipelineSessionStatus(str, Enum):
    """Lifecycle state of a camera pipeline session."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class PipelineConfig:
    """
    Configuration parameters for an individual camera pipeline session.

    Attributes:
        camera_id: Unique camera identifier (e.g. CAM-001).
        rtsp_url: Optional RTSP streaming URL for real ingestion.
        transport: Transport protocol ("tcp" or "udp").
        frame_stride: Process 1 of every N frames (default: 1).
        target_fps: Optional PTS-based target sampling rate.
        tracker_config: Optional custom TrackerConfig for vehicle tracking.
        anpr_config: Optional custom ANPRConfig for license plate recognition.
        enable_anpr: If False, ANPR stage is bypassed.
        max_frames: Optional frame limit for bounded execution/testing.
        poll_interval_sec: Sleep interval when stream is idle or waiting for frames.
        watchlist: Optional watchlist of license plates for alert dispatching.
    """
    camera_id: str
    rtsp_url: str | None = None
    transport: str = "tcp"
    frame_stride: int = 1
    target_fps: float | None = None
    tracker_config: TrackerConfig | None = None
    anpr_config: ANPRConfig | None = None
    enable_anpr: bool = True
    max_frames: int | None = None
    poll_interval_sec: float = 0.005
    watchlist: list[str] | None = None


class CameraPipelineSession:
    """
    Encapsulates the complete runtime state and execution lifecycle for
    exactly one camera pipeline.

    Combines:
    - Ingestion: CameraStreamSession (or custom frame reader)
    - AI Pipeline: StreamProcessor -> VehicleTracker -> ANPRCoordinator -> CameraPipeline
    - Optional Persistence: PersistenceDispatcher
    - Thread Runner: Background daemon worker thread with clean stopping semantics
    - Health & Telemetry: Status, frames processed, error recording
    """

    def __init__(
        self,
        config: PipelineConfig,
        pipeline: CameraPipeline,
        stream_session: CameraStreamSession | None = None,
        dispatcher: PersistenceDispatcher | None = None,
    ) -> None:
        self.camera_id = config.camera_id
        self.config = config
        self.pipeline = pipeline
        self.stream_session = stream_session
        self.dispatcher = dispatcher

        self._status = PipelineSessionStatus.STOPPED
        self._error_message: str | None = None
        self._frames_processed = 0
        self._last_processed_pts_ms: float | None = None

        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @property
    def status(self) -> PipelineSessionStatus:
        with self._lock:
            return self._status

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._status == PipelineSessionStatus.RUNNING

    @property
    def frames_processed(self) -> int:
        with self._lock:
            return self._frames_processed

    @property
    def last_processed_pts_ms(self) -> float | None:
        with self._lock:
            return self._last_processed_pts_ms

    @property
    def error_message(self) -> str | None:
        with self._lock:
            return self._error_message

    def start(self) -> bool:
        """
        Start the pipeline session.

        If a stream_session is present, opens the stream and starts the background
        processing thread. Returns True on successful start, False if already running
        or if connection failed.
        """
        with self._lock:
            if self._status in (PipelineSessionStatus.RUNNING, PipelineSessionStatus.STARTING):
                logger.warning("Pipeline session for camera %s is already %s", self.camera_id, self._status.value)
                return False

            self._status = PipelineSessionStatus.STARTING
            self._error_message = None
            self._stop_event.clear()

            # If an RTSP stream session is attached, start it
            if self.stream_session is not None and not self.stream_session.is_active:
                stream_ok = self.stream_session.start()
                if not stream_ok:
                    self._status = PipelineSessionStatus.ERROR
                    self._error_message = f"Failed to start stream session for {self.camera_id}"
                    logger.error("Camera %s: %s", self.camera_id, self._error_message)
                    return False

            # Spawn background execution thread if stream session exists
            if self.stream_session is not None:
                self._thread = threading.Thread(
                    target=self._run_loop,
                    name=f"pipeline-{self.camera_id}",
                    daemon=True,
                )
                self._thread.start()

            self._status = PipelineSessionStatus.RUNNING
            logger.info("Pipeline session started for camera %s", self.camera_id)
            return True

    def stop(self, timeout_sec: float = 5.0) -> bool:
        """
        Stop the pipeline session and cleanly release all underlying resources.

        Signals the worker thread to stop, joins the thread, and closes the stream.
        """
        with self._lock:
            if self._status == PipelineSessionStatus.STOPPED:
                return True

            self._status = PipelineSessionStatus.STOPPING
            self._stop_event.set()

        # Join the thread outside the lock to avoid deadlock
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout_sec)
            if self._thread.is_alive():
                logger.warning("Worker thread for camera %s did not terminate within %0.1fs", self.camera_id, timeout_sec)

        with self._lock:
            if self.stream_session is not None:
                try:
                    self.stream_session.stop()
                except Exception as exc:
                    logger.error("Error stopping stream session for camera %s: %s", self.camera_id, exc)

            self._status = PipelineSessionStatus.STOPPED
            self._thread = None

            # Clear telemetry so a stopped pipeline reports nothing rather than
            # its final reading; leaving the counters up made the dashboards
            # look live long after the AI had been stopped.
            self._frames_processed = 0
            self._last_processed_pts_ms = None
            try:
                self.pipeline.reset_stats()
            except Exception as exc:  # never let telemetry cleanup fail a stop
                logger.warning("Could not reset stats for camera %s: %s", self.camera_id, exc)

            logger.info("Pipeline session stopped for camera %s", self.camera_id)
            return True

    def process_one_frame(self, packet: FramePacket) -> PipelineResult:
        """
        Synchronously process a single FramePacket through the pipeline.

        Safe to invoke directly from testing harnesses, custom frame readers,
        or the background thread.
        """
        result = self.pipeline.process_frame(packet)

        with self._lock:
            self._frames_processed += 1
            self._last_processed_pts_ms = packet.pts_ms

        # Dispatch recognized plates to persistence dispatcher if configured
        if self.dispatcher is not None and result.recognized_plates:
            try:
                self.dispatcher.dispatch(
                    pipeline_result=result,
                    camera_code=self.camera_id,
                    watchlist=self.config.watchlist,
                )
            except Exception as exc:
                logger.error("Dispatch error for camera %s: %s", self.camera_id, exc)

        return result

    def _run_loop(self) -> None:
        """Internal background loop reading and processing frames continuously."""
        logger.debug("Background processing loop started for camera %s", self.camera_id)
        read_failures = 0
        max_read_failures = 50

        try:
            while not self._stop_event.is_set():
                if self.config.max_frames is not None and self.frames_processed >= self.config.max_frames:
                    logger.info("Camera %s reached max frame limit (%d)", self.camera_id, self.config.max_frames)
                    break

                if self.stream_session is None:
                    time.sleep(self.config.poll_interval_sec)
                    continue

                success, packet = self.stream_session.read_packet()
                if not success or packet is None:
                    read_failures += 1
                    if read_failures > max_read_failures:
                        logger.warning("Camera %s encountered %d consecutive read failures", self.camera_id, read_failures)
                    time.sleep(self.config.poll_interval_sec)
                    continue

                read_failures = 0
                self.process_one_frame(packet)

        except Exception as exc:
            with self._lock:
                self._status = PipelineSessionStatus.ERROR
                self._error_message = f"Unhandled exception in pipeline loop: {exc}"
            logger.error("Fatal error in pipeline loop for camera %s: %s", self.camera_id, exc, exc_info=True)
        finally:
            with self._lock:
                if self._status != PipelineSessionStatus.ERROR:
                    self._status = PipelineSessionStatus.STOPPED
            logger.debug("Background loop terminated for camera %s", self.camera_id)

    def get_health(self) -> dict[str, Any]:
        """Return operational health and telemetry for this camera pipeline session."""
        with self._lock:
            stats = self.pipeline.get_stats()
            stream_health = self.stream_session.health.to_dict() if self.stream_session else None
            return {
                "camera_id": self.camera_id,
                "status": self._status.value,
                "is_running": self._status == PipelineSessionStatus.RUNNING,
                "frames_processed": self._frames_processed,
                "last_processed_pts_ms": self._last_processed_pts_ms,
                "error_message": self._error_message,
                "pipeline_stats": stats,
                "stream_health": stream_health,
            }


class CameraPipelineManager:
    """
    Production-quality lifecycle manager for multi-camera Sentinel pipelines.

    Capabilities:
    - Thread-safe registration and deregistration of camera pipelines.
    - Independent per-camera start and stop without cross-camera impact.
    - Automatic prevention of duplicate sessions.
    - Failure isolation: exceptions or disconnects in one camera do not halt others.
    - Clean resource disposal of stream readers and processing threads.
    - Dynamic population from CameraCatalogItem records.
    - Zero global mutable state: each manager instance is self-contained.
    """

    def __init__(
        self,
        detector: Any | None = None,
        detector_factory: Callable[[], Any] | None = None,
        plate_recognizer: BasePlateRecognizer | None = None,
        plate_recognizer_factory: Callable[[str], BasePlateRecognizer] | None = None,
        dispatcher: PersistenceDispatcher | None = None,
        default_transport: str = "tcp",
    ) -> None:
        """
        Initialize a new CameraPipelineManager.

        Parameters:
            detector: Pre-instantiated shared VehicleDetector (e.g. for GPU reuse).
            detector_factory: Factory function to create a new VehicleDetector per camera.
            plate_recognizer: Pre-instantiated shared or mock plate recognizer.
            plate_recognizer_factory: Factory function to create a plate recognizer per camera.
            dispatcher: Optional shared PersistenceDispatcher for database persistence.
            default_transport: Default RTSP transport ("tcp" or "udp").
        """
        self.detector = detector
        self.detector_factory = detector_factory
        self.plate_recognizer = plate_recognizer
        self.plate_recognizer_factory = plate_recognizer_factory
        self.dispatcher = dispatcher
        self.default_transport = default_transport

        self._lock = threading.RLock()
        self._sessions: dict[str, CameraPipelineSession] = {}

    @property
    def registered_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    @property
    def running_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.is_running)

    def register_camera(
        self,
        config: PipelineConfig,
        stream_session: CameraStreamSession | None = None,
        auto_start: bool = False,
    ) -> CameraPipelineSession:
        """
        Register a new camera pipeline.

        Prevents duplicate registrations by returning the existing session if already
        registered. If auto_start is True and the session is not running, starts it.
        """
        with self._lock:
            if config.camera_id in self._sessions:
                logger.info("Camera %s is already registered in pipeline manager", config.camera_id)
                session = self._sessions[config.camera_id]
                if auto_start and not session.is_running:
                    session.start()
                return session

            # Resolve detector for this camera
            cam_detector = None
            if self.detector_factory is not None:
                cam_detector = self.detector_factory()
            elif self.detector is not None:
                cam_detector = self.detector
            else:
                raise ValueError(
                    f"Cannot register camera {config.camera_id}: No detector or detector_factory provided"
                )

            # Resolve plate recognizer
            cam_recognizer = None
            if self.plate_recognizer_factory is not None:
                cam_recognizer = self.plate_recognizer_factory(config.camera_id)
            elif self.plate_recognizer is not None:
                cam_recognizer = self.plate_recognizer

            # Build isolated components
            stream_processor = StreamProcessor(
                detector=cam_detector,
                config=StreamProcessorConfig(
                    frame_stride=config.frame_stride,
                    target_fps=config.target_fps,
                ),
            )
            vehicle_tracker = VehicleTracker(config=config.tracker_config or TrackerConfig())

            anpr_coord = None
            if config.enable_anpr and cam_recognizer is not None:
                anpr_coord = ANPRCoordinator(
                    recognizer=cam_recognizer,
                    config=config.anpr_config or ANPRConfig(),
                )

            pipeline = CameraPipeline(
                camera_id=config.camera_id,
                stream_processor=stream_processor,
                vehicle_tracker=vehicle_tracker,
                anpr_coordinator=anpr_coord,
            )

            # Build RTSP stream session if URL is given and stream_session not provided
            if stream_session is None and config.rtsp_url is not None:
                stream_session = CameraStreamSession(
                    camera_id=config.camera_id,
                    rtsp_url=config.rtsp_url,
                    transport=config.transport or self.default_transport,
                )

            session = CameraPipelineSession(
                config=config,
                pipeline=pipeline,
                stream_session=stream_session,
                dispatcher=self.dispatcher,
            )

            self._sessions[config.camera_id] = session
            logger.info("Registered camera pipeline for %s", config.camera_id)

            if auto_start:
                session.start()

            return session

    def start_camera(self, camera_id: str) -> bool:
        """
        Start the pipeline for the specified camera.

        Returns True on successful transition to RUNNING, False if camera is unknown
        or already running.
        """
        with self._lock:
            session = self._sessions.get(camera_id)
            if session is None:
                logger.warning("Cannot start unknown camera: %s", camera_id)
                return False
            return session.start()

    def stop_camera(self, camera_id: str, timeout_sec: float = 5.0) -> bool:
        """
        Stop the pipeline for the specified camera.

        Returns True if stopped successfully, False if camera is unknown.
        """
        session = None
        with self._lock:
            session = self._sessions.get(camera_id)
            if session is None:
                logger.warning("Cannot stop unknown camera: %s", camera_id)
                return False

        return session.stop(timeout_sec=timeout_sec)

    def unregister_camera(self, camera_id: str, timeout_sec: float = 5.0) -> bool:
        """
        Stop and remove the specified camera pipeline session.
        """
        session = None
        with self._lock:
            session = self._sessions.pop(camera_id, None)

        if session is not None:
            session.stop(timeout_sec=timeout_sec)
            logger.info("Unregistered camera pipeline: %s", camera_id)
            return True
        return False

    def start_all(self, camera_ids: list[str] | None = None) -> dict[str, bool]:
        """
        Start all registered cameras (or the specified subset).

        Failure to start one camera never prevents other cameras from starting.
        Returns a dictionary mapping camera_id to its start outcome.
        """
        with self._lock:
            targets = camera_ids if camera_ids is not None else list(self._sessions.keys())

        results: dict[str, bool] = {}
        for cam_id in targets:
            try:
                results[cam_id] = self.start_camera(cam_id)
            except Exception as exc:
                logger.error("Exception starting camera %s in start_all: %s", cam_id, exc)
                results[cam_id] = False

        return results

    def stop_all(self, timeout_sec: float = 5.0) -> dict[str, bool]:
        """
        Gracefully stop all currently running camera pipelines.

        Returns a dictionary mapping camera_id to its stop outcome.
        """
        with self._lock:
            active_ids = list(self._sessions.keys())

        results: dict[str, bool] = {}
        for cam_id in active_ids:
            try:
                results[cam_id] = self.stop_camera(cam_id, timeout_sec=timeout_sec)
            except Exception as exc:
                logger.error("Exception stopping camera %s in stop_all: %s", cam_id, exc)
                results[cam_id] = False

        return results

    def get_running_camera_ids(self) -> list[str]:
        """Return a list of IDs of all currently running camera pipelines."""
        with self._lock:
            return [cam_id for cam_id, s in self._sessions.items() if s.is_running]

    def get_registered_camera_ids(self) -> list[str]:
        """Return a list of IDs of all registered camera pipelines."""
        with self._lock:
            return list(self._sessions.keys())

    def get_session(self, camera_id: str) -> CameraPipelineSession | None:
        """Retrieve the pipeline session for a specific camera ID."""
        with self._lock:
            return self._sessions.get(camera_id)

    def get_health(self, camera_id: str) -> dict[str, Any] | None:
        """Retrieve health and telemetry dictionary for a single camera."""
        with self._lock:
            session = self._sessions.get(camera_id)
            return session.get_health() if session is not None else None

    def get_all_health(self) -> dict[str, dict[str, Any]]:
        """Retrieve health and telemetry dictionary for all registered cameras."""
        with self._lock:
            return {cam_id: s.get_health() for cam_id, s in self._sessions.items()}

    def load_from_catalog(
        self,
        catalog_items: list[CameraCatalogItem],
        auto_start: bool = False,
        config_overrides: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        Dynamically register camera pipelines from catalogue items.

        Parameters:
            catalog_items: List of CameraCatalogItem objects from CameraCatalog.fetch().
            auto_start: If True, starts each registered pipeline.
            config_overrides: Optional mapping of attributes to override on PipelineConfig.

        Returns:
            List of successfully registered camera IDs.
        """
        overrides = config_overrides or {}
        registered: list[str] = []

        for item in catalog_items:
            cam_id = item.camera_id
            if not cam_id:
                continue

            config = PipelineConfig(
                camera_id=cam_id,
                rtsp_url=item.rtsp_url,
                transport=overrides.get("transport", self.default_transport),
                frame_stride=overrides.get("frame_stride", 1),
                target_fps=overrides.get("target_fps", item.fps_hint),
                enable_anpr=overrides.get("enable_anpr", True),
                watchlist=overrides.get("watchlist"),
            )

            try:
                self.register_camera(config=config, auto_start=auto_start)
                registered.append(cam_id)
            except Exception as exc:
                logger.error("Failed to register catalog camera %s: %s", cam_id, exc)

        return registered
