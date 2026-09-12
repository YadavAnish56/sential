"""
Plate recognizer abstraction, deterministic mock implementation, and EasyOCR
deep-learning adapter for Sentinel ANPR.

Enables seamless swapping between test mock recognizers and deep-learning OCR engines
(e.g., EasyOCR 1.7.2 / PyTorch CRNN / PaddleOCR) without altering the ANPRCoordinator boundary.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Sequence
import numpy as np

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    TORCH_AVAILABLE = False

try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    easyocr = None
    EASYOCR_AVAILABLE = False

try:
    from .schemas import PlateCandidate
    from .normalization import normalize_plate, sanitize_plate_string, validate_indian_plate_format
    from .preprocessor import preprocess_crop_for_ocr
except ImportError:
    from ai_engine.anpr.schemas import PlateCandidate
    from ai_engine.anpr.normalization import normalize_plate, sanitize_plate_string, validate_indian_plate_format
    from ai_engine.anpr.preprocessor import preprocess_crop_for_ocr

logger = logging.getLogger("sentinel.ai_engine.anpr.recognizer")


class BasePlateRecognizer(ABC):
    """
    Abstract interface for license plate recognition engines.
    """

    @abstractmethod
    def recognize(self, crop: np.ndarray, pts_ms: float | None = None) -> PlateCandidate | None:
        """
        Execute license plate detection and character recognition on a vehicle crop.

        Parameters:
            crop: Isolated vehicle image region (np.ndarray).
            pts_ms: Optional presentation timestamp of the source frame.

        Returns:
            PlateCandidate containing raw_text, confidence, and optional plate_bbox,
            or None if no plate characters could be localized.
        """
        raise NotImplementedError


class MockPlateRecognizer(BasePlateRecognizer):
    """
    Deterministic mock recognizer for unit testing and CI pipelines.

    Does NOT fabricate text from pixels or perform fake OCR. Returns explicitly
    configured or queued PlateCandidate instances.
    """

    def __init__(
        self,
        default_candidate: PlateCandidate | None = None,
        responses: list[PlateCandidate | None] | None = None,
        exception_to_raise: Exception | None = None,
    ) -> None:
        self.default_candidate = default_candidate
        self._responses: list[PlateCandidate | None] = list(responses) if responses is not None else []
        self._call_count: int = 0
        self.exception_to_raise = exception_to_raise

    @property
    def call_count(self) -> int:
        return self._call_count

    def set_next_response(self, candidate: PlateCandidate | None) -> None:
        """Enqueue a specific response for the next recognize() invocation."""
        self._responses.append(candidate)

    def clear_responses(self) -> None:
        """Clear queued responses."""
        self._responses.clear()

    def recognize(self, crop: np.ndarray, pts_ms: float | None = None) -> PlateCandidate | None:
        """
        Return the next queued response, default candidate, or raise configured exception.
        """
        self._call_count += 1

        if self.exception_to_raise is not None:
            raise self.exception_to_raise

        if self._responses:
            return self._responses.pop(0)

        return self.default_candidate


class EasyOCRPlateRecognizer(BasePlateRecognizer):
    """
    License plate recognition adapter using EasyOCR (CRAFT detector + CRNN recognizer).

    CRITICAL ARCHITECTURAL NOTE:
        EasyOCR is a generic scene text detection and recognition engine, NOT a dedicated
        license plate detector. CRAFT detects arbitrary text regions on vehicles, including
        manufacturer emblems (HYUNDAI, SUZUKI, MARUTI, TATA), model names (SWIFT, CRETA),
        fuel tags (DIESEL, CNG), dealer stickers, and advertisements.

        This recognizer evaluates all candidate text boxes detected by CRAFT, passes each
        through Sentinel's Indian vehicle plate normalization and regex validation, ranks
        the hypotheses, and selects the best structurally valid Indian license plate.
        Non-plate text is strictly filtered out.
    """

    def __init__(
        self,
        languages: list[str] | None = None,
        gpu: bool | None = None,
        model_storage_directory: str | None = None,
        allow_invalid_candidates: bool = False,
        min_confidence: float = 0.20,
        enable_clahe_retry: bool = False,
        reader: Any = None,
    ) -> None:
        """
        Initialize EasyOCRPlateRecognizer.

        Parameters:
            languages: List of language codes (default: ['en']).
            gpu: Explicit GPU boolean, or None to autodetermine via torch.cuda.is_available().
            model_storage_directory: Optional local path to cached model weights (~/.EasyOCR/model/).
            allow_invalid_candidates: If True, returns best candidate even if not valid Indian format.
                                      Default is False (rejects all non-plate text like SUZUKI, DIESEL).
            min_confidence: Minimum raw OCR confidence to consider a detected text box [0.0, 1.0].
            enable_clahe_retry: If True, attempts a single deterministic retry on contrast-enhanced crop
                                when the primary crop yields no valid candidate.
            reader: Optional pre-initialized or mocked EasyOCR Reader instance for dependency injection.
        """
        self.languages = list(languages) if languages is not None else ["en"]
        self.model_storage_directory = model_storage_directory
        self.allow_invalid_candidates = allow_invalid_candidates
        self.min_confidence = min_confidence
        self.enable_clahe_retry = enable_clahe_retry

        self._call_count: int = 0
        self._reader: Any = reader
        self._status: str = "UNINITIALIZED"
        self._error_message: str | None = None
        self._device: str = "cpu"

        if self._reader is not None:
            self._status = "READY"
            if hasattr(self._reader, "device"):
                self._device = str(self._reader.device)
            elif gpu is True or (gpu is None and TORCH_AVAILABLE and torch.cuda.is_available()):
                self._device = "cuda"
        else:
            self._initialize_reader(gpu=gpu)

    @property
    def is_ready(self) -> bool:
        """True if the underlying OCR Reader is successfully initialized and ready."""
        return self._status == "READY" and self._reader is not None

    @property
    def status(self) -> str:
        """Current status: 'READY', 'FAILED', or 'UNINITIALIZED'."""
        return self._status

    @property
    def error_message(self) -> str | None:
        """Error message if initialization failed, otherwise None."""
        return self._error_message

    @property
    def device(self) -> str:
        """Resolved execution device ('cuda' or 'cpu')."""
        return self._device

    @property
    def call_count(self) -> int:
        """Total number of recognize() invocations."""
        return self._call_count

    @property
    def reader(self) -> Any:
        """Access underlying EasyOCR Reader instance."""
        return self._reader

    def _initialize_reader(self, gpu: bool | None) -> None:
        """Initialize EasyOCR Reader strictly offline with zero external downloads."""
        if not EASYOCR_AVAILABLE or easyocr is None:
            self._status = "FAILED"
            self._error_message = "EasyOCR package is not installed."
            logger.error(self._error_message)
            return

        if gpu is None:
            use_gpu = TORCH_AVAILABLE and torch.cuda.is_available()
        else:
            use_gpu = bool(gpu)

        self._device = "cuda" if use_gpu else "cpu"

        try:
            logger.info("Initializing EasyOCR Reader (offline, gpu=%s, languages=%s)...", use_gpu, self.languages)
            reader_kwargs: dict[str, Any] = {
                "gpu": use_gpu,
                "download_enabled": False,
                "verbose": False,
            }
            if self.model_storage_directory:
                reader_kwargs["model_storage_directory"] = self.model_storage_directory

            self._reader = easyocr.Reader(self.languages, **reader_kwargs)
            self._status = "READY"
            if hasattr(self._reader, "device"):
                self._device = str(self._reader.device)
            logger.info("EasyOCR Reader initialized successfully on device: %s", self._device)
        except Exception as exc:
            self._status = "FAILED"
            self._error_message = f"EasyOCR initialization failed: {exc}"
            logger.error(self._error_message, exc_info=True)
            self._reader = None

    def recognize(self, crop: np.ndarray, pts_ms: float | None = None) -> PlateCandidate | None:
        """
        Execute license plate detection and character recognition on a vehicle crop.

        Parameters:
            crop: Isolated vehicle image region (np.ndarray of shape HxWxC or HxW).
            pts_ms: Optional presentation timestamp of the source video frame.

        Returns:
            PlateCandidate for the best valid Indian plate candidate, or None if no
            valid plate could be localized or OCR failed.
        """
        self._call_count += 1

        if not self.is_ready or self._reader is None:
            logger.warning("EasyOCRPlateRecognizer called while not ready (status=%s)", self._status)
            return None

        if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0 or len(crop.shape) < 2:
            return None

        # Execute OCR on primary vehicle crop
        candidate = self._extract_best_candidate(crop=crop, pts_ms=pts_ms)

        # Optional single deterministic fallback on contrast-enhanced crop if primary failed
        if candidate is None and self.enable_clahe_retry:
            try:
                enhanced_crop = preprocess_crop_for_ocr(crop)
                candidate = self._extract_best_candidate(crop=enhanced_crop, pts_ms=pts_ms)
            except Exception as exc:
                logger.debug("Contrast-enhanced OCR retry failed: %s", exc)

        return candidate

    def _extract_best_candidate(self, crop: np.ndarray, pts_ms: float | None) -> PlateCandidate | None:
        """
        Run EasyOCR on the image array, extract all text detections, normalize each,
        and select the best hypothesis.
        """
        try:
            raw_results = self._reader.readtext(crop)
        except Exception as exc:
            logger.error("EasyOCR inference failed: %s", exc, exc_info=True)
            return None

        if not raw_results:
            return None

        crop_h, crop_w = crop.shape[:2]
        candidates: list[PlateCandidate] = []

        for item in raw_results:
            if len(item) < 3:
                continue
            bbox_poly, raw_text, conf = item[0], item[1], float(item[2])

            if not raw_text or not isinstance(raw_text, str):
                continue
            if conf < self.min_confidence:
                continue

            cleaned_text = raw_text.strip()
            if not cleaned_text:
                continue

            # Convert EasyOCR 4-point polygon to (x1, y1, x2, y2) text-region bounding box
            plate_bbox = self._poly_to_bbox(bbox_poly, crop_w=crop_w, crop_h=crop_h)

            # Pass through Sentinel Indian plate normalization & validation
            normalized_plate, is_valid_format = normalize_plate(cleaned_text)

            candidate = PlateCandidate(
                raw_text=cleaned_text,
                normalized_plate=normalized_plate,
                confidence=conf,
                is_valid_format=is_valid_format,
                pts_ms=pts_ms,
                plate_bbox=plate_bbox,
            )
            candidates.append(candidate)

        # For two-row or multi-segment plates (e.g. ['KA 02', 'MM 9091']):
        # Assemble reading-order concatenated candidate (top-to-bottom, left-to-right)
        if len(raw_results) > 1:
            def sort_reading_order(item: Any) -> tuple[float, float]:
                poly = item[0]
                cy = float(np.mean([pt[1] for pt in poly]))
                cx = float(np.mean([pt[0] for pt in poly]))
                return (cy, cx)

            valid_items = [
                it for it in raw_results
                if len(it) >= 3 and it[1] and isinstance(it[1], str) and it[1].strip()
            ]
            if len(valid_items) > 1:
                sorted_items = sorted(valid_items, key=sort_reading_order)
                combined_raw = " ".join(it[1].strip() for it in sorted_items)
                combined_conf = float(np.mean([float(it[2]) for it in sorted_items]))

                # Compute bounding box union
                all_xs = [float(pt[0]) for it in sorted_items for pt in it[0]]
                all_ys = [float(pt[1]) for it in sorted_items for pt in it[0]]
                min_x = max(0.0, min(float(crop_w), min(all_xs)))
                min_y = max(0.0, min(float(crop_h), min(all_ys)))
                max_x = max(0.0, min(float(crop_w), max(all_xs)))
                max_y = max(0.0, min(float(crop_h), max(all_ys)))
                combined_bbox = (round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2))

                norm_combined, is_valid_combined = normalize_plate(combined_raw)
                combined_candidate = PlateCandidate(
                    raw_text=combined_raw,
                    normalized_plate=norm_combined,
                    confidence=combined_conf,
                    is_valid_format=is_valid_combined,
                    pts_ms=pts_ms,
                    plate_bbox=combined_bbox,
                )
                candidates.append(combined_candidate)

        if not candidates:
            return None

        # Rank all candidates
        ranked = self._rank_candidates(candidates)
        if not ranked:
            return None

        best = ranked[0]

        # If not a valid Indian plate format and invalid candidates are disallowed, reject
        if not best.is_valid_format and not self.allow_invalid_candidates:
            return None

        return best

    @staticmethod
    def _poly_to_bbox(
        bbox_poly: Any,
        crop_w: int,
        crop_h: int,
    ) -> tuple[float, float, float, float] | None:
        """
        Convert EasyOCR 4-point polygon [[x1,y1],[x2,y1],[x2,y2],[x1,y2]] into
        an axis-aligned text-region bounding box (min_x, min_y, max_x, max_y)
        relative to the vehicle crop coordinate system.
        """
        try:
            xs = [float(pt[0]) for pt in bbox_poly]
            ys = [float(pt[1]) for pt in bbox_poly]
            min_x = max(0.0, min(float(crop_w), min(xs)))
            min_y = max(0.0, min(float(crop_h), min(ys)))
            max_x = max(0.0, min(float(crop_w), max(xs)))
            max_y = max(0.0, min(float(crop_h), max(ys)))
            if max_x > min_x and max_y > min_y:
                return (round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2))
        except Exception:
            pass
        return None

    @staticmethod
    def _rank_candidates(candidates: list[PlateCandidate]) -> list[PlateCandidate]:
        """
        Rank OCR candidates prioritizing:
        1. Valid Indian vehicle plate format (is_valid_format == True)
        2. OCR recognition confidence score
        3. Plausible plate character length (8 to 10 characters)
        4. Plausible horizontal aspect ratio (plates are horizontal rectangles)
        """
        def candidate_sort_key(cand: PlateCandidate) -> tuple[int, float, int, float]:
            # Priority 1: Valid Indian format (1 for valid, 0 for invalid)
            valid_score = 1 if cand.is_valid_format else 0

            # Priority 2: Recognition confidence score
            conf_score = cand.confidence

            # Priority 3: Plausible character length (8-10 characters)
            norm_len = len(cand.normalized_plate)
            length_score = 1 if (8 <= norm_len <= 10) else 0

            # Priority 4: Plausible plate aspect ratio
            aspect_score = 0.0
            if cand.plate_bbox:
                x1, y1, x2, y2 = cand.plate_bbox
                w = max(1.0, x2 - x1)
                h = max(1.0, y2 - y1)
                aspect_ratio = w / h
                if 1.5 <= aspect_ratio <= 6.0:
                    aspect_score = 1.0
                elif aspect_ratio >= 1.0:
                    aspect_score = 0.5

            return (valid_score, conf_score, length_score, aspect_score)

        return sorted(candidates, key=candidate_sort_key, reverse=True)

