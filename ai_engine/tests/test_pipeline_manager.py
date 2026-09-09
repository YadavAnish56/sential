"""
Unit and Integration Test Suite for CameraPipelineManager (Phase 7.7).

Validates multi-camera pipeline lifecycle management:
- T1: Single camera start
- T2: Single camera stop
- T3: Duplicate start prevention
- T4: Unknown camera handling
- T5: Multiple camera isolation
- T6: Failure isolation (one camera fails, other continues)
- T7: Stop-one-does-not-stop-another
- T8: Shutdown-all
- T9: Resource cleanup
- T10: Per-camera state isolation (tracker and PTS)
- T11: Repeated start/stop lifecycle
- T12: Concurrent lifecycle operations (thread safety)
- T13: Absence of hidden global mutable state
- T14: Dynamic catalogue loading
"""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streaming.frame_reader import FramePacket
from streaming.health import StreamHealth, StreamStatus
from streaming.camera_catalog import CameraCatalogItem
from ai_engine.schemas import Detection, DetectionResult
from ai_engine.pipeline_manager import (
    CameraPipelineManager,
    CameraPipelineSession,
    PipelineConfig,
    PipelineSessionStatus,
)
from ai_engine.anpr.recognizer import MockPlateRecognizer
from ai_engine.anpr.schemas import PlateCandidate


# ─────────────────────────────────────────────────────────────────────────────
# Test Doubles
# ─────────────────────────────────────────────────────────────────────────────

class MockDetector:
    """Lightweight test double for VehicleDetector."""

    def __init__(
        self,
        detections: list[Detection] | None = None,
        detections_factory: Callable[[FramePacket], list[Detection]] | None = None,
        should_fail: bool = False,
    ) -> None:
        self.detections = detections or []
        self.detections_factory = detections_factory
        self.should_fail = should_fail
        self.call_count = 0

    def detect(self, packet: FramePacket) -> DetectionResult:
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError("MockDetector forced failure")

        if self.detections_factory is not None:
            dets = self.detections_factory(packet)
        elif self.detections:
            # Re-bind detections to current packet camera_id and pts_ms
            dets = [
                Detection(
                    class_id=d.class_id,
                    class_name=d.class_name,
                    confidence=d.confidence,
                    x1=d.x1,
                    y1=d.y1,
                    x2=d.x2,
                    y2=d.y2,
                    camera_id=packet.camera_id,
                    pts_ms=packet.pts_ms,
                )
                for d in self.detections
            ]
        else:
            dets = []

        return DetectionResult(
            camera_id=packet.camera_id,
            pts_ms=packet.pts_ms,
            detections=dets,
            inference_time_ms=1.0,
            device="cpu",
            model_name="mock_model",
        )


class MockCameraStreamSession:
    """Lightweight in-memory test double for CameraStreamSession."""

    def __init__(
        self,
        camera_id: str,
        frames: list[FramePacket] | None = None,
        fail_on_start: bool = False,
        fail_on_read: bool = False,
    ) -> None:
        self.camera_id = camera_id
        self.frames = list(frames or [])
        self.fail_on_start = fail_on_start
        self.fail_on_read = fail_on_read
        self._is_active = False
        self._frames_read = 0
        self.stopped = False
        self._lock = threading.Lock()

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    @property
    def health(self) -> StreamHealth:
        status = StreamStatus.ONLINE if self._is_active else StreamStatus.OFFLINE
        return StreamHealth(
            camera_id=self.camera_id,
            status=status,
            last_frame_number=self._frames_read,
        )

    def start(self) -> bool:
        with self._lock:
            if self.fail_on_start:
                return False
            self._is_active = True
            self.stopped = False
            return True

    def read_packet(self) -> tuple[bool, FramePacket | None]:
        with self._lock:
            if not self._is_active:
                return False, None
            if self.fail_on_read:
                raise RuntimeError(f"Mock read failure for camera {self.camera_id}")
            if not self.frames:
                # Return empty frame on demand
                self._frames_read += 1
                pkt = make_frame_packet(self.camera_id, pts_ms=float(self._frames_read * 33.3))
                return True, pkt
            pkt = self.frames.pop(0)
            self._frames_read += 1
            return True, pkt

    def stop(self) -> None:
        with self._lock:
            self._is_active = False
            self.stopped = True


def make_frame_packet(camera_id: str = "CAM-001", pts_ms: float = 1000.0) -> FramePacket:
    """Create a synthetic FramePacket for testing."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    return FramePacket(
        frame=frame,
        pts_ms=pts_ms,
        received_at=time.time(),
        camera_id=camera_id,
        width=640,
        height=480,
    )


def make_detection(
    camera_id: str = "CAM-001",
    class_name: str = "car",
    bbox: tuple[float, float, float, float] = (100.0, 100.0, 200.0, 200.0),
    pts_ms: float = 100.0,
) -> Detection:
    """Create a synthetic Detection object."""
    x1, y1, x2, y2 = bbox
    return Detection(
        class_id=2,
        class_name=class_name,
        confidence=0.90,
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        camera_id=camera_id,
        pts_ms=pts_ms,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: T1 - T14
# ─────────────────────────────────────────────────────────────────────────────

def test_t1_single_camera_start():
    """T1: Verify single camera transitions from STOPPED to RUNNING."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)
    stream = MockCameraStreamSession(camera_id="CAM-001")

    session = manager.register_camera(
        config=PipelineConfig(camera_id="CAM-001"),
        stream_session=stream,
    )
    assert session.status == PipelineSessionStatus.STOPPED
    assert not session.is_running

    started = manager.start_camera("CAM-001")
    assert started is True
    assert session.status == PipelineSessionStatus.RUNNING
    assert session.is_running is True
    assert stream.is_active is True
    assert manager.get_running_camera_ids() == ["CAM-001"]

    manager.stop_all()


def test_t2_single_camera_stop():
    """T2: Verify single camera transitions from RUNNING to STOPPED and releases stream."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)
    stream = MockCameraStreamSession(camera_id="CAM-001")

    manager.register_camera(
        config=PipelineConfig(camera_id="CAM-001"),
        stream_session=stream,
        auto_start=True,
    )
    assert manager.get_running_camera_ids() == ["CAM-001"]

    stopped = manager.stop_camera("CAM-001")
    assert stopped is True
    session = manager.get_session("CAM-001")
    assert session.status == PipelineSessionStatus.STOPPED
    assert not session.is_running
    assert stream.is_active is False
    assert stream.stopped is True
    assert manager.get_running_camera_ids() == []


def test_t3_duplicate_start_prevention():
    """T3: Verify attempting to start an already running camera returns False without duplication."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)
    stream = MockCameraStreamSession(camera_id="CAM-001")

    manager.register_camera(
        config=PipelineConfig(camera_id="CAM-001"),
        stream_session=stream,
    )
    assert manager.start_camera("CAM-001") is True

    # Duplicate start
    duplicate_start = manager.start_camera("CAM-001")
    assert duplicate_start is False
    assert manager.running_count == 1
    assert manager.get_running_camera_ids() == ["CAM-001"]

    manager.stop_all()


def test_t4_unknown_camera_handling():
    """T4: Verify operations on unknown camera IDs return graceful failure status."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)

    assert manager.start_camera("UNKNOWN-CAM") is False
    assert manager.stop_camera("UNKNOWN-CAM") is False
    assert manager.unregister_camera("UNKNOWN-CAM") is False
    assert manager.get_session("UNKNOWN-CAM") is None
    assert manager.get_health("UNKNOWN-CAM") is None


def test_t5_multiple_camera_isolation():
    """T5: Verify multiple cameras run independently without crosstalk."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)

    session_a = manager.register_camera(PipelineConfig(camera_id="CAM-A"))
    session_b = manager.register_camera(PipelineConfig(camera_id="CAM-B"))

    assert sorted(manager.get_registered_camera_ids()) == ["CAM-A", "CAM-B"]

    # Process 3 frames through CAM-A and 1 frame through CAM-B synchronously
    for i in range(3):
        session_a.process_one_frame(make_frame_packet("CAM-A", pts_ms=float(i * 100)))
    session_b.process_one_frame(make_frame_packet("CAM-B", pts_ms=500.0))

    assert session_a.frames_processed == 3
    assert session_b.frames_processed == 1
    assert session_a.last_processed_pts_ms == 200.0
    assert session_b.last_processed_pts_ms == 500.0


def test_t6_failure_isolation():
    """T6: Verify failure in one camera does NOT halt or contaminate other cameras."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)

    # CAM-FAIL will fail on read; CAM-OK operates normally
    stream_fail = MockCameraStreamSession(camera_id="CAM-FAIL", fail_on_read=True)
    stream_ok = MockCameraStreamSession(camera_id="CAM-OK")

    session_fail = manager.register_camera(
        PipelineConfig(camera_id="CAM-FAIL", max_frames=5),
        stream_session=stream_fail,
    )
    session_ok = manager.register_camera(
        PipelineConfig(camera_id="CAM-OK", max_frames=10),
        stream_session=stream_ok,
    )

    manager.start_all()

    # Allow background threads to execute
    time.sleep(0.1)

    # CAM-FAIL should enter ERROR status due to forced read exception
    assert session_fail.status == PipelineSessionStatus.ERROR
    assert "Mock read failure" in (session_fail.error_message or "")

    # CAM-OK should remain RUNNING or cleanly continue processing
    assert session_ok.status in (PipelineSessionStatus.RUNNING, PipelineSessionStatus.STOPPED)
    assert session_ok.error_message is None

    manager.stop_all()


def test_t7_stop_one_does_not_stop_another():
    """T7: Verify stopping one camera leaves other running cameras unaffected."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)

    stream_a = MockCameraStreamSession(camera_id="CAM-A")
    stream_b = MockCameraStreamSession(camera_id="CAM-B")

    manager.register_camera(PipelineConfig(camera_id="CAM-A"), stream_session=stream_a, auto_start=True)
    manager.register_camera(PipelineConfig(camera_id="CAM-B"), stream_session=stream_b, auto_start=True)

    assert sorted(manager.get_running_camera_ids()) == ["CAM-A", "CAM-B"]

    # Stop CAM-A only
    manager.stop_camera("CAM-A")

    assert manager.get_session("CAM-A").status == PipelineSessionStatus.STOPPED
    assert manager.get_session("CAM-B").status == PipelineSessionStatus.RUNNING
    assert manager.get_running_camera_ids() == ["CAM-B"]

    manager.stop_all()


def test_t8_shutdown_all():
    """T8: Verify stop_all cleanly shuts down all active camera pipelines."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)

    for i in range(1, 4):
        cam_id = f"CAM-00{i}"
        manager.register_camera(
            PipelineConfig(camera_id=cam_id),
            stream_session=MockCameraStreamSession(camera_id=cam_id),
            auto_start=True,
        )

    assert len(manager.get_running_camera_ids()) == 3

    results = manager.stop_all()
    assert all(results.values())
    assert manager.get_running_camera_ids() == []
    assert manager.running_count == 0


def test_t9_resource_cleanup():
    """T9: Verify unregister_camera cleanly releases streams, threads, and internal state."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)
    stream = MockCameraStreamSession(camera_id="CAM-CLEAN")

    manager.register_camera(
        PipelineConfig(camera_id="CAM-CLEAN"),
        stream_session=stream,
        auto_start=True,
    )
    assert manager.get_session("CAM-CLEAN") is not None

    unregistered = manager.unregister_camera("CAM-CLEAN")
    assert unregistered is True
    assert stream.is_active is False
    assert stream.stopped is True
    assert manager.get_session("CAM-CLEAN") is None
    assert "CAM-CLEAN" not in manager.get_registered_camera_ids()


def test_t10_per_camera_state_isolation():
    """T10: Verify monotonic tracker IDs and PTS intervals remain strictly isolated."""
    det = make_detection(camera_id="CAM-A", class_name="car", bbox=(10.0, 10.0, 50.0, 50.0))
    detector = MockDetector(detections=[det])
    manager = CameraPipelineManager(detector=detector)

    session_a = manager.register_camera(PipelineConfig(camera_id="CAM-A"))
    session_b = manager.register_camera(PipelineConfig(camera_id="CAM-B"))

    # Feed frames to CAM-A
    res_a1 = session_a.process_one_frame(make_frame_packet("CAM-A", pts_ms=100.0))
    res_a2 = session_a.process_one_frame(make_frame_packet("CAM-A", pts_ms=200.0))

    # Feed frames to CAM-B
    res_b1 = session_b.process_one_frame(make_frame_packet("CAM-B", pts_ms=100.0))

    # Each camera's internal tracker must have created its own track starting at ID 1
    assert res_a1.tracking_result is not None
    assert res_b1.tracking_result is not None
    track_a_ids = [t.track_id for t in res_a1.tracking_result.active_tracks]
    track_b_ids = [t.track_id for t in res_b1.tracking_result.active_tracks]

    assert track_a_ids == [1]
    assert track_b_ids == [1]  # CAM-B gets its own track ID 1, not 2!

    # Telemetry isolation
    stats_a = session_a.pipeline.get_stats()
    stats_b = session_b.pipeline.get_stats()
    assert stats_a["frames_total"] == 2
    assert stats_b["frames_total"] == 1


def test_t11_repeated_start_stop_lifecycle():
    """T11: Verify repeated start/stop cycles operate deterministically without leakage."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)
    stream = MockCameraStreamSession(camera_id="CAM-CYCLE")

    manager.register_camera(PipelineConfig(camera_id="CAM-CYCLE"), stream_session=stream)

    for cycle in range(3):
        assert manager.start_camera("CAM-CYCLE") is True
        assert manager.get_session("CAM-CYCLE").status == PipelineSessionStatus.RUNNING
        assert manager.stop_camera("CAM-CYCLE") is True
        assert manager.get_session("CAM-CYCLE").status == PipelineSessionStatus.STOPPED

    assert manager.running_count == 0


def test_t12_concurrent_lifecycle_operations():
    """T12: Verify concurrent start and stop calls are thread-safe and cause no deadlocks."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)

    # Register 10 cameras
    for i in range(10):
        cam_id = f"CAM-{i:02d}"
        stream = MockCameraStreamSession(camera_id=cam_id)
        manager.register_camera(PipelineConfig(camera_id=cam_id), stream_session=stream)

    def worker_action(cam_id: str, action: str):
        if action == "start":
            manager.start_camera(cam_id)
        elif action == "stop":
            manager.stop_camera(cam_id)
        elif action == "health":
            manager.get_health(cam_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for i in range(10):
            cam_id = f"CAM-{i:02d}"
            futures.append(pool.submit(worker_action, cam_id, "start"))
            futures.append(pool.submit(worker_action, cam_id, "health"))
            futures.append(pool.submit(worker_action, cam_id, "stop"))

        for f in futures:
            f.result()

    # Clean up any remaining running cameras
    manager.stop_all()
    assert manager.running_count == 0


def test_t13_no_hidden_global_mutable_state():
    """T13: Verify multiple manager instances do not share state or cross-contaminate."""
    detector = MockDetector()
    manager1 = CameraPipelineManager(detector=detector)
    manager2 = CameraPipelineManager(detector=detector)

    stream1 = MockCameraStreamSession(camera_id="CAM-001")
    manager1.register_camera(PipelineConfig(camera_id="CAM-001"), stream_session=stream1, auto_start=True)

    assert manager1.get_registered_camera_ids() == ["CAM-001"]
    assert manager1.get_running_camera_ids() == ["CAM-001"]

    # Manager 2 must be completely empty
    assert manager2.get_registered_camera_ids() == []
    assert manager2.get_running_camera_ids() == []
    assert manager2.get_session("CAM-001") is None

    manager1.stop_all()


def test_t14_dynamic_catalogue_loading():
    """T14: Verify dynamic population from CameraCatalogItem records without hardcoding."""
    detector = MockDetector()
    manager = CameraPipelineManager(detector=detector)

    catalog_items = [
        CameraCatalogItem(
            camera_id="CAT-CAM-101",
            name="North Gate",
            rtsp_url="rtsp://mock-host:8554/gate1",
            fps_hint=25.0,
        ),
        CameraCatalogItem(
            camera_id="CAT-CAM-102",
            name="South Gate",
            rtsp_url="rtsp://mock-host:8554/gate2",
            fps_hint=30.0,
        ),
    ]

    registered = manager.load_from_catalog(catalog_items, auto_start=False)
    assert registered == ["CAT-CAM-101", "CAT-CAM-102"]
    assert manager.registered_count == 2

    sess_101 = manager.get_session("CAT-CAM-101")
    sess_102 = manager.get_session("CAT-CAM-102")
    assert sess_101 is not None and sess_101.config.target_fps == 25.0
    assert sess_102 is not None and sess_102.config.target_fps == 30.0
