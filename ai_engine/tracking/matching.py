"""
Deterministic IoU computation and association algorithms for Sentinel vehicle tracking.

Implemented purely in NumPy and Python standard library without external dependencies
(such as SciPy, filterpy, or lap).
"""

from __future__ import annotations

from typing import Sequence
import numpy as np

try:
    from ..schemas import Detection
    from .schemas import Track
except ImportError:
    from ai_engine.schemas import Detection
    from ai_engine.tracking.schemas import Track


def box_iou_batch(boxes_a: np.ndarray | Sequence[Sequence[float]], boxes_b: np.ndarray | Sequence[Sequence[float]]) -> np.ndarray:
    """
    Compute pairwise Intersection over Union (IoU) between two sets of bounding boxes.

    Parameters:
        boxes_a: Array-like of shape (N, 4) with (x1, y1, x2, y2).
        boxes_b: Array-like of shape (M, 4) with (x1, y1, x2, y2).

    Returns:
        np.ndarray of shape (N, M) with pairwise IoU values in [0.0, 1.0].
        Empty inputs return shape (N, M) filled with zeros.
    """
    arr_a = np.asarray(boxes_a, dtype=np.float32)
    arr_b = np.asarray(boxes_b, dtype=np.float32)

    n = len(arr_a)
    m = len(arr_b)

    if n == 0 or m == 0:
        return np.zeros((n, m), dtype=np.float32)

    # Ensure 2D shape (N, 4) and (M, 4)
    if arr_a.ndim == 1 and arr_a.shape[0] == 4:
        arr_a = arr_a.reshape(1, 4)
    if arr_b.ndim == 1 and arr_b.shape[0] == 4:
        arr_b = arr_b.reshape(1, 4)

    # Compute intersection top-left and bottom-right
    # arr_a[:, None, :2] has shape (N, 1, 2)
    # arr_b[None, :, :2] has shape (1, M, 2)
    inter_lt = np.maximum(arr_a[:, None, :2], arr_b[None, :, :2])  # (N, M, 2)
    inter_rb = np.minimum(arr_a[:, None, 2:], arr_b[None, :, 2:])  # (N, M, 2)

    inter_wh = np.clip(inter_rb - inter_lt, a_min=0.0, a_max=None)  # (N, M, 2)
    inter_area = inter_wh[:, :, 0] * inter_wh[:, :, 1]              # (N, M)

    # Individual box areas (handling zero/negative areas safely)
    area_a = np.clip(arr_a[:, 2] - arr_a[:, 0], a_min=0.0, a_max=None) * \
             np.clip(arr_a[:, 3] - arr_a[:, 1], a_min=0.0, a_max=None)  # (N,)
    area_b = np.clip(arr_b[:, 2] - arr_b[:, 0], a_min=0.0, a_max=None) * \
             np.clip(arr_b[:, 3] - arr_b[:, 1], a_min=0.0, a_max=None)  # (M,)

    union_area = area_a[:, None] + area_b[None, :] - inter_area
    union_area = np.maximum(union_area, 1e-7)  # Prevent division by zero

    iou = inter_area / union_area
    return np.clip(iou, 0.0, 1.0)


def associate_detections_to_tracks(
    detections: Sequence[Detection],
    tracks: Sequence[Track],
    predicted_boxes: Sequence[Sequence[float]] | None = None,
    iou_threshold: float = 0.3,
    match_class: bool = True,
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """
    Perform deterministic greedy IoU association between detections and tracks.

    Parameters:
        detections: Sequence of Detection objects from VehicleDetector.
        tracks: Sequence of existing Track objects.
        predicted_boxes: Optional predicted bounding boxes for tracks (defaults to track.bbox).
        iou_threshold: Minimum IoU required to confirm a match.
        match_class: If True, only allow matches between detections and tracks with the same class_id.

    Returns:
        tuple of:
            - matched_pairs: list of (detection_index, track_index)
            - unmatched_detections: list of unmatched detection indices
            - unmatched_tracks: list of unmatched track indices
    """
    num_dets = len(detections)
    num_trks = len(tracks)

    if num_dets == 0 or num_trks == 0:
        return [], list(range(num_dets)), list(range(num_trks))

    det_boxes = [d.bbox for d in detections]
    trk_boxes = (
        list(predicted_boxes)
        if predicted_boxes is not None and len(predicted_boxes) == num_trks
        else [t.bbox for t in tracks]
    )

    iou_matrix = box_iou_batch(det_boxes, trk_boxes)  # Shape (num_dets, num_trks)

    # Invalidate pairs with incompatible class_id if class matching is enabled
    if match_class:
        for d_idx, det in enumerate(detections):
            for t_idx, trk in enumerate(tracks):
                if det.class_id != trk.class_id:
                    iou_matrix[d_idx, t_idx] = -1.0

    # Deterministic greedy assignment:
    # Sort candidate pairs by IoU descending, using det_idx and trk_idx as deterministic tie-breakers
    candidates: list[tuple[float, int, int]] = []
    for d_idx in range(num_dets):
        for t_idx in range(num_trks):
            score = float(iou_matrix[d_idx, t_idx])
            if score >= iou_threshold:
                candidates.append((score, d_idx, t_idx))

    # Sort: descending by score, ascending by d_idx, ascending by t_idx
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    matched_pairs: list[tuple[int, int]] = []
    matched_dets: set[int] = set()
    matched_trks: set[int] = set()

    for _, d_idx, t_idx in candidates:
        if d_idx in matched_dets or t_idx in matched_trks:
            continue
        matched_pairs.append((d_idx, t_idx))
        matched_dets.add(d_idx)
        matched_trks.add(t_idx)

    unmatched_detections = [i for i in range(num_dets) if i not in matched_dets]
    unmatched_tracks = [j for j in range(num_trks) if j not in matched_trks]

    return matched_pairs, unmatched_detections, unmatched_tracks
