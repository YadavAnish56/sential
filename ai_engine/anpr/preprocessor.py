"""
OpenCV-based vehicle cropping and image quality preprocessing for Sentinel ANPR.

Responsibilities:
- Safe coordinate clamping and bounding box extraction.
- Deterministic crop quality calculation using Laplacian variance and contrast (independent of detector confidence).
- Lightweight image contrast enhancement and bilateral filtering.
- Memory isolation: strictly returns array copies; never holds references to full video frames.
"""

from __future__ import annotations

import logging
from typing import Sequence
import cv2
import numpy as np

logger = logging.getLogger("sentinel.ai_engine.anpr.preprocessor")


def extract_vehicle_crop(
    frame: np.ndarray,
    bbox: tuple[float, float, float, float] | Sequence[float],
    min_width: int = 40,
    min_height: int = 30,
) -> np.ndarray | None:
    """
    Extract a vehicle bounding box crop safely from a full frame.

    Parameters:
        frame: Full source image (np.ndarray of shape HxWxC or HxW).
        bbox: Bounding box coordinates (x1, y1, x2, y2).
        min_width: Minimum allowable crop width in pixels.
        min_height: Minimum allowable crop height in pixels.

    Returns:
        Isolated copy of the cropped image region (np.ndarray), or None if invalid.
    """
    if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
        return None

    h, w = frame.shape[:2]
    if h <= 0 or w <= 0:
        return None

    if len(bbox) != 4:
        return None

    x1, y1, x2, y2 = bbox

    # Clamp coordinates safely to frame boundaries
    clamped_x1 = max(0, min(int(round(x1)), w - 1))
    clamped_y1 = max(0, min(int(round(y1)), h - 1))
    clamped_x2 = max(0, min(int(round(x2)), w))
    clamped_y2 = max(0, min(int(round(y2)), h))

    crop_w = clamped_x2 - clamped_x1
    crop_h = clamped_y2 - clamped_y1

    if crop_w < min_width or crop_h < min_height:
        return None

    # Return an explicit memory copy to release reference to the full parent frame
    return frame[clamped_y1:clamped_y2, clamped_x1:clamped_x2].copy()


def compute_crop_quality(crop: np.ndarray) -> float:
    """
    Calculate an objective image quality score for a vehicle crop.

    Combines:
    - Sharpness via Laplacian variance (focus metric)
    - Dynamic range / contrast via standard deviation
    - Bounded area factor

    Does NOT use detector confidence or external tracker state.

    Returns:
        Deterministic quality score (float >= 0.0). Higher implies clearer image.
    """
    if crop is None or crop.size == 0:
        return 0.0

    # Convert to grayscale if 3-channel BGR/RGB
    if len(crop.shape) == 3 and crop.shape[2] == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    elif len(crop.shape) == 2:
        gray = crop
    else:
        return 0.0

    h, w = gray.shape[:2]
    if h < 10 or w < 10:
        return 0.0

    # 1. Focus / Sharpness metric: variance of the Laplacian
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # 2. Contrast metric: standard deviation of pixel intensities
    contrast_std = float(np.std(gray))

    # 3. Area scale factor (log scale to keep bounded)
    area = h * w
    area_factor = float(np.clip(np.log10(max(100.0, area)) / 4.0, 0.5, 1.5))

    # Composite score (weighted combination)
    quality = (laplacian_var * 0.4 + contrast_std * 0.6) * area_factor
    return max(0.0, round(quality, 2))


def preprocess_crop_for_ocr(
    crop: np.ndarray,
    target_width: int = 320,
    target_height: int = 240,
) -> np.ndarray:
    """
    Apply standard OpenCV contrast normalization and noise reduction to prepare
    a vehicle crop for downstream plate recognition.

    Returns:
        Preprocessed grayscale or contrast-enhanced image.
    """
    if crop is None or crop.size == 0:
        return np.zeros((target_height, target_width), dtype=np.uint8)

    if len(crop.shape) == 3 and crop.shape[2] == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop.copy()

    # Bilateral filter to smooth flat regions while preserving character edges
    filtered = cv2.bilateralFilter(gray, d=5, sigmaColor=50, sigmaSpace=50)

    # Adaptive histogram equalization (CLAHE) for illumination invariance
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(filtered)

    return enhanced
