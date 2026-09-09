import os
import sys
from pathlib import Path

from ai_engine.schemas import Detection, DetectionResult



def test_detection_attributes_and_properties():
    det = Detection(
        class_id=2,
        class_name="car",
        confidence=0.875,
        x1=100.0,
        y1=150.0,
        x2=300.0,
        y2=250.0,
        camera_id="cam-7",
        pts_ms=1240.5,
    )

    assert det.class_id == 2
    assert det.class_name == "car"
    assert det.confidence == 0.875
    assert det.bbox == (100.0, 150.0, 300.0, 250.0)
    assert det.width == 200.0
    assert det.height == 100.0
    assert det.area == 20000.0
    assert det.camera_id == "cam-7"
    assert det.pts_ms == 1240.5


def test_detection_to_dict():
    det = Detection(
        class_id=3,
        class_name="motorcycle",
        confidence=0.91234,
        x1=50.123,
        y1=60.456,
        x2=120.789,
        y2=180.999,
        camera_id="cam-1",
        pts_ms=33.33,
    )

    d = det.to_dict()
    assert d["class_id"] == 3
    assert d["class_name"] == "motorcycle"
    assert d["confidence"] == 0.9123
    assert d["x1"] == 50.12
    assert d["y1"] == 60.46
    assert d["x2"] == 120.79
    assert d["y2"] == 181.0
    assert d["camera_id"] == "cam-1"
    assert d["pts_ms"] == 33.33


def test_detection_result_empty():
    res = DetectionResult(
        camera_id="cam-2",
        pts_ms=100.0,
        inference_time_ms=12.5,
        device="cpu",
        model_name="yolov8n.pt",
    )

    assert res.count == 0
    assert res.camera_id == "cam-2"
    assert res.pts_ms == 100.0
    d = res.to_dict()
    assert d["count"] == 0
    assert d["detections"] == []
    assert d["device"] == "cpu"
    assert d["inference_time_ms"] == 12.5


def test_detection_result_with_detections():
    d1 = Detection(
        class_id=2, class_name="car", confidence=0.85,
        x1=10, y1=10, x2=50, y2=50, camera_id="cam-9", pts_ms=500.0
    )
    d2 = Detection(
        class_id=7, class_name="truck", confidence=0.92,
        x1=60, y1=20, x2=180, y2=120, camera_id="cam-9", pts_ms=500.0
    )
    res = DetectionResult(
        camera_id="cam-9",
        pts_ms=500.0,
        detections=[d1, d2],
        inference_time_ms=18.4,
        device="cuda",
        model_name="yolov8n.pt",
    )

    assert res.count == 2
    data = res.to_dict()
    assert data["count"] == 2
    assert len(data["detections"]) == 2
    assert data["detections"][0]["class_name"] == "car"
    assert data["detections"][1]["class_name"] == "truck"
    # Ensure source PTS is preserved on both detections
    assert data["detections"][0]["pts_ms"] == 500.0
    assert data["detections"][1]["pts_ms"] == 500.0
