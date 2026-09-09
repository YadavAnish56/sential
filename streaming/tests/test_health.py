import pytest
from streaming.health import StreamHealthTracker, StreamStatus


def test_health_initial_state():
    tracker = StreamHealthTracker(camera_id="cam-101")
    assert tracker.status == StreamStatus.OFFLINE
    assert tracker.current.last_seen is None
    assert tracker.current.consecutive_failures == 0


def test_health_state_transitions():
    tracker = StreamHealthTracker(camera_id="cam-101")

    tracker.set_connecting()
    assert tracker.status == StreamStatus.CONNECTING

    tracker.set_online({"width": 1920, "height": 1080})
    assert tracker.status == StreamStatus.ONLINE
    assert tracker.current.properties["width"] == 1920

    tracker.set_reconnecting(attempt=1)
    assert tracker.status == StreamStatus.RECONNECTING
    assert tracker.current.reconnect_attempts == 1

    tracker.set_offline(reason="Maintenance")
    assert tracker.status == StreamStatus.OFFLINE
    assert tracker.current.error_message == "Maintenance"


def test_failure_threshold_prevents_flapping():
    tracker = StreamHealthTracker(camera_id="cam-101", failure_threshold=3)
    tracker.set_online()
    assert tracker.status == StreamStatus.ONLINE

    # 1st failure: must remain ONLINE
    tracker.record_read_failure("dropped frame")
    assert tracker.status == StreamStatus.ONLINE
    assert tracker.current.consecutive_failures == 1

    # 2nd failure: still ONLINE
    tracker.record_read_failure("dropped frame")
    assert tracker.status == StreamStatus.ONLINE
    assert tracker.current.consecutive_failures == 2

    # 3rd failure reaches threshold: degrades to ERROR
    tracker.record_read_failure("stream broken")
    assert tracker.status == StreamStatus.ERROR
    assert tracker.current.consecutive_failures == 3

    # New frame arrives: immediately recovers to ONLINE
    tracker.record_frame(frame_number=10, pts_ms=400.0)
    assert tracker.status == StreamStatus.ONLINE
    assert tracker.current.consecutive_failures == 0
    assert tracker.current.last_frame_number == 10
    assert tracker.current.last_pts_ms == 400.0


def test_check_stale_detection():
    tracker = StreamHealthTracker(camera_id="cam-101", stale_timeout_sec=5.0)
    tracker.set_online()

    # Frame recorded at t=100.0
    tracker.record_frame(frame_number=1, pts_ms=40.0, now=100.0)

    # At t=104.0 (4s later) -> not stale
    assert tracker.check_stale(now=104.0) is False
    assert tracker.status == StreamStatus.ONLINE

    # At t=106.0 (6s later) -> stale!
    assert tracker.check_stale(now=106.0) is True
    assert tracker.status == StreamStatus.ERROR
