"""
Vehicle Detector Module for Sentinel.

Consumes Phase 4 FramePacket, executes YOLOv8 inference, extracts vehicle bounding
boxes, and preserves source presentation timestamps (PTS) and camera IDs.

CRITICAL REQUIREMENTS:
- No fake detections or fabricated bounding boxes.
- If model weights are unavailable, report MODEL_NOT_AVAILABLE honestly.
- Resolve vehicle class IDs dynamically from the model's `model.names`.
- Preserve source PTS and camera ID from FramePacket.
- Support CPU and CUDA device auto-selection.
"""

from __future__ import annotations

import logging
import os
import time
import numpy as np
from streaming.frame_reader import FramePacket


try:
    from .schemas import Detection, DetectionResult
    from .config import DetectorConfig, DEFAULT_TARGET_CLASSES
except ImportError:
    from schemas import Detection, DetectionResult
    from config import DetectorConfig, DEFAULT_TARGET_CLASSES


logger = logging.getLogger("sentinel.ai_engine.detector")


class ModelNotAvailableError(RuntimeError):
    """Raised when inference is attempted but the model weights are unavailable."""
    pass


class VehicleDetector:
    """
    YOLOv8-based vehicle detector consuming Phase 4 FramePacket instances.
    """

    def __init__(
        self,
        config: DetectorConfig | None = None,
        model_path: str | None = None,
        confidence: float | None = None,
        target_classes: Sequence[str] | None = None,
        device: str | None = None,
    ) -> None:
        self.config = config or DetectorConfig()
        if model_path is not None:
            self.config.model_path = model_path
        if confidence is not None:
            self.config.confidence = confidence
        if target_classes is not None:
            self.config.target_classes = target_classes
        if device is not None:
            self.config.device = device

        self.model: Any = None
        self.is_ready: bool = False
        self.status: str = "INITIALIZING"
        self.error_message: str | None = None
        self.resolved_device: str = "cpu"
        self.target_class_ids: list[int] = []
        self.class_id_to_name: dict[int, str] = {}

        self._initialize_device()
        self._load_model()

    def _initialize_device(self) -> None:
        """Detect and set execution device (CPU vs CUDA)."""
        requested = (self.config.device or "auto").lower()

        try:
            import torch
            cuda_available = torch.cuda.is_available()
        except ImportError:
            cuda_available = False

        if requested == "cuda":
            if cuda_available:
                self.resolved_device = "cuda"
            else:
                logger.warning("CUDA requested but not available. Falling back to CPU.")
                self.resolved_device = "cpu"
        elif requested == "cpu":
            self.resolved_device = "cpu"
        else:  # "auto"
            self.resolved_device = "cuda" if cuda_available else "cpu"

        logger.info(
            "VehicleDetector selected device: %s (CUDA available: %s)",
            self.resolved_device, cuda_available
        )

    def _load_model(self) -> None:
        """Load YOLOv8 model weights and resolve target vehicle class IDs."""
        path = self.config.model_path

        if not path or not os.path.isfile(path):
            self.status = "MODEL_NOT_AVAILABLE"
            self.is_ready = False
            self.error_message = (
                f"Model weights file not found at '{path}'. "
                "Place YOLOv8 weights (e.g. yolov8n.pt) at this path to enable inference."
            )
            logger.warning("VehicleDetector: %s", self.error_message)
            return

        try:
            from ultralytics import YOLO

            logger.info("Loading YOLO model from %s on %s...", path, self.resolved_device)
            self.model = YOLO(path)
            self._resolve_vehicle_classes()

            self.status = "READY"
            self.is_ready = True
            self.error_message = None
            logger.info(
                "YOLO model loaded successfully. Target classes resolved: %s",
                self.class_id_to_name
            )

        except Exception as e:
            self.status = "LOAD_ERROR"
            self.is_ready = False
            self.error_message = f"Failed to load YOLO model: {e}"
            logger.error(self.error_message)

    def _resolve_vehicle_classes(self) -> None:
        """
        Dynamically map configured vehicle category names to actual model class IDs
        using `model.names`. Avoids hard-coding class IDs.
        """
        if not self.model or not hasattr(self.model, "names"):
            return

        model_names: dict[int, str] = self.model.names
        # Standardize target names for matching (lowercase)
        targets = {name.lower().strip() for name in self.config.target_classes}

        self.target_class_ids = []
        self.class_id_to_name = {}

        for cid, cname in model_names.items():
            if str(cname).lower().strip() in targets:
                self.target_class_ids.append(int(cid))
                self.class_id_to_name[int(cid)] = str(cname)

        if not self.target_class_ids:
            logger.warning(
                "None of the target classes %s were found in model names: %s",
                targets, list(model_names.values())[:10]
            )

    def detect(self, packet: FramePacket) -> DetectionResult:
        """
        Run vehicle detection on a single FramePacket.

        Parameters:
            packet: Phase 4 FramePacket instance containing raw frame and source PTS.

        Returns:
            DetectionResult containing detected vehicles, bounding boxes, confidence,
            camera ID, and source presentation timestamp (PTS).
        """
        if not self.is_ready or self.model is None:
            raise ModelNotAvailableError(
                self.error_message or "VehicleDetector is not ready. Model is unavailable."
            )

        if packet.frame is None:
            return DetectionResult(
                camera_id=packet.camera_id,
                pts_ms=packet.pts_ms,
                detections=[],
                inference_time_ms=0.0,
                device=self.resolved_device,
                model_name=os.path.basename(self.config.model_path),
            )

        start_time = time.perf_counter()

        # Run inference using Ultralytics YOLO predict
        # Filter by target_class_ids and confidence threshold
        results = self.model.predict(
            source=packet.frame,
            conf=self.config.confidence,
            classes=self.target_class_ids if self.target_class_ids else None,
            device=self.resolved_device,
            verbose=False,
        )

        inference_time_ms = (time.perf_counter() - start_time) * 1000.0

        detections: list[Detection] = []

        if results and len(results) > 0:
            boxes = results[0].boxes
            if boxes is not None:
                try:
                    xyxy = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else np.asarray(boxes.xyxy)
                    confs = boxes.conf.cpu().numpy() if hasattr(boxes.conf, "cpu") else np.asarray(boxes.conf)
                    cls_ids = (
                        boxes.cls.cpu().numpy().astype(int)
                        if hasattr(boxes.cls, "cpu")
                        else np.asarray(boxes.cls, dtype=int)
                    )

                    for i in range(len(xyxy)):
                        cid = int(cls_ids[i])
                        cname = self.class_id_to_name.get(
                            cid, self.model.names.get(cid, f"class_{cid}") if hasattr(self.model, "names") else f"class_{cid}"
                        )
                        conf = float(confs[i])
                        box = xyxy[i]

                        # Strictly preserve camera_id and source PTS from packet
                        # Never use arrival wall-clock time
                        det = Detection(
                            class_id=cid,
                            class_name=cname,
                            confidence=conf,
                            x1=float(box[0]),
                            y1=float(box[1]),
                            x2=float(box[2]),
                            y2=float(box[3]),
                            camera_id=packet.camera_id,
                            pts_ms=packet.pts_ms,
                        )
                        detections.append(det)
                except Exception as e:
                    logger.error("Error parsing YOLO detections: %s", e)


        return DetectionResult(
            camera_id=packet.camera_id,
            pts_ms=packet.pts_ms,
            detections=detections,
            inference_time_ms=inference_time_ms,
            device=self.resolved_device,
            model_name=os.path.basename(self.config.model_path),
        )
