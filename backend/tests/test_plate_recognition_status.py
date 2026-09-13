"""
Health must say plainly when plates cannot be read.

Vehicle detection and tracking continue without the ANPR models, so the
dashboards keep showing rising frame and track counts while nothing is ever
recorded. That combination reads as a working system to an operator, which is
the failure this endpoint is meant to make obvious.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"
for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from app.routes import health as health_module


def make_request(recognizer):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(plate_recognizer=recognizer)))


@pytest.fixture
def detector_present(monkeypatch):
    monkeypatch.setattr(health_module.os.path, "isfile", lambda _p: True)


@pytest.fixture
def detector_missing(monkeypatch):
    monkeypatch.setattr(health_module.os.path, "isfile", lambda _p: False)


class TestPlateRecognitionStatus:
    def test_ready_when_both_the_detector_and_recognizer_are_usable(self, detector_present):
        status = health_module._plate_recognition_status(
            make_request(SimpleNamespace(is_ready=True))
        )
        assert status["ready"] is True
        assert status["reason"] is None

    def test_missing_detector_weights_are_reported_with_the_expected_path(self, detector_missing):
        status = health_module._plate_recognition_status(
            make_request(SimpleNamespace(is_ready=True))
        )
        assert status["ready"] is False
        assert status["plate_detector_ready"] is False
        assert "plate-detector weights" in status["reason"]
        assert status["model_path"] in status["reason"]

    def test_a_recognizer_that_failed_to_load_is_not_counted_as_ready(self, detector_present):
        # The object exists but its own weights never loaded.
        status = health_module._plate_recognition_status(
            make_request(SimpleNamespace(is_ready=False))
        )
        assert status["ready"] is False
        assert status["recognizer_ready"] is False
        assert "text-recognition weights" in status["reason"]

    def test_both_failures_are_named_not_just_the_first(self, detector_missing):
        status = health_module._plate_recognition_status(
            make_request(SimpleNamespace(is_ready=False))
        )
        assert len(status["missing"]) == 2
        assert "plate-detector weights" in status["reason"]
        assert "text-recognition weights" in status["reason"]

    def test_a_missing_recognizer_is_not_ready(self, detector_present):
        status = health_module._plate_recognition_status(make_request(None))
        assert status["ready"] is False
        assert status["recognizer_ready"] is False

    def test_the_reason_says_sightings_will_not_be_recorded(self, detector_missing):
        status = health_module._plate_recognition_status(make_request(None))
        assert "no sighting will be recorded" in status["reason"]

    def test_a_recognizer_without_a_readiness_flag_is_assumed_usable(self, detector_present):
        """Older or stubbed recognizers expose no is_ready; presence is all we have."""
        status = health_module._plate_recognition_status(make_request(SimpleNamespace()))
        assert status["recognizer_ready"] is True
        assert status["ready"] is True
