"""Overlay payload built for the live bounding-box view."""

import pytest

from ai_engine.pipeline_manager import _build_overlay, _normalise_box


class FakePacket:
    def __init__(self, width=1920, height=1080, pts_ms=1234.5):
        self.width = width
        self.height = height
        self.pts_ms = pts_ms


class FakeTrack:
    def __init__(self, track_id, bbox, class_name="car", confidence=0.9):
        self.track_id = track_id
        self.bbox = bbox
        self.class_name = class_name
        self.confidence = confidence


class FakeTracking:
    def __init__(self, tracks):
        self.active_tracks = tracks


class FakeAnpr:
    def __init__(self, track_id, plate, confidence=0.8, status="RECOGNIZED"):
        self.track_id = track_id
        self.normalized_plate = plate
        self.confidence = confidence
        self.status = status


class FakeResult:
    def __init__(self, tracking=None, anpr=None, skipped=False):
        self.tracking_result = tracking
        self.anpr_results = anpr or []
        self.skipped = skipped


class TestNormaliseBox:
    def test_converts_pixels_to_fractions(self):
        assert _normalise_box((100, 50, 300, 250), 1000, 500) == [0.1, 0.1, 0.2, 0.4]

    def test_clamps_a_box_predicted_outside_the_frame(self):
        assert _normalise_box((-20, -10, 1200, 600), 1000, 500) == [0.0, 0.0, 1.0, 1.0]

    @pytest.mark.parametrize(
        "bbox,width,height",
        [
            ((100, 100, 100, 100), 1000, 500),  # zero area
            ((300, 50, 100, 250), 1000, 500),   # inverted
            ((1, 2, 3, 4), 0, 0),               # no frame size
            (None, 100, 100),                   # missing
            ((1, 2), 100, 100),                 # too short
        ],
    )
    def test_rejects_unusable_boxes(self, bbox, width, height):
        assert _normalise_box(bbox, width, height) is None


class TestBuildOverlay:
    def test_one_entry_per_tracked_vehicle(self):
        result = FakeResult(FakeTracking([
            FakeTrack(1, (0, 0, 960, 540)),
            FakeTrack(2, (960, 540, 1920, 1080)),
        ]))
        overlay = _build_overlay(result, FakePacket())
        assert [b["track_id"] for b in overlay["boxes"]] == [1, 2]
        assert overlay["boxes"][0]["box"] == [0.0, 0.0, 0.5, 0.5]

    def test_attaches_the_plate_read_for_that_track(self):
        result = FakeResult(
            FakeTracking([FakeTrack(7, (0, 0, 960, 540))]),
            [FakeAnpr(7, "GJ05AB1234", confidence=0.91)],
        )
        box = _build_overlay(result, FakePacket())["boxes"][0]
        assert box["plate"] == "GJ05AB1234"
        assert box["plate_confidence"] == pytest.approx(0.91)
        assert box["plate_status"] == "RECOGNIZED"

    def test_keeps_the_most_confident_read_for_a_track(self):
        result = FakeResult(
            FakeTracking([FakeTrack(7, (0, 0, 960, 540))]),
            [FakeAnpr(7, "GJ05AB0000", confidence=0.40), FakeAnpr(7, "GJ05AB1234", confidence=0.95)],
        )
        assert _build_overlay(result, FakePacket())["boxes"][0]["plate"] == "GJ05AB1234"

    def test_a_track_without_a_read_carries_no_plate(self):
        result = FakeResult(FakeTracking([FakeTrack(3, (0, 0, 100, 100))]))
        assert _build_overlay(result, FakePacket())["boxes"][0]["plate"] is None

    def test_a_skipped_frame_produces_nothing_so_the_last_overlay_stands(self):
        result = FakeResult(FakeTracking([FakeTrack(1, (0, 0, 100, 100))]), skipped=True)
        assert _build_overlay(result, FakePacket()) is None

    def test_a_frame_without_tracking_reports_an_empty_draw_list(self):
        overlay = _build_overlay(FakeResult(None), FakePacket())
        assert overlay["boxes"] == []
        assert overlay["width"] == 1920

    def test_unusable_boxes_are_dropped_not_rendered_at_the_origin(self):
        result = FakeResult(FakeTracking([
            FakeTrack(1, (0, 0, 0, 0)),
            FakeTrack(2, (100, 100, 500, 400)),
        ]))
        boxes = _build_overlay(result, FakePacket())["boxes"]
        assert [b["track_id"] for b in boxes] == [2]

    def test_carries_the_source_timestamp(self):
        overlay = _build_overlay(FakeResult(FakeTracking([])), FakePacket(pts_ms=9876.0))
        assert overlay["pts_ms"] == 9876.0
