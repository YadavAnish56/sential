"""
Multi-Object Vehicle Tracker for Sentinel AI Engine.

Implements a deterministic SORT-style tracker using pure NumPy:
- IoU-based association with class-aware constraints
- Simple linear velocity prediction for bounding box motion
- Discontinuity-aware lifecycle (flushes tracks without resetting monotonic ID counter)
- Strict per-camera state isolation
- Zero external tracking libraries (no SciPy, filterpy, or Re-ID)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

try:
    from ..schemas import Detection, DetectionResult
    from .schemas import Track, TrackState, TrackingResult
    from .matching import associate_detections_to_tracks
except ImportError:
    from ai_engine.schemas import Detection, DetectionResult
    from ai_engine.tracking.schemas import Track, TrackState, TrackingResult
    from ai_engine.tracking.matching import associate_detections_to_tracks

logger = logging.getLogger("sentinel.ai_engine.tracking.tracker")


@dataclass
class TrackerConfig:
    """
    Configuration parameters for multi-object vehicle tracking.

    Attributes:
        iou_threshold: Minimum IoU overlap required to match a detection to an existing track.
        min_hits: Number of consecutive detections required to transition TENTATIVE -> CONFIRMED.
        max_misses: Maximum consecutive missed frames before a track is TERMINATED.
        max_age_ms: Optional maximum PTS time gap (in ms) before a missed track is TERMINATED.
        match_class: If True, only allow associations between detections and tracks of the same class.
        use_motion_prediction: If True, predict bounding box positions using linear velocity before matching.
        max_prediction_ms: Maximum temporal horizon (in ms) to extrapolate motion prediction.
    """
    iou_threshold: float = 0.3
    min_hits: int = 2
    max_misses: int = 15
    max_age_ms: float | None = 2000.0
    match_class: bool = True
    use_motion_prediction: bool = True
    max_prediction_ms: float = 500.0


class CameraTracker:
    """
    Manages multi-object tracking for exactly one camera feed.

    Guarantees:
    - Monotonically increasing track IDs unique to this camera.
    - Zero state leakage across camera boundaries.
    - On discontinuity: terminates current tracks into lost_tracks, but preserves next_track_id.
    - Does not depend on system wall-clock time; relies strictly on frame sequence and source PTS.
    """

    def __init__(self, camera_id: str, config: TrackerConfig | None = None) -> None:
        self.camera_id = camera_id
        self.config = config or TrackerConfig()

        self._next_track_id: int = 1
        self._active_tracks: dict[int, Track] = {}
        self._frame_count: int = 0

    @property
    def next_track_id(self) -> int:
        """Current monotonic track ID counter for this camera."""
        return self._next_track_id

    @property
    def active_track_count(self) -> int:
        return len(self._active_tracks)

    def _predict_box(self, track: Track, current_pts_ms: float | None) -> tuple[float, float, float, float]:
        """
        Compute deterministic linear motion prediction for a track bounding box.
        """
        if not self.config.use_motion_prediction or track.hits <= 1:
            return track.bbox

        w = track.width
        h = track.height
        vx1, vy1, _, _ = track.velocity

        if track.velocity_is_per_ms and current_pts_ms is not None and track.last_pts_ms is not None:
            dt = max(0.0, current_pts_ms - track.last_pts_ms)
            # Bound prediction delta to prevent extreme runaway extrapolation
            dt = min(dt, self.config.max_prediction_ms)
            pred_x1 = track.bbox[0] + (vx1 * dt)
            pred_y1 = track.bbox[1] + (vy1 * dt)
        else:
            # Fall back to single frame-step velocity
            pred_x1 = track.bbox[0] + vx1
            pred_y1 = track.bbox[1] + vy1

        return (pred_x1, pred_y1, pred_x1 + w, pred_y1 + h)

    def update(self, result: DetectionResult) -> TrackingResult:
        """
        Process incoming DetectionResult and advance the camera tracker state.

        Parameters:
            result: DetectionResult containing detections, camera_id, pts_ms, and is_discontinuity.

        Returns:
            TrackingResult detailing active, newly created, and lost tracks.
        """
        self._frame_count += 1
        pts_ms = result.pts_ms
        new_tracks: list[Track] = []
        lost_tracks: list[Track] = []

        # Handle Stream Discontinuity (jump, loop, or reconnect)
        if result.is_discontinuity:
            logger.info(
                "Camera %s: Stream discontinuity detected at PTS %s. Flushing %d active tracks.",
                self.camera_id, pts_ms, len(self._active_tracks)
            )
            for track in self._active_tracks.values():
                track.state = TrackState.TERMINATED
                lost_tracks.append(track)
            self._active_tracks.clear()

            # Process any detections in this frame as brand new tracks (preserving monotonic counter)
            for det in result.detections:
                new_trk = self._create_track(det, pts_ms)
                self._active_tracks[new_trk.track_id] = new_trk
                new_tracks.append(new_trk)

            return TrackingResult(
                camera_id=self.camera_id,
                pts_ms=pts_ms,
                is_discontinuity=True,
                active_tracks=list(self._active_tracks.values()),
                new_tracks=new_tracks,
                lost_tracks=lost_tracks,
            )

        # Standard Frame Progression
        existing_tracks = list(self._active_tracks.values())
        predicted_boxes = [self._predict_box(t, pts_ms) for t in existing_tracks]

        # Associate detections to active tracks
        matched_pairs, unmatched_det_indices, unmatched_trk_indices = associate_detections_to_tracks(
            detections=result.detections,
            tracks=existing_tracks,
            predicted_boxes=predicted_boxes,
            iou_threshold=self.config.iou_threshold,
            match_class=self.config.match_class,
        )

        # 1. Update Matched Tracks
        for d_idx, t_idx in matched_pairs:
            det = result.detections[d_idx]
            track = existing_tracks[t_idx]

            # Calculate and smooth linear velocity
            prev_bbox = track.bbox
            new_bbox = det.bbox
            dt_ms = (pts_ms - track.last_pts_ms) if (pts_ms is not None and track.last_pts_ms is not None) else None

            if dt_ms is not None and dt_ms > 0:
                vel = (
                    (new_bbox[0] - prev_bbox[0]) / dt_ms,
                    (new_bbox[1] - prev_bbox[1]) / dt_ms,
                    (new_bbox[2] - prev_bbox[2]) / dt_ms,
                    (new_bbox[3] - prev_bbox[3]) / dt_ms,
                )
                track.velocity = vel
                track.velocity_is_per_ms = True
            else:
                vel = (
                    new_bbox[0] - prev_bbox[0],
                    new_bbox[1] - prev_bbox[1],
                    new_bbox[2] - prev_bbox[2],
                    new_bbox[3] - prev_bbox[3],
                )
                track.velocity = vel
                track.velocity_is_per_ms = False

            # Update bounding box, confidence, and timestamps
            track.bbox = new_bbox
            track.confidence = det.confidence
            track.last_pts_ms = pts_ms
            track.hits += 1
            track.misses = 0

            # Update best confidence / best bounding box for downstream ANPR
            if det.confidence > track.best_confidence:
                track.best_confidence = det.confidence
                track.best_bbox = new_bbox

            # Lifecycle State Transitions
            if track.state == TrackState.TENTATIVE and track.hits >= self.config.min_hits:
                track.state = TrackState.CONFIRMED
            elif track.state == TrackState.LOST:
                track.state = TrackState.CONFIRMED

        # 2. Handle Unmatched Tracks (Missed in this frame)
        for t_idx in unmatched_trk_indices:
            track = existing_tracks[t_idx]
            track.misses += 1

            if track.state in (TrackState.CONFIRMED, TrackState.TENTATIVE):
                track.state = TrackState.LOST

            # Check termination criteria (frame misses or PTS age limit)
            is_expired_misses = track.misses > self.config.max_misses
            is_expired_pts = False
            if self.config.max_age_ms is not None and pts_ms is not None and track.last_pts_ms is not None:
                if (pts_ms - track.last_pts_ms) > self.config.max_age_ms:
                    is_expired_pts = True

            if is_expired_misses or is_expired_pts:
                track.state = TrackState.TERMINATED
                del self._active_tracks[track.track_id]
                lost_tracks.append(track)

        # 3. Handle Unmatched Detections (New vehicle candidates)
        for d_idx in unmatched_det_indices:
            det = result.detections[d_idx]
            new_trk = self._create_track(det, pts_ms)
            self._active_tracks[new_trk.track_id] = new_trk
            new_tracks.append(new_trk)

        return TrackingResult(
            camera_id=self.camera_id,
            pts_ms=pts_ms,
            is_discontinuity=False,
            active_tracks=list(self._active_tracks.values()),
            new_tracks=new_tracks,
            lost_tracks=lost_tracks,
        )

    def _create_track(self, det: Detection, pts_ms: float | None) -> Track:
        """Instantiate a new track using the camera's monotonic ID counter."""
        trk_id = self._next_track_id
        self._next_track_id += 1

        initial_state = TrackState.CONFIRMED if self.config.min_hits <= 1 else TrackState.TENTATIVE

        return Track(
            track_id=trk_id,
            camera_id=self.camera_id,
            class_id=det.class_id,
            class_name=det.class_name,
            confidence=det.confidence,
            bbox=det.bbox,
            state=initial_state,
            first_pts_ms=pts_ms,
            last_pts_ms=pts_ms,
            hits=1,
            misses=0,
            best_confidence=det.confidence,
            best_bbox=det.bbox,
            velocity=(0.0, 0.0, 0.0, 0.0),
            velocity_is_per_ms=False,
        )


class VehicleTracker:
    """
    Multi-camera vehicle tracking coordinator.

    Routes DetectionResult objects to their respective CameraTracker instances,
    lazily creating trackers per camera_id and guaranteeing 100% camera state isolation.
    """

    def __init__(self, config: TrackerConfig | None = None) -> None:
        self.config = config or TrackerConfig()
        self._camera_trackers: dict[str, CameraTracker] = {}

    def get_tracker(self, camera_id: str) -> CameraTracker:
        """Retrieve or lazily instantiate an isolated tracker for the given camera."""
        if camera_id not in self._camera_trackers:
            self._camera_trackers[camera_id] = CameraTracker(
                camera_id=camera_id,
                config=self.config,
            )
        return self._camera_trackers[camera_id]

    def update(self, result: DetectionResult) -> TrackingResult:
        """
        Route detection results to the appropriate camera tracker.

        Parameters:
            result: DetectionResult from Phase 5A / StreamProcessor.

        Returns:
            TrackingResult for that camera.
        """
        tracker = self.get_tracker(result.camera_id)
        return tracker.update(result)

    def reset_camera(self, camera_id: str) -> None:
        """Release and remove tracker state for a specific camera."""
        if camera_id in self._camera_trackers:
            del self._camera_trackers[camera_id]

    def reset_all(self) -> None:
        """Clear all camera tracking state."""
        self._camera_trackers.clear()
