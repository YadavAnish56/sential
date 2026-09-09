import pytest
from streaming.reconnect import ReconnectManager


def test_reconnect_backoff_progression():
    mgr = ReconnectManager(camera_id="cam-1", initial_delay=2.0, max_delay=30.0, backoff_factor=2.0)

    # Sequence: 2, 4, 8, 16, 30, 30...
    expected_delays = [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
    for attempt, expected in enumerate(expected_delays):
        assert mgr.calculate_delay(attempt) == pytest.approx(expected)


def test_record_failure_and_success():
    mgr = ReconnectManager(camera_id="cam-1", initial_delay=2.0, max_delay=30.0)

    # 1st failure
    delay1 = mgr.record_failure(now=100.0)
    assert delay1 == pytest.approx(2.0)
    assert mgr.attempts == 1
    assert mgr.is_reconnecting is True

    # 2nd failure
    delay2 = mgr.record_failure(now=102.0)
    assert delay2 == pytest.approx(4.0)
    assert mgr.attempts == 2

    # 3rd failure
    delay3 = mgr.record_failure(now=106.0)
    assert delay3 == pytest.approx(8.0)
    assert mgr.attempts == 3

    # Success resets everything
    mgr.record_success()
    assert mgr.attempts == 0
    assert mgr.current_delay == pytest.approx(2.0)
    assert mgr.is_reconnecting is False


def test_can_attempt_now():
    mgr = ReconnectManager(camera_id="cam-1", initial_delay=2.0)

    # Before any attempt, can attempt
    assert mgr.can_attempt_now() is True

    # Failed at t=100.0s, delay is 2.0s
    mgr.record_failure(now=100.0)

    # At t=101.0s, only 1.0s elapsed -> False
    assert mgr.can_attempt_now(now=101.0) is False

    # At t=102.0s -> True
    assert mgr.can_attempt_now(now=102.0) is True


def test_reconnect_loop_with_mock():
    mgr = ReconnectManager(camera_id="cam-1", initial_delay=2.0, max_delay=30.0)

    sleep_calls = []
    release_calls = []
    connect_calls = []

    def mock_sleep(d: float):
        sleep_calls.append(d)

    def mock_release():
        release_calls.append(True)

    # Fail twice, succeed on 3rd
    attempts = [False, False, True]

    def mock_connect():
        connect_calls.append(True)
        return attempts.pop(0)

    result = mgr.reconnect(
        connect_fn=mock_connect,
        release_fn=mock_release,
        max_attempts=5,
        sleep_fn=mock_sleep,
    )

    assert result is True
    assert len(connect_calls) == 3
    assert len(release_calls) == 3
    assert len(sleep_calls) == 3
    assert sleep_calls == [2.0, 4.0, 8.0]
    assert mgr.attempts == 0  # reset on success
