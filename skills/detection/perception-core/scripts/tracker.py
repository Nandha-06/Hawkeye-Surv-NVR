"""
ByteTrack — Lightweight multi-object tracker for the Unified Perception Pipeline.

Uses Kalman filtering for motion prediction and the Hungarian algorithm
(scipy.optimize.linear_sum_assignment) for optimal detection-to-track association.

Two-stage matching strategy:
  1. High-confidence detections are matched against all predicted tracks.
  2. Low-confidence detections are matched against remaining unmatched tracks.
This recovers occluded objects that produce low-confidence detections.

Reference: Zhang et al., "ByteTrack: Multi-Object Tracking by Associating
Every Detection Box", ECCV 2022.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional

try:
    from scipy.optimize import linear_sum_assignment
except ImportError:
    # Graceful fallback: greedy assignment (less optimal but functional)
    def linear_sum_assignment(cost_matrix):
        """Greedy assignment fallback when scipy is not available."""
        rows, cols = cost_matrix.shape
        row_indices = []
        col_indices = []
        used_cols = set()
        # Sort by minimum cost per row
        row_order = np.argsort(cost_matrix.min(axis=1))
        for r in row_order:
            if len(used_cols) >= cols:
                break
            available = [c for c in range(cols) if c not in used_cols]
            if not available:
                break
            best_c = available[np.argmin(cost_matrix[r, available])]
            row_indices.append(r)
            col_indices.append(best_c)
            used_cols.add(best_c)
        return np.array(row_indices), np.array(col_indices)


# ──────────────────────────────────────────────────────────────────────────────
# Kalman Filter (constant-velocity model)
# ──────────────────────────────────────────────────────────────────────────────

class KalmanFilter:
    """
    Simple constant-velocity Kalman filter for bounding box tracking.

    State vector (8-d): [cx, cy, w, h, vx, vy, vw, vh]
    Measurement (4-d):  [cx, cy, w, h]
    """

    def __init__(self):
        dt = 1.0  # one frame timestep

        # State transition matrix: position += velocity * dt
        self.F = np.eye(8, dtype=np.float64)
        self.F[0, 4] = dt
        self.F[1, 5] = dt
        self.F[2, 6] = dt
        self.F[3, 7] = dt

        # Measurement matrix: observe position only
        self.H = np.zeros((4, 8), dtype=np.float64)
        self.H[0, 0] = 1.0
        self.H[1, 1] = 1.0
        self.H[2, 2] = 1.0
        self.H[3, 3] = 1.0

        # Process noise covariance
        self.Q = np.eye(8, dtype=np.float64)
        self.Q[:4, :4] *= 1.0     # position process noise
        self.Q[4:, 4:] *= 0.01    # velocity process noise

        # Measurement noise covariance
        self.R = np.eye(4, dtype=np.float64) * 1.0

        # State and covariance
        self.x = np.zeros(8, dtype=np.float64)
        self.P = np.eye(8, dtype=np.float64) * 10.0

    def init(self, measurement: np.ndarray):
        """Initialize state from first measurement [cx, cy, w, h]."""
        self.x[:4] = measurement
        self.x[4:] = 0.0  # zero initial velocity
        self.P = np.eye(8, dtype=np.float64) * 10.0
        self.P[4:, 4:] *= 100.0  # high uncertainty on initial velocity

    def predict(self) -> np.ndarray:
        """Predict next state. Returns predicted [cx, cy, w, h]."""
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        # Ensure width/height stay positive
        self.x[2] = max(self.x[2], 1.0)
        self.x[3] = max(self.x[3], 1.0)
        return self.x[:4].copy()

    def update(self, measurement: np.ndarray) -> np.ndarray:
        """Update state with new measurement. Returns corrected [cx, cy, w, h]."""
        y = measurement - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        I_KH = np.eye(8) - K @ self.H
        self.P = I_KH @ self.P
        return self.x[:4].copy()

    @property
    def position(self) -> np.ndarray:
        """Current [cx, cy, w, h]."""
        return self.x[:4].copy()


# ──────────────────────────────────────────────────────────────────────────────
# Track
# ──────────────────────────────────────────────────────────────────────────────

class Track:
    """
    A single tracked object with Kalman state and identity information.

    Lifecycle: tentative → confirmed → lost → deleted
    """

    _TENTATIVE = "tentative"
    _CONFIRMED = "confirmed"
    _LOST = "lost"

    def __init__(self, track_id: int, bbox: np.ndarray, confidence: float = 0.0,
                 cls: str = "person"):
        self.track_id: int = track_id
        self.confidence: float = confidence
        self.cls: str = cls

        # Kalman filter
        self.kalman = KalmanFilter()
        cx, cy, w, h = _bbox_to_cxcywh(bbox)
        self.kalman.init(np.array([cx, cy, w, h]))
        self.bbox: np.ndarray = bbox.copy()  # [x1, y1, x2, y2]

        # Track lifecycle
        self.age: int = 0               # total frames since creation
        self.hits: int = 1              # consecutive matched frames
        self.time_since_update: int = 0  # frames since last detection match
        self.state: str = self._TENTATIVE

        # Identity (set by re-ID modules)
        self.label: Optional[str] = None
        self.face_embedding: Optional[np.ndarray] = None
        self.identity_score: float = 0.0

    def predict(self):
        """Advance Kalman state to next frame."""
        cxcywh = self.kalman.predict()
        self.bbox = _cxcywh_to_bbox(cxcywh)
        self.age += 1
        self.time_since_update += 1

    def update(self, bbox: np.ndarray, confidence: float):
        """Match this track with a new detection."""
        cx, cy, w, h = _bbox_to_cxcywh(bbox)
        self.kalman.update(np.array([cx, cy, w, h]))
        self.bbox = bbox.copy()
        self.confidence = confidence
        self.hits += 1
        self.time_since_update = 0

        # State transitions
        if self.state == self._TENTATIVE and self.hits >= 3:
            self.state = self._CONFIRMED
        elif self.state == self._LOST:
            self.state = self._CONFIRMED

    def mark_lost(self):
        """Mark track as lost (no matching detection this frame)."""
        if self.state == self._CONFIRMED:
            self.state = self._LOST
        self.hits = 0

    @property
    def is_confirmed(self) -> bool:
        return self.state == self._CONFIRMED

    @property
    def is_deleted(self) -> bool:
        """Should this track be removed from the tracker?"""
        if self.state == self._TENTATIVE and self.time_since_update > 2:
            return True
        if self.state == self._LOST and self.time_since_update > 30:
            return True
        return False

    @property
    def center(self) -> np.ndarray:
        """Bounding box center [cx, cy]."""
        return np.array([
            (self.bbox[0] + self.bbox[2]) / 2,
            (self.bbox[1] + self.bbox[3]) / 2,
        ])

    @property
    def width(self) -> float:
        return max(0, self.bbox[2] - self.bbox[0])

    @property
    def height(self) -> float:
        return max(0, self.bbox[3] - self.bbox[1])


# ──────────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ──────────────────────────────────────────────────────────────────────────────

def _bbox_to_cxcywh(bbox: np.ndarray) -> tuple:
    """Convert [x1, y1, x2, y2] to (cx, cy, w, h)."""
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return cx, cy, w, h


def _cxcywh_to_bbox(cxcywh: np.ndarray) -> np.ndarray:
    """Convert [cx, cy, w, h] to [x1, y1, x2, y2]."""
    cx, cy, w, h = cxcywh
    return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])


def compute_iou_matrix(bboxes_a: np.ndarray, bboxes_b: np.ndarray) -> np.ndarray:
    """
    Compute IoU between two sets of [x1, y1, x2, y2] bounding boxes.

    Args:
        bboxes_a: (M, 4)
        bboxes_b: (N, 4)

    Returns:
        iou_matrix: (M, N)
    """
    m = len(bboxes_a)
    n = len(bboxes_b)
    if m == 0 or n == 0:
        return np.zeros((m, n), dtype=np.float64)

    a = bboxes_a[:, np.newaxis, :]  # (M, 1, 4)
    b = bboxes_b[np.newaxis, :, :]  # (1, N, 4)

    xx1 = np.maximum(a[..., 0], b[..., 0])
    yy1 = np.maximum(a[..., 1], b[..., 1])
    xx2 = np.minimum(a[..., 2], b[..., 2])
    yy2 = np.minimum(a[..., 3], b[..., 3])

    inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
    area_a = (a[..., 2] - a[..., 0]) * (a[..., 3] - a[..., 1])
    area_b = (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])
    union = area_a + area_b - inter

    return np.where(union > 0, inter / union, 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# ByteTracker
# ──────────────────────────────────────────────────────────────────────────────

class ByteTracker:
    """
    ByteTrack multi-object tracker.

    Two-stage association:
      1. Match high-confidence detections to all predicted tracks (IoU).
      2. Match low-confidence detections to remaining unmatched tracks.

    Args:
        max_lost:       Max frames a track can be lost before deletion.
        min_hits:       Min consecutive hits to confirm a tentative track.
        high_threshold: Confidence threshold separating high/low detections.
        iou_threshold:  Minimum IoU for a valid match.
    """

    def __init__(self, max_lost: int = 30, min_hits: int = 3,
                 high_threshold: float = 0.5, iou_threshold: float = 0.3):
        self._next_id = 1
        self.tracks: list[Track] = []
        self.max_lost = max_lost
        self.min_hits = min_hits
        self.high_threshold = high_threshold
        self.iou_threshold = iou_threshold

    def update(self, detections: list[dict]) -> list[Track]:
        """
        Process a new frame's detections and return active tracks.

        Args:
            detections: list of dicts with keys:
                - "bbox": [x1, y1, x2, y2]
                - "confidence": float
                - "class": str (only "person" detections should be passed)

        Returns:
            List of confirmed Track objects with updated bounding boxes.
        """
        # 1. Predict all existing tracks forward
        for track in self.tracks:
            track.predict()

        if not detections:
            # No detections: mark all tracks, prune deleted
            for track in self.tracks:
                track.mark_lost()
            self.tracks = [t for t in self.tracks if not t.is_deleted]
            return [t for t in self.tracks if t.is_confirmed]

        # 2. Split detections by confidence
        det_bboxes = np.array([d["bbox"] for d in detections], dtype=np.float64)
        det_confs = np.array([d["confidence"] for d in detections], dtype=np.float64)

        high_mask = det_confs >= self.high_threshold
        high_indices = np.where(high_mask)[0]
        low_indices = np.where(~high_mask)[0]

        # 3. First association: high-confidence detections vs all tracks
        matched_tracks = set()
        matched_dets = set()

        if len(self.tracks) > 0 and len(high_indices) > 0:
            track_bboxes = np.array([t.bbox for t in self.tracks], dtype=np.float64)
            high_bboxes = det_bboxes[high_indices]

            iou_matrix = compute_iou_matrix(track_bboxes, high_bboxes)
            cost_matrix = 1.0 - iou_matrix

            if cost_matrix.size > 0:
                row_idx, col_idx = linear_sum_assignment(cost_matrix)

                for r, c in zip(row_idx, col_idx):
                    if iou_matrix[r, c] >= self.iou_threshold:
                        det_i = high_indices[c]
                        self.tracks[r].update(det_bboxes[det_i], det_confs[det_i])
                        matched_tracks.add(r)
                        matched_dets.add(det_i)

        # 4. Second association: low-confidence detections vs unmatched tracks
        unmatched_track_indices = [i for i in range(len(self.tracks))
                                   if i not in matched_tracks]

        if len(unmatched_track_indices) > 0 and len(low_indices) > 0:
            remaining_track_bboxes = np.array(
                [self.tracks[i].bbox for i in unmatched_track_indices],
                dtype=np.float64
            )
            low_bboxes = det_bboxes[low_indices]
            iou_matrix_2 = compute_iou_matrix(remaining_track_bboxes, low_bboxes)
            cost_matrix_2 = 1.0 - iou_matrix_2

            if cost_matrix_2.size > 0:
                row_idx_2, col_idx_2 = linear_sum_assignment(cost_matrix_2)

                for r, c in zip(row_idx_2, col_idx_2):
                    if iou_matrix_2[r, c] >= self.iou_threshold:
                        track_i = unmatched_track_indices[r]
                        det_i = low_indices[c]
                        self.tracks[track_i].update(det_bboxes[det_i], det_confs[det_i])
                        matched_tracks.add(track_i)
                        matched_dets.add(det_i)

        # 5. Mark unmatched tracks as lost
        for i in range(len(self.tracks)):
            if i not in matched_tracks:
                self.tracks[i].mark_lost()

        # 6. Create new tracks from unmatched high-confidence detections
        for i in high_indices:
            if i not in matched_dets:
                new_track = Track(
                    track_id=self._next_id,
                    bbox=det_bboxes[i],
                    confidence=det_confs[i],
                    cls=detections[i].get("class", "person"),
                )
                self._next_id += 1
                self.tracks.append(new_track)

        # 7. Prune deleted tracks
        self.tracks = [t for t in self.tracks if not t.is_deleted]

        # 8. Return confirmed tracks
        return [t for t in self.tracks if t.is_confirmed]

    def get_all_tracks(self) -> list[Track]:
        """Return all non-deleted tracks (including tentative)."""
        return [t for t in self.tracks if not t.is_deleted]
