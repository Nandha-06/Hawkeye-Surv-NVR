#!/usr/bin/env python3
"""
RF-DETR detection and instance segmentation skill for Hawkeye.

The skill keeps the existing Hawkeye detection protocol and emits bounding boxes
for every object. When segmentation is enabled, each object may also include:
  segmentation: {type: "polygon", points: [[x, y], ...], area: int}
"""

import argparse
import base64
import json
import os
import signal
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Allowed roots for frame_path values received on stdin. Only paths inside
# these roots are accepted; everything else is rejected to prevent
# arbitrary-file-read / exfil primitives via crafted messages.
_ALLOWED_FRAME_ROOTS = [
    Path(".temp").resolve(),
    Path(".data/snapshots").resolve(),
    Path(".data/frames").resolve(),
    Path("/tmp").resolve(),
]

def _is_safe_frame_path(p) -> bool:
    if not p:
        return False
    if not isinstance(p, (str, bytes, os.PathLike)):
        return False
    s = os.fsdecode(p)
    if "\x00" in s or "\n" in s or "\r" in s:
        return False
    lowered = s.replace("\\", "/")
    if lowered.startswith("/") or lowered.startswith("~"):
        return False
    if "/.." in lowered or lowered.startswith("../") or ".." in Path(s).parts:
        return False
    try:
        candidate = Path(s).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    for root in _ALLOWED_FRAME_ROOTS:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            continue
    return False

# Whitelist of allowed Hugging Face model namespaces. Loading from
# arbitrary HF repos fetches AND executes model code, so only known
# publishers are accepted.
_ALLOWED_MODEL_NAMESPACES = (
    "Roboflow/",
)

def _is_allowed_model_id(model_id: str) -> bool:
    if not model_id or not isinstance(model_id, str):
        return False
    if len(model_id) > 128 or "\x00" in model_id or "\n" in model_id:
        return False
    return any(model_id.startswith(ns) for ns in _ALLOWED_MODEL_NAMESPACES)

# ── Import shared protocol from skills/lib/ ────────────────────────────────
_script_dir = Path(__file__).resolve().parent
_protocol_loaded = False
for _lib_path in [
    _script_dir,                                  # local override
    _script_dir.parent / "lib",                   # <skill>/lib/
    _script_dir.parent.parent / "lib",            # <category>/lib/
    _script_dir.parent.parent.parent / "lib",     # skills/lib/ (canonical)
]:
    if (_lib_path / "protocol.py").exists():
        sys.path.insert(0, str(_lib_path))
        from protocol import emit, log, setup_log_prefix, event_error, event_ready, event_progress, event_live_frame  # noqa: E402
        setup_log_prefix("RF-DETR")
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)
    def log(msg):
        print(f"[RF-DETR] {msg}", file=sys.stderr, flush=True)


DETECTION_MODELS = {
    "nano": "Roboflow/rf-detr-nano",
    "small": "Roboflow/rf-detr-small",
    "medium": "Roboflow/rf-detr-medium",
    "large": "Roboflow/rf-detr-large",
    "base": "Roboflow/rf-detr-base",
    "base2": "Roboflow/rf-detr-base-2",
}

SEGMENTATION_MODELS = {
    "preview": "Roboflow/rf-detr-seg-preview",
    "nano": "Roboflow/rf-detr-seg-nano",
    "small": "Roboflow/rf-detr-seg-small",
    "medium": "Roboflow/rf-detr-seg-medium",
    "large": "Roboflow/rf-detr-seg-large",
    "xlarge": "Roboflow/rf-detr-seg-xlarge",
    "xxlarge": "Roboflow/rf-detr-seg-xxlarge",
    "segmentation": "Roboflow/rf-detr-segmentation",
}

PERF_STATS_INTERVAL = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RF-DETR detection and segmentation")
    parser.add_argument("--config", type=str, default="")
    parser.add_argument("--model-size", type=str, default="nano")
    parser.add_argument("--seg-model-size", type=str, default="")
    parser.add_argument("--task-mode", choices=["detection", "segmentation", "both"], default="both")
    parser.add_argument("--confidence", type=float, default=0.55)
    parser.add_argument("--mask-threshold", type=float, default=0.50)
    parser.add_argument("--classes", type=str, default="person,car,dog,cat")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda", "mps"], default="auto")
    parser.add_argument("--fps", type=float, default=3)
    parser.add_argument("--half-precision", action="store_true")
    parser.add_argument("--torch-compile", action="store_true")
    parser.add_argument("--enable-motion-gating", action="store_true")
    parser.add_argument("--motion-threshold", type=int, default=15)
    parser.add_argument("--min-motion-area", type=int, default=500)
    return parser.parse_args()


def load_config(args: argparse.Namespace) -> Dict[str, Any]:
    env_params = os.environ.get("HAWKEYE_SKILL_PARAMS")
    if env_params:
        try:
            return json.loads(env_params)
        except json.JSONDecodeError:
            pass

    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                return json.load(f)

    return {
        "model_size": args.model_size,
        "seg_model_size": args.seg_model_size or args.model_size,
        "task_mode": args.task_mode,
        "confidence": args.confidence,
        "mask_threshold": args.mask_threshold,
        "classes": [c.strip() for c in args.classes.split(",") if c.strip()],
        "device": args.device,
        "fps": args.fps,
        "half_precision": args.half_precision,
        "torch_compile": args.torch_compile,
        "enable_motion_gating": args.enable_motion_gating,
        "motion_threshold": args.motion_threshold,
        "min_motion_area": args.min_motion_area,
    }


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def normalize_classes(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [c.strip() for c in value.split(",") if c.strip()]
    return [str(c).strip() for c in value if str(c).strip()]


def choose_device(requested: str):
    import torch

    if requested == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    if requested == "mps" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    if requested == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def id_to_label(id2label: Dict[Any, str], label_id: Any) -> str:
    key = int(label_id)
    return id2label.get(key) or id2label.get(str(key)) or str(key)


def clamp_box(box: Iterable[float], width: int, height: int) -> List[int]:
    x1, y1, x2, y2 = [float(v) for v in box]
    return [
        max(0, min(width, int(round(x1)))),
        max(0, min(height, int(round(y1)))),
        max(0, min(width, int(round(x2)))),
        max(0, min(height, int(round(y2)))),
    ]


def mask_to_polygon(mask: Any, min_points: int = 4) -> Tuple[Optional[List[List[int]]], int]:
    import cv2
    import numpy as np
    import torch

    if isinstance(mask, torch.Tensor):
        mask = mask.detach().cpu().numpy()
    mask_u8 = (np.asarray(mask) > 0).astype("uint8")
    area = int(mask_u8.sum())
    if area <= 0:
        return None, 0

    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, area

    contour = max(contours, key=cv2.contourArea)
    epsilon = max(1.0, 0.005 * cv2.arcLength(contour, True))
    approx = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
    if len(approx) < min_points:
        x, y, w, h = cv2.boundingRect(contour)
        approx = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    return [[int(x), int(y)] for x, y in approx.tolist()], area


def mask_to_box(mask: Any, width: int, height: int) -> List[int]:
    import numpy as np
    import torch

    if isinstance(mask, torch.Tensor):
        mask = mask.detach().cpu().numpy()
    ys, xs = np.where(np.asarray(mask) > 0)
    if xs.size == 0 or ys.size == 0:
        return [0, 0, 0, 0]
    return clamp_box([xs.min(), ys.min(), xs.max(), ys.max()], width, height)


def is_point_in_zones(px: int, py: int, width: int, height: int, alert_zones: List[Any]) -> bool:
    if not alert_zones:
        return False
    import cv2
    import numpy as np

    for zone in alert_zones:
        try:
            pts = np.array([[int(pt[0] * width), int(pt[1] * height)] for pt in zone], dtype=np.int32)
            if cv2.pointPolygonTest(pts, (px, py), False) >= 0:
                return True
        except Exception as exc:
            log(f"Alert zone parse error: {exc}")
    return False


class PerfTracker:
    def __init__(self, interval: int = PERF_STATS_INTERVAL) -> None:
        self.interval = interval
        self.total_frames = 0
        self.window_frames = 0
        self.errors = 0
        self.model_load_ms = 0.0
        self.timings = {"file_read": [], "inference": [], "postprocess": [], "emit": [], "total": []}

    def record(self, stage: str, ms: float) -> None:
        if stage in self.timings:
            self.timings[stage].append(ms)

    def record_frame(self) -> None:
        self.total_frames += 1
        self.window_frames += 1
        if self.window_frames >= self.interval:
            self.emit_stats()

    def emit_stats(self) -> None:
        stats = {
            "event": "perf_stats",
            "total_frames": self.total_frames,
            "errors": self.errors,
            "model_load_ms": round(self.model_load_ms, 1),
            "timings_ms": {},
        }
        for stage, values in self.timings.items():
            if not values:
                continue
            sorted_values = sorted(values)
            n = len(sorted_values)
            stats["timings_ms"][stage] = {
                "avg": round(sum(sorted_values) / n, 2),
                "p50": round(sorted_values[n // 2], 2),
                "p95": round(sorted_values[min(n - 1, int(n * 0.95))], 2),
            }
            values.clear()
        self.window_frames = 0
        emit(stats)

    def emit_final(self) -> None:
        if any(self.timings.values()):
            self.emit_stats()


class MotionDetector:
    def __init__(self, threshold: int, min_area: int, motion_masks: List[Any]) -> None:
        self.threshold = threshold
        self.min_area = min_area
        self.motion_masks = motion_masks
        self.avg_frame = None
        self.mask_img = None

    def has_motion(self, frame_bgr) -> bool:
        import cv2
        import numpy as np

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if self.mask_img is None:
            h, w = frame_bgr.shape[:2]
            self.mask_img = np.ones((h, w), dtype=np.uint8) * 255
            for poly in self.motion_masks:
                try:
                    pts = np.array([[int(pt[0] * w), int(pt[1] * h)] for pt in poly], dtype=np.int32)
                    cv2.fillPoly(self.mask_img, [pts], 0)
                except Exception as exc:
                    log(f"Motion mask parse error: {exc}")

        cv2.bitwise_and(gray, self.mask_img, dst=gray)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        if self.avg_frame is None:
            self.avg_frame = gray.copy().astype("float")
            return False

        cv2.accumulateWeighted(gray, self.avg_frame, 0.5)
        frame_delta = cv2.absdiff(gray, cv2.convertScaleAbs(self.avg_frame))
        thresh = cv2.threshold(frame_delta, self.threshold, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return any(cv2.contourArea(contour) >= self.min_area for contour in contours)


class RfDetrRunner:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.task_mode = config.get("task_mode", "both")
        if self.task_mode not in ("detection", "segmentation", "both"):
            self.task_mode = "both"

        self.confidence = float(config.get("confidence", 0.55))
        self.mask_threshold = float(config.get("mask_threshold", 0.50))
        self.target_classes = normalize_classes(config.get("classes", []))
        self.alert_zones = config.get("alert_zones", [])
        self.device = None
        self.processor = None
        self.model = None
        self.model_id = ""
        self.model_kind = "segmentation" if self.task_mode in ("segmentation", "both") else "detection"

    def resolve_model_id(self) -> str:
        custom_model_id = str(self.config.get("custom_model_id") or "").strip()
        if custom_model_id:
            if not _is_allowed_model_id(custom_model_id):
                raise ValueError(
                    f"custom_model_id '{custom_model_id}' is not in the allow-list "
                    f"(allowed prefixes: {', '.join(_ALLOWED_MODEL_NAMESPACES)})"
                )
            return custom_model_id

        if self.model_kind == "segmentation":
            seg_size = str(self.config.get("seg_model_size") or self.config.get("model_size") or "nano")
            return SEGMENTATION_MODELS.get(seg_size, SEGMENTATION_MODELS["nano"])

        det_size = str(self.config.get("model_size") or "nano")
        return DETECTION_MODELS.get(det_size, DETECTION_MODELS["nano"])

    def load(self) -> float:
        import torch
        from transformers import AutoImageProcessor

        t0 = time.perf_counter()
        self.device = choose_device(str(self.config.get("device", "auto")))
        self.model_id = self.resolve_model_id()

        emit({"event": "progress", "stage": "model", "message": f"Loading {self.model_id} on {self.device}..."})
        self.processor = AutoImageProcessor.from_pretrained(self.model_id)

        dtype = None
        use_half = as_bool(self.config.get("half_precision"), default=False) and self.device.type == "cuda"
        if use_half:
            dtype = torch.float16

        def load_pretrained(model_cls):
            if dtype is None:
                return model_cls.from_pretrained(self.model_id)
            try:
                return model_cls.from_pretrained(self.model_id, dtype=dtype)
            except TypeError:
                return model_cls.from_pretrained(self.model_id, torch_dtype=dtype)

        if self.model_kind == "segmentation":
            from transformers import RfDetrForInstanceSegmentation

            self.model = load_pretrained(RfDetrForInstanceSegmentation)
        else:
            from transformers import AutoModelForObjectDetection

            self.model = load_pretrained(AutoModelForObjectDetection)

        self.model.to(self.device)
        self.model.eval()

        if as_bool(self.config.get("torch_compile"), default=False) and hasattr(torch, "compile") and self.device.type == "cuda":
            emit({"event": "progress", "stage": "model", "message": "Compiling model with torch.compile..."})
            self.model = torch.compile(self.model, mode="reduce-overhead")

        load_ms = (time.perf_counter() - t0) * 1000
        return load_ms

    def infer(self, frame_bgr) -> List[Dict[str, Any]]:
        import cv2
        import torch
        from PIL import Image

        h, w = frame_bgr.shape[:2]
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(frame_rgb)

        inputs = self.processor(images=image, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        if next(self.model.parameters()).dtype == torch.float16 and "pixel_values" in inputs:
            inputs["pixel_values"] = inputs["pixel_values"].half()

        with torch.inference_mode():
            outputs = self.model(**inputs)

        target_sizes = torch.tensor([[h, w]], device=self.device)
        if self.model_kind == "segmentation":
            return self._postprocess_segmentation(outputs, target_sizes, w, h)
        return self._postprocess_detection(outputs, target_sizes, w, h)

    def _keep_class(self, class_name: str) -> bool:
        return not self.target_classes or class_name in self.target_classes

    def _with_alert_zone(self, obj: Dict[str, Any], width: int, height: int) -> Dict[str, Any]:
        x1, y1, x2, y2 = obj["bbox"]
        px = int((x1 + x2) / 2)
        py = int(y2)
        obj["in_alert_zone"] = is_point_in_zones(px, py, width, height, self.alert_zones)
        return obj

    def _postprocess_detection(self, outputs: Any, target_sizes: Any, width: int, height: int) -> List[Dict[str, Any]]:
        results = self.processor.post_process_object_detection(
            outputs, target_sizes=target_sizes, threshold=self.confidence
        )[0]
        objects = []
        for score, label, box in zip(results["scores"], results["labels"], results["boxes"]):
            class_name = id_to_label(self.model.config.id2label, label.item())
            if not self._keep_class(class_name):
                continue
            obj = {
                "class": class_name,
                "confidence": round(float(score.item()), 3),
                "bbox": clamp_box(box.tolist(), width, height),
                "source": "rf-detr",
            }
            objects.append(self._with_alert_zone(obj, width, height))
        return objects

    def _postprocess_segmentation(self, outputs: Any, target_sizes: Any, width: int, height: int) -> List[Dict[str, Any]]:
        try:
            results = self.processor.post_process_instance_segmentation(
                outputs,
                threshold=self.confidence,
                mask_threshold=self.mask_threshold,
                target_sizes=target_sizes,
                return_binary_maps=True,
            )[0]
            objects = self._objects_from_instance_results(results, width, height)
            if objects:
                return objects
        except Exception as exc:
            log(f"Segmentation postprocess fell back to boxes: {exc}")

        return self._postprocess_detection(outputs, target_sizes, width, height)

    def _objects_from_instance_results(self, results: Dict[str, Any], width: int, height: int) -> List[Dict[str, Any]]:
        import torch

        objects = []
        masks = results.get("masks")
        if masks is None:
            segmentation = results.get("segmentation")
            if isinstance(segmentation, torch.Tensor) and segmentation.ndim == 3:
                masks = segmentation

        if masks is not None:
            labels = results.get("labels")
            scores = results.get("scores")
            boxes = results.get("boxes")
            for idx, mask in enumerate(masks):
                label_id = labels[idx].item() if labels is not None else -1
                class_name = id_to_label(self.model.config.id2label, label_id)
                if not self._keep_class(class_name):
                    continue
                score = float(scores[idx].item()) if scores is not None else self.confidence
                polygon, area = mask_to_polygon(mask)
                bbox = clamp_box(boxes[idx].tolist(), width, height) if boxes is not None else mask_to_box(mask, width, height)
                obj = {
                    "class": class_name,
                    "confidence": round(score, 3),
                    "bbox": bbox,
                    "source": "rf-detr-seg",
                }
                if polygon:
                    obj["segmentation"] = {"type": "polygon", "points": polygon, "area": area}
                objects.append(self._with_alert_zone(obj, width, height))
            return objects

        segmentation = results.get("segmentation")
        segments_info = results.get("segments_info") or []
        if segmentation is None or not segments_info:
            return []

        for segment in segments_info:
            label_id = segment.get("label_id", segment.get("label", -1))
            class_name = id_to_label(self.model.config.id2label, label_id)
            if not self._keep_class(class_name):
                continue
            segment_id = segment.get("id")
            if segment_id is None:
                continue
            mask = segmentation == segment_id
            polygon, area = mask_to_polygon(mask)
            obj = {
                "class": class_name,
                "confidence": round(float(segment.get("score", self.confidence)), 3),
                "bbox": mask_to_box(mask, width, height),
                "source": "rf-detr-seg",
            }
            if polygon:
                obj["segmentation"] = {"type": "polygon", "points": polygon, "area": area}
            objects.append(self._with_alert_zone(obj, width, height))
        return objects


def encode_frame(frame_bgr) -> Optional[str]:
    import cv2

    ok, jpeg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode("ascii")


class StreamReader:
    def __init__(self, url: str) -> None:
        self.url = url
        self.cap = None
        self.ret = False
        self.frame = None
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()

    def _update(self) -> None:
        import cv2

        while self.running:
            self.cap = cv2.VideoCapture(self.url)
            if not self.cap.isOpened():
                log(f"Could not open stream {self.url}; retrying")
                time.sleep(5)
                continue
            is_file = not str(self.url).startswith(("rtsp://", "http://", "https://"))
            while self.running and self.cap.isOpened():
                ret, frame = self.cap.read()
                if not ret or frame is None:
                    if is_file:
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        time.sleep(0.03)
                        continue
                    break
                with self.lock:
                    self.ret = True
                    self.frame = frame.copy()
                time.sleep(0.002)
            self.cap.release()
            with self.lock:
                self.ret = False
                self.frame = None

    def read(self):
        with self.lock:
            if self.frame is None:
                return False, None
            return self.ret, self.frame.copy()

    def stop(self) -> None:
        self.running = False
        if self.cap is not None:
            self.cap.release()


def process_frame(
    runner: RfDetrRunner,
    frame_bgr,
    frame_id: Any,
    camera_id: str,
    timestamp: str,
    perf: PerfTracker,
    motion_detector: Optional[MotionDetector],
) -> Optional[List[Dict[str, Any]]]:
    t_frame = time.perf_counter()
    if motion_detector is not None and not motion_detector.has_motion(frame_bgr):
        emit({
            "event": "detections",
            "frame_id": frame_id,
            "camera_id": camera_id,
            "timestamp": timestamp,
            "objects": [],
            "motion_gated": True,
        })
        perf.record("total", (time.perf_counter() - t_frame) * 1000)
        perf.record_frame()
        return None

    t0 = time.perf_counter()
    objects = runner.infer(frame_bgr)
    perf.record("inference", (time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    emit({
        "event": "detections",
        "frame_id": frame_id,
        "camera_id": camera_id,
        "timestamp": timestamp,
        "objects": objects,
    })
    perf.record("emit", (time.perf_counter() - t0) * 1000)
    perf.record("total", (time.perf_counter() - t_frame) * 1000)
    perf.record_frame()
    return objects


def run_pull_mode(config: Dict[str, Any], runner: RfDetrRunner, perf: PerfTracker, motion_detector: Optional[MotionDetector]) -> None:
    camera_id = str(config.get("camera_id", "unknown"))
    rtsp_url = str(config.get("url") or config.get("rtsp_url") or "")
    fps = max(0.1, float(config.get("fps", 3)))
    interval = 1.0 / fps
    reader = StreamReader(rtsp_url)
    stop_flag = False

    def listen_stop() -> None:
        nonlocal stop_flag
        for line in sys.stdin:
            try:
                if json.loads(line.strip()).get("command") == "stop":
                    stop_flag = True
                    break
            except Exception:
                continue

    threading.Thread(target=listen_stop, daemon=True).start()
    frame_id = 0
    while not stop_flag:
        loop_start = time.perf_counter()
        ret, frame_bgr = reader.read()
        if not ret or frame_bgr is None:
            time.sleep(0.02)
            continue
        frame_id += 1
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        live_frame = encode_frame(frame_bgr)
        if live_frame:
            emit({"event": "live_frame", "frame_id": frame_id, "camera_id": camera_id, "timestamp": timestamp, "frame": live_frame})
        try:
            process_frame(runner, frame_bgr, frame_id, camera_id, timestamp, perf, motion_detector)
        except Exception as exc:
            perf.errors += 1
            emit({"event": "error", "frame_id": frame_id, "message": f"Pipeline error: {exc}", "retriable": True})
        elapsed = time.perf_counter() - loop_start
        time.sleep(max(0.001, interval - elapsed))
    reader.stop()


def run_push_mode(runner: RfDetrRunner, perf: PerfTracker, motion_detector: Optional[MotionDetector]) -> None:
    import cv2
    import numpy as np

    stdin_buffer = sys.stdin.buffer
    while True:
        magic = stdin_buffer.read(4)
        if not magic:
            break

        frame_bgr = None
        if magic == b"AEGS":
            header = stdin_buffer.read(8)
            if len(header) < 8:
                break
            json_len, img_len = struct.unpack(">II", header)
            msg_bytes = stdin_buffer.read(json_len)
            img_bytes = stdin_buffer.read(img_len)
            if len(msg_bytes) < json_len or len(img_bytes) < img_len:
                break
            msg = json.loads(msg_bytes.decode("utf-8"))
            frame_bgr = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        else:
            line = (magic + stdin_buffer.readline()).strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue

        if msg.get("command") == "stop":
            break
        if msg.get("event") != "frame":
            continue

        frame_id = msg.get("frame_id")
        camera_id = str(msg.get("camera_id", "unknown"))
        timestamp = str(msg.get("timestamp", ""))

        t0 = time.perf_counter()
        if frame_bgr is None:
            frame_path = msg.get("frame_path")
            if not frame_path or not _is_safe_frame_path(frame_path) or not Path(frame_path).exists():
                perf.errors += 1
                emit({"event": "error", "frame_id": frame_id, "message": f"Frame not found or unsafe path: {frame_path}", "retriable": True})
                continue
            frame_bgr = cv2.imread(str(frame_path))
        perf.record("file_read", (time.perf_counter() - t0) * 1000)

        if frame_bgr is None:
            perf.errors += 1
            emit({"event": "error", "frame_id": frame_id, "message": "Could not decode frame", "retriable": True})
            continue

        try:
            process_frame(runner, frame_bgr, frame_id, camera_id, timestamp, perf, motion_detector)
        except Exception as exc:
            perf.errors += 1
            emit({"event": "error", "frame_id": frame_id, "message": f"Pipeline error: {exc}", "retriable": True})


def main() -> None:
    args = parse_args()
    config = load_config(args)
    perf = PerfTracker()

    runner = RfDetrRunner(config)
    try:
        perf.model_load_ms = runner.load()
    except Exception as exc:
        emit({"event": "error", "message": f"Failed to load RF-DETR model: {exc}", "retriable": False})
        raise

    motion_detector = None
    if as_bool(config.get("enable_motion_gating"), default=False):
        motion_detector = MotionDetector(
            threshold=int(config.get("motion_threshold", 15)),
            min_area=int(config.get("min_motion_area", 500)),
            motion_masks=config.get("motion_masks", []),
        )

    def handle_signal(_signum, _frame) -> None:
        perf.emit_final()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    emit({
        "event": "ready",
        "model": runner.model_id,
        "model_kind": runner.model_kind,
        "task_mode": runner.task_mode,
        "device": str(runner.device),
        "classes": len(getattr(runner.model.config, "id2label", {}) or {}),
        "fps": float(config.get("fps", 3)),
        "model_load_ms": round(perf.model_load_ms, 1),
        "available_detection_sizes": list(DETECTION_MODELS.keys()),
        "available_segmentation_sizes": list(SEGMENTATION_MODELS.keys()),
        "modules": {
            "object_detection": True,
            "instance_segmentation": runner.model_kind == "segmentation",
            "motion_gating": motion_detector is not None,
        },
    })

    source = str(config.get("source", "webcam"))
    rtsp_url = str(config.get("url") or config.get("rtsp_url") or "")
    if source == "rtsp" or rtsp_url:
        run_pull_mode(config, runner, perf, motion_detector)
    else:
        run_push_mode(runner, perf, motion_detector)
    perf.emit_final()


if __name__ == "__main__":
    main()
