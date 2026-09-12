"""
Unit tests for PlateDetector (Phase 20C).

Validates:
- Loading model
- Valid plate bounding box extraction
- Bounds clamping within vehicle crops
- No detection handling on blank/noisy images
- Confidence threshold filtering
"""

import numpy as np
import pytest

from ai_engine.anpr.plate_detector import PlateDetector, DetectedPlate


class TestPlateDetector:

    def test_model_loading_and_ready(self):
        detector = PlateDetector(model_path="models/license_plate_detector.pt")
        assert detector.is_ready is True
        assert detector.device in ["cuda", "cpu"]

    def test_missing_model_fails_safely(self):
        detector = PlateDetector(model_path="models/non_existent_weight.pt")
        assert detector.is_ready is False
        results = detector.detect(np.zeros((100, 100, 3), dtype=np.uint8))
        assert results == []

    def test_invalid_and_empty_crops(self):
        detector = PlateDetector(model_path="models/license_plate_detector.pt")
        assert detector.detect(None) == []
        assert detector.detect(np.zeros((5, 5, 3), dtype=np.uint8)) == []

    def test_bounds_clamping(self):
        detector = PlateDetector(model_path="models/license_plate_detector.pt", confidence_threshold=0.1)
        # 100x100 crop
        crop = np.zeros((100, 100, 3), dtype=np.uint8)
        # Even if inference runs on synthetic image, returned boxes must never exceed [0, 100]
        results = detector.detect(crop, vehicle_bbox=(500, 400, 600, 500))
        for p in results:
            px1, py1, px2, py2 = p.bbox_crop
            assert 0.0 <= px1 <= 100.0
            assert 0.0 <= py1 <= 100.0
            assert 0.0 <= px2 <= 100.0
            assert 0.0 <= py2 <= 100.0
            assert px2 >= px1
            assert py2 >= py1
            # Check frame mapping
            fx1, fy1, fx2, fy2 = p.bbox_frame
            assert fx1 == 500.0 + px1
            assert fy1 == 400.0 + py1
            assert fx2 == 500.0 + px2
            assert fy2 == 400.0 + py2

    def test_confidence_filtering(self):
        # High threshold should filter low confidence detections
        detector_high = PlateDetector(model_path="models/license_plate_detector.pt", confidence_threshold=0.999)
        crop = np.zeros((200, 200, 3), dtype=np.uint8)
        results = detector_high.detect(crop)
        assert len(results) == 0
