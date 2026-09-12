"""
Dedicated License Plate Detector for Sentinel AI Engine.

Wraps a verified YOLO license plate detection model to localize license plates
specifically within vehicle crops. Maps relative crop coordinates back to
full-frame coordinates.

Architecture: YOLOv8n (Nano) fine-tuned for license plate detection.
License: MIT
Model File: models/license_plate_detector.pt
SHA256: 2d95861825bb4184404344c9cf809f40fd31dba785fe54e8ba5b9a3583789822
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Sequence
import numpy as np

try:
    from ..config import resolve_model_path
except (ImportError, ValueError):
    try:
        from ai_engine.config import resolve_model_path
    except ImportError:
        def resolve_model_path(p, d=""):
            return str(p or d)

try:
    import torch
    from ultralytics import YOLO
    ULTRALYTICS_AVAILABLE = True
except ImportError:
    torch = None
    YOLO = None
    ULTRALYTICS_AVAILABLE = False

logger = logging.getLogger("sentinel.ai_engine.anpr.plate_detector")


@dataclass(slots=True)
class DetectedPlate:
    """
    Structured representation of a detected license plate within a vehicle crop.

    Attributes:
        bbox_crop: Bounding box within the vehicle crop (x1, y1, x2, y2).
        bbox_frame: Bounding box mapped back to the full video frame (x1, y1, x2, y2).
        confidence: Detection confidence score [0.0, 1.0].
        width: Plate box width in pixels.
        height: Plate box height in pixels.
        aspect_ratio: Width / Height aspect ratio.
    """
    bbox_crop: tuple[float, float, float, float]
    bbox_frame: tuple[float, float, float, float]
    confidence: float
    width: float
    height: float
    aspect_ratio: float


class PlateDetector:
    """
    Dedicated plate detector operating on vehicle crops.
    """

    def __init__(
        self,
        model_path: str = "models/license_plate_detector.pt",
        confidence_threshold: float = 0.25,
        device: str = "auto",
    ) -> None:
        """
        Initialize the PlateDetector.

        Parameters:
            model_path: Path to the local .pt model file.
            confidence_threshold: Minimum detection confidence threshold.
            device: 'cuda', 'cpu', or 'auto'.
        """
        self.model_path = resolve_model_path(
            model_path or os.environ.get("PLATE_MODEL_PATH"),
            "models/license_plate_detector.pt"
        )
        self.confidence_threshold = confidence_threshold

        if device == "auto":
            self.device = "cuda" if (torch is not None and torch.cuda.is_available()) else "cpu"
        else:
            self.device = device

        self._model = None
        self._is_ready = False
        self._load_model()

    @property
    def is_ready(self) -> bool:
        """True if model weights loaded successfully."""
        return self._is_ready

    def _load_model(self) -> None:
        """Load YOLO model strictly from local disk with zero auto-downloads."""
        if not ULTRALYTICS_AVAILABLE or YOLO is None:
            logger.error("PlateDetector: Ultralytics/PyTorch is not available.")
            self._is_ready = False
            return

        self.model_path = resolve_model_path(self.model_path, "models/license_plate_detector.pt")

        if not os.path.exists(self.model_path):
            logger.error(
                "PlateDetector: Model file not found at %s. Runtime downloads are disabled.",
                self.model_path,
            )
            self._is_ready = False
            return

        try:
            logger.info("PlateDetector: Loading model from %s on %s...", self.model_path, self.device)
            self._model = YOLO(self.model_path)
            self._is_ready = True
            logger.info("PlateDetector: Model loaded successfully.")
        except Exception as exc:
            logger.error("PlateDetector: Failed to load model: %s", exc, exc_info=True)
            self._is_ready = False

    def detect(
        self,
        vehicle_crop: np.ndarray,
        vehicle_bbox: tuple[float, float, float, float] | Sequence[float] | None = None,
    ) -> list[DetectedPlate]:
        """
        Detect license plates within a vehicle image crop.

        Parameters:
            vehicle_crop: Cropped image of the vehicle (HxWxC BGR).
            vehicle_bbox: Optional bounding box of the vehicle in the parent frame (vx1, vy1, vx2, vy2).

        Returns:
            List of DetectedPlate objects, ranked by detection confidence descending.
        """
        if not self._is_ready or self._model is None:
            return []

        if vehicle_crop is None or not hasattr(vehicle_crop, "shape") or vehicle_crop.size == 0:
            return []

        vh, vw = vehicle_crop.shape[:2]
        if vh < 20 or vw < 20:
            return []

        vx1, vy1 = (0.0, 0.0)
        if vehicle_bbox is not None and len(vehicle_bbox) == 4:
            vx1, vy1 = float(vehicle_bbox[0]), float(vehicle_bbox[1])

        try:
            device_arg = 0 if self.device == "cuda" else "cpu"
            results = self._model.predict(
                vehicle_crop,
                conf=self.confidence_threshold,
                device=device_arg,
                verbose=False,
            )
        except Exception as exc:
            logger.error("PlateDetector: Inference failed: %s", exc, exc_info=True)
            return []

        if not results or len(results) == 0:
            return []

        detected_plates: list[DetectedPlate] = []
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []

        for box in boxes:
            conf = float(box.conf[0].item())
            if conf < self.confidence_threshold:
                continue

            xyxy = box.xyxy[0].cpu().numpy()
            raw_px1, raw_py1, raw_px2, raw_py2 = [float(v) for v in xyxy]

            # Strictly clamp coordinates within vehicle crop dimensions
            px1 = max(0.0, min(float(vw), raw_px1))
            py1 = max(0.0, min(float(vh), raw_py1))
            px2 = max(0.0, min(float(vw), raw_px2))
            py2 = max(0.0, min(float(vh), raw_py2))

            pw = px2 - px1
            ph = py2 - py1

            if pw <= 5.0 or ph <= 5.0:
                continue

            aspect_ratio = pw / ph

            # Map to parent video frame coordinates
            fx1 = vx1 + px1
            fy1 = vy1 + py1
            fx2 = vx1 + px2
            fy2 = vy1 + py2

            detected_plates.append(
                DetectedPlate(
                    bbox_crop=(round(px1, 2), round(py1, 2), round(px2, 2), round(py2, 2)),
                    bbox_frame=(round(fx1, 2), round(fy1, 2), round(fx2, 2), round(fy2, 2)),
                    confidence=conf,
                    width=round(pw, 2),
                    height=round(ph, 2),
                    aspect_ratio=round(aspect_ratio, 2),
                )
            )

        # Sort detections by confidence score descending
        detected_plates.sort(key=lambda p: p.confidence, reverse=True)
        return detected_plates
