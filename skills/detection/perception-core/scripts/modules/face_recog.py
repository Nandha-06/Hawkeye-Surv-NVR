"""
Face Recognition Module — SCRFD face detector + MobileFaceNet embedder.

Detects faces within padded person crops (not full frame) using SCRFD,
aligns each face to 112x112 via landmark-based affine transform, and
extracts 128-d MobileFaceNet embeddings.

Handles overlapping person boxes by associating each detected face
with the nearest tracked person center.

Ported from skills/detection/face-recognition/scripts/detect.py.
"""

import sys
import time
import numpy as np
import cv2
import onnxruntime
from pathlib import Path
from typing import Optional

from modules.base import PerceptionModule, ModuleResult


def _log(msg: str):
    print(f"[FaceRecog] {msg}", file=sys.stderr, flush=True)


# ──────────────────────────────────────────────────────────────────────────────
# SCRFD constants & helpers
# ──────────────────────────────────────────────────────────────────────────────

SCRFD_INPUT_SIZE = (640, 640)
SCRFD_FEAT_STRIDE = [8, 16, 32]
SCRFD_NUM_ANCHORS = 2

# MobileFaceNet constants
MFNET_INPUT_SIZE = (112, 112)

# ArcFace-style 5-point reference landmarks for 112x112 alignment
ARCFACE_REF_POINTS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)

# Person crop padding ratio (15% on each side to capture full head)
CROP_PAD_RATIO = 0.15


def _distance2bbox(points, distance):
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _distance2kps(points, distance):
    num_points = distance.shape[1] // 2
    result = np.zeros((distance.shape[0], num_points, 2), dtype=np.float32)
    for i in range(num_points):
        result[:, i, 0] = points[:, 0] + distance[:, 2 * i]
        result[:, i, 1] = points[:, 1] + distance[:, 2 * i + 1]
    return result


def _generate_anchor_centers(height, width, stride):
    anchor_centers = np.stack(
        np.mgrid[:height, :width][::-1], axis=-1
    ).astype(np.float32)
    anchor_centers = (anchor_centers * stride).reshape(-1, 2)
    if SCRFD_NUM_ANCHORS > 1:
        anchor_centers = np.stack(
            [anchor_centers] * SCRFD_NUM_ANCHORS, axis=1
        ).reshape(-1, 2)
    return anchor_centers


def _nms(dets, threshold=0.4):
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(ovr <= threshold)[0]
        order = order[inds + 1]
    return keep


def _align_face(img_bgr: np.ndarray, landmarks_5x2: np.ndarray) -> Optional[np.ndarray]:
    """Warp face to canonical 112x112 alignment using similarity transform."""
    src_pts = landmarks_5x2.astype(np.float32)
    M = cv2.estimateAffinePartial2D(src_pts, ARCFACE_REF_POINTS)[0]
    if M is None:
        return None
    return cv2.warpAffine(img_bgr, M, MFNET_INPUT_SIZE, borderValue=(0, 0, 0))


# ──────────────────────────────────────────────────────────────────────────────
# SCRFD Face Detector
# ──────────────────────────────────────────────────────────────────────────────

class _SCRFDDetector:
    """SCRFD face detection via ONNX Runtime."""

    def __init__(self, model_path: str, providers: list):
        _log(f"Loading SCRFD from {model_path}...")
        t0 = time.perf_counter()
        self.session = onnxruntime.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.load_ms = (time.perf_counter() - t0) * 1000
        _log(f"SCRFD loaded in {self.load_ms:.1f}ms")

        # Pre-compute anchor centers for 640x640 input
        self._anchor_cache = {}
        for stride in SCRFD_FEAT_STRIDE:
            h = SCRFD_INPUT_SIZE[1] // stride
            w = SCRFD_INPUT_SIZE[0] // stride
            self._anchor_cache[stride] = _generate_anchor_centers(h, w, stride)

    def detect(self, img_bgr: np.ndarray, threshold: float = 0.5,
               nms_thresh: float = 0.4):
        """
        Detect faces in a BGR image.
        Returns: list of (bbox_xyxy, confidence, landmarks_5x2)
        """
        h_orig, w_orig = img_bgr.shape[:2]
        input_h, input_w = SCRFD_INPUT_SIZE

        # Letterbox resize
        scale = min(input_w / w_orig, input_h / h_orig)
        new_w, new_h = int(w_orig * scale), int(h_orig * scale)
        resized = cv2.resize(img_bgr, (new_w, new_h))

        det_img = np.zeros((input_h, input_w, 3), dtype=np.uint8)
        det_img[:new_h, :new_w, :] = resized

        # Convert BGR to RGB for SCRFD face detector
        det_img_rgb = cv2.cvtColor(det_img, cv2.COLOR_BGR2RGB)
        blob = (det_img_rgb.astype(np.float32) - 127.5) / 128.0
        blob = blob.transpose(2, 0, 1)[np.newaxis]

        outputs = self.session.run(None, {self.input_name: blob})

        scores_list, bboxes_list, kpss_list = [], [], []
        fmc = len(SCRFD_FEAT_STRIDE)

        for idx, stride in enumerate(SCRFD_FEAT_STRIDE):
            scores = outputs[idx][0].flatten()
            bbox_preds = outputs[idx + fmc][0]
            kps_preds = outputs[idx + fmc * 2][0]

            pos_inds = np.where(scores >= threshold)[0]
            if len(pos_inds) == 0:
                continue

            anchor_centers = self._anchor_cache[stride]
            pos_scores = scores[pos_inds]
            pos_anchors = anchor_centers[pos_inds]

            bboxes = _distance2bbox(pos_anchors, bbox_preds[pos_inds] * stride)
            kpss = _distance2kps(pos_anchors, kps_preds[pos_inds] * stride)

            scores_list.append(pos_scores)
            bboxes_list.append(bboxes)
            kpss_list.append(kpss)

        if not scores_list:
            return []

        scores_all = np.concatenate(scores_list)
        bboxes_all = np.concatenate(bboxes_list)
        kpss_all = np.concatenate(kpss_list)

        dets = np.hstack((bboxes_all, scores_all[:, np.newaxis]))
        keep = _nms(dets, threshold=nms_thresh)

        results = []
        for k in keep:
            bbox = (bboxes_all[k] / scale).astype(int).tolist()
            results.append((bbox, float(scores_all[k]), kpss_all[k] / scale))

        return results


# ──────────────────────────────────────────────────────────────────────────────
# MobileFaceNet Embedder
# ──────────────────────────────────────────────────────────────────────────────

class _MobileFaceNetEmbedder:
    """MobileFaceNet 128-d face embedding via ONNX Runtime."""

    def __init__(self, model_path: str, providers: list):
        _log(f"Loading MobileFaceNet from {model_path}...")
        t0 = time.perf_counter()
        self.session = onnxruntime.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.load_ms = (time.perf_counter() - t0) * 1000
        _log(f"MobileFaceNet loaded in {self.load_ms:.1f}ms")

    def extract(self, face_bgr_112: np.ndarray) -> np.ndarray:
        """Extract normalized 128-d embedding from 112x112 BGR face."""
        face_rgb = face_bgr_112[:, :, ::-1].copy()
        blob = (face_rgb.astype(np.float32) - 127.5) / 128.0
        blob = blob.transpose(2, 0, 1)[np.newaxis]
        feat = self.session.run(None, {self.input_name: blob})[0]
        embedding = feat[0]
        return embedding / (np.linalg.norm(embedding) + 1e-12)


# ──────────────────────────────────────────────────────────────────────────────
# Face Recognition Module
# ──────────────────────────────────────────────────────────────────────────────

class FaceRecogModule(PerceptionModule):
    """
    Face recognition using SCRFD (detection) + MobileFaceNet (embedding).

    Key optimization: runs SCRFD on padded person crops, NOT the full frame.
    A 200x400 person crop is much faster to scan than 1920x1080.

    Handles overlapping persons by checking if the detected face center
    is inside the original (unpadded) person bounding box.
    """

    EMBEDDING_DIM = 128

    def __init__(self, scrfd_path: str, mfnet_path: str,
                 min_person_width: int = 80,
                 min_confidence: float = 0.6,
                 reid_every_n: int = 5,
                 det_threshold: float = 0.50):
        self.scrfd_path = scrfd_path
        self.mfnet_path = mfnet_path
        self.min_person_width = min_person_width
        self.min_confidence = min_confidence
        self.reid_every_n = reid_every_n
        self.det_threshold = det_threshold

        self.detector: Optional[_SCRFDDetector] = None
        self.embedder: Optional[_MobileFaceNetEmbedder] = None
        self.load_ms: float = 0.0

    @property
    def name(self) -> str:
        return "Face Recognition (SCRFD+MobileFaceNet)"

    def load(self, providers: list) -> None:
        """Load SCRFD and MobileFaceNet ONNX models."""
        for path, label in [(self.scrfd_path, "SCRFD"), (self.mfnet_path, "MobileFaceNet")]:
            if not Path(path).exists():
                raise FileNotFoundError(
                    f"{label} model not found at {path}. Run the deploy script."
                )

        self.detector = _SCRFDDetector(self.scrfd_path, providers)
        self.embedder = _MobileFaceNetEmbedder(self.mfnet_path, providers)
        self.load_ms = self.detector.load_ms + self.embedder.load_ms

    def should_run(self, track, frame_shape: tuple) -> bool:
        """Gating: skip tiny persons, low confidence, and non-refresh frames."""
        if track.width < self.min_person_width:
            return False
        if track.confidence < self.min_confidence:
            return False
        if track.label is None:
            return True
        return track.age % self.reid_every_n == 0

    def process(self, track, frame: np.ndarray) -> ModuleResult:
        """
        Detect face in padded person crop, align, and extract embedding.

        The person bbox is padded by CROP_PAD_RATIO (15%) on each side
        to ensure full head coverage despite tight YOLO boxes.
        """
        if self.detector is None or self.embedder is None:
            return ModuleResult(modality="face")

        h_img, w_img = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in track.bbox]
        pw = x2 - x1
        ph = y2 - y1

        # Apply padding to capture full head
        pad_x = int(pw * CROP_PAD_RATIO)
        pad_y = int(ph * CROP_PAD_RATIO)
        cx1 = max(0, x1 - pad_x)
        cy1 = max(0, y1 - pad_y)
        cx2 = min(w_img, x2 + pad_x)
        cy2 = min(h_img, y2 + pad_y)

        person_crop = frame[cy1:cy2, cx1:cx2]
        if person_crop.size == 0:
            return ModuleResult(modality="face")

        try:
            # Detect faces in the padded person crop
            face_detections = self.detector.detect(
                person_crop, threshold=self.det_threshold
            )

            if not face_detections:
                return ModuleResult(modality="face")

            # Find the best face that actually belongs to this person
            # (handles overlapping person boxes)
            person_center = track.center  # [cx, cy] in full-frame coords
            best_face = None
            best_dist = float("inf")

            for face_bbox, face_conf, face_kps in face_detections:
                # Convert face center from crop coords to full-frame coords
                face_cx = (face_bbox[0] + face_bbox[2]) / 2 + cx1
                face_cy = (face_bbox[1] + face_bbox[3]) / 2 + cy1

                # Check if face center is inside the upper part of the padded person box
                # Head is typically in the upper 50% of the body, allowing it to go up to cy1
                if not (cx1 <= face_cx <= cx2 and cy1 <= face_cy <= y1 + ph * 0.5):
                    continue  # Face belongs to a different person or is in the lower body

                dist = np.sqrt(
                    (face_cx - person_center[0]) ** 2 +
                    (face_cy - person_center[1]) ** 2
                )
                if dist < best_dist:
                    best_dist = dist
                    best_face = (face_bbox, face_conf, face_kps)

            if best_face is None:
                return ModuleResult(modality="face")

            face_bbox, face_conf, face_kps = best_face

            # Align face using landmarks (in crop coordinates)
            face_chip = _align_face(person_crop, face_kps)

            if face_chip is None:
                # Fallback: simple crop + resize
                fb = face_bbox
                fx1, fy1 = max(0, fb[0]), max(0, fb[1])
                fx2 = min(person_crop.shape[1], fb[2])
                fy2 = min(person_crop.shape[0], fb[3])
                face_crop = person_crop[fy1:fy2, fx1:fx2]
                if face_crop.size == 0:
                    return ModuleResult(modality="face")
                face_chip = cv2.resize(face_crop, MFNET_INPUT_SIZE)

            # Extract face embedding
            embedding = self.embedder.extract(face_chip)

            return ModuleResult(
                embedding=embedding,
                modality="face",
                crop=face_chip,
            )

        except Exception as e:
            _log(f"Face recognition failed: {e}")
            return ModuleResult(modality="face")
