"""
Unit tests for Plate Preprocessor (Phase 20C).

Validates:
- Plate crop extraction with safety clamping and margin
- Aspect-preserving resize and CLAHE preprocessing
- Plate sharpness metric (Laplacian variance)
- Two-row plate aspect ratio detection
"""

import cv2
import numpy as np
import pytest

from ai_engine.anpr.preprocessor import (
    extract_plate_crop,
    compute_plate_sharpness,
    preprocess_plate_for_ocr,
    is_two_row_plate,
)


class TestPlatePreprocessor:

    def test_extract_plate_crop_basic(self):
        # 300x400 synthetic vehicle image
        veh = np.zeros((300, 400, 3), dtype=np.uint8)
        veh[100:150, 100:250] = 255  # 150x50 white plate region

        plate = extract_plate_crop(veh, (100, 100, 250, 150), margin_ratio=0.0)
        assert plate is not None
        assert plate.shape[0] == 50
        assert plate.shape[1] == 150

    def test_extract_plate_crop_with_margin(self):
        veh = np.zeros((300, 400, 3), dtype=np.uint8)
        # Margin of 10% expands bounding box safely
        plate = extract_plate_crop(veh, (50, 50, 150, 100), margin_ratio=0.1)
        assert plate is not None
        # Original width=100, height=50. Expanded by 10% on each side -> w~120, h~60
        assert plate.shape[1] >= 100
        assert plate.shape[0] >= 50

    def test_extract_plate_crop_clamping(self):
        veh = np.zeros((200, 200, 3), dtype=np.uint8)
        # Box near image edges
        plate = extract_plate_crop(veh, (190, 190, 250, 250), margin_ratio=0.1)
        assert plate is None or (plate.shape[0] <= 200 and plate.shape[1] <= 200)

    def test_compute_plate_sharpness(self):
        sharp = np.zeros((100, 200, 3), dtype=np.uint8)
        sharp[:, ::2] = 255  # High-frequency vertical stripes
        blurry = cv2.GaussianBlur(sharp, (15, 15), 0)

        sharp_score = compute_plate_sharpness(sharp)
        blurry_score = compute_plate_sharpness(blurry)

        assert sharp_score > blurry_score
        assert blurry_score >= 0.0

    def test_preprocess_plate_for_ocr(self):
        plate = np.ones((40, 160, 3), dtype=np.uint8) * 128
        prep = preprocess_plate_for_ocr(plate, target_height=64)
        assert prep is not None
        assert prep.shape[0] == 64
        # Width should preserve 4:1 aspect ratio -> ~256px
        assert prep.shape[1] == 256
        assert len(prep.shape) == 2  # Grayscale

    def test_is_two_row_plate(self):
        assert is_two_row_plate(1.8) is True
        assert is_two_row_plate(2.2) is True
        assert is_two_row_plate(2.7) is True
        # Standard single row plates have AR >= 2.8 (usually 4.0 - 5.0)
        assert is_two_row_plate(3.5) is False
        assert is_two_row_plate(4.8) is False
