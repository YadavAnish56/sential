import math
import pytest
from streaming.pts import PTSTracker, PTSInfo


def test_pts_sequential_processing():
    tracker = PTSTracker(max_normal_interval_ms=150.0)

    # Frame 1 at 0ms
    info1 = tracker.process(0.0)
    assert info1.is_valid is True
    assert info1.pts_ms == 0.0
    assert info1.delta_ms is None
    assert info1.is_jump is False
    assert info1.is_loop_or_reset is False
    assert info1.is_irregular is False

    # Frame 2 at 40ms (25fps)
    info2 = tracker.process(40.0)
    assert info2.is_valid is True
    assert info2.pts_ms == 40.0
    assert info2.delta_ms == pytest.approx(40.0)
    assert info2.is_jump is False
    assert info2.is_irregular is False

    # Frame 3 at 80ms
    info3 = tracker.process(80.0)
    assert info3.is_valid is True
    assert info3.delta_ms == pytest.approx(40.0)
    assert tracker.frame_count == 3


def test_pts_irregular_intervals():
    tracker = PTSTracker(max_normal_interval_ms=100.0)

    tracker.process(0.0)
    # Gap of 250ms -> irregular
    info = tracker.process(250.0)
    assert info.is_valid is True
    assert info.delta_ms == pytest.approx(250.0)
    assert info.is_irregular is True
    assert tracker.irregular_count == 1

    # Zero or negative small interval -> irregular burst
    info_burst = tracker.process(250.0)
    assert info_burst.is_irregular is True
    assert tracker.irregular_count == 2


def test_pts_jump_forward():
    tracker = PTSTracker(pts_jump_threshold_ms=2000.0)

    tracker.process(100.0)
    # Jump by 5000ms -> discontinuity
    info = tracker.process(5100.0)
    assert info.is_valid is True
    assert info.delta_ms == pytest.approx(5000.0)
    assert info.is_jump is True
    assert tracker.jumps_count == 1


def test_pts_loop_reset():
    tracker = PTSTracker(reset_threshold_ms=-500.0)

    tracker.process(10000.0)
    # Stream loops back to 40.0ms (delta = -9960ms)
    info = tracker.process(40.0)
    assert info.is_valid is True
    assert info.delta_ms == pytest.approx(-9960.0)
    assert info.is_loop_or_reset is True
    assert tracker.resets_count == 1


def test_pts_invalid_and_missing():
    tracker = PTSTracker()

    info_none = tracker.process(None)
    assert info_none.is_valid is False
    assert info_none.pts_ms is None

    info_neg = tracker.process(-1.0)
    assert info_neg.is_valid is False

    info_nan = tracker.process(float("nan"))
    assert info_nan.is_valid is False

    info_inf = tracker.process(float("inf"))
    assert info_inf.is_valid is False

    assert tracker.missing_pts_count == 4


def test_pts_reset():
    tracker = PTSTracker()
    tracker.process(100.0)
    tracker.process(200.0)
    assert tracker.frame_count == 2

    tracker.reset()
    assert tracker.frame_count == 0
    assert tracker.last_pts_ms is None
