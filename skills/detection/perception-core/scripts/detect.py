#!/usr/bin/env python3
"""
Unified Perception Pipeline — SharpAI Hawkeye.

Single-frame-loop detection pipeline combining:
  - YOLO 2026 object detection (all classes)
  - ByteTrack multi-object tracking (persons)
  - [Optional] Body Re-ID (FastReID ONNX, 512-d)
  - [Optional] Face Recognition (SCRFD + MobileFaceNet, 128-d)
  - Identity fusion + unified vector database

Communicates via JSON lines over stdin/stdout (same protocol as before —
zero frontend changes required).

Usage:
  python detect.py --config config.json
  python detect.py --model-size nano --confidence 0.5 --device auto
  python detect.py --enable-body-reid --enable-face-recognition
"""

import sys
import os
import json
import argparse
import signal
import time
from pathlib import Path
from typing import Optional
import base64
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import requests

# Prevent ultralytics from auto-installing packages
os.environ.setdefault("YOLO_AUTOINSTALL", "0")

# Allowed roots for frame_path / file_path values received on stdin. Only
# paths inside these roots are accepted; everything else is rejected to
# prevent arbitrary-file-read / exfil primitives via crafted messages.
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
    # Reject obvious escape attempts before resolving
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

# ── Import shared protocol & env_config from skills/lib/ ───────────────────
_script_dir = Path(__file__).resolve().parent
_lib_candidates = [
    _script_dir,                                  # local override (tests/dev)
    _script_dir.parent.parent.parent / "lib",     # skills/lib/ (canonical)
    _script_dir.parent / "lib",                   # perception-core/lib/
]
_env_config_loaded = False
for _lib_path in _lib_candidates:
    if (_lib_path / "env_config.py").exists():
        sys.path.insert(0, str(_lib_path))
        from env_config import HardwareEnv  # noqa: E402
        _env_config_loaded = True
        break

# Protocol helpers (emit, log, typed event_* functions)
if str(_lib_candidates[1]) in sys.path or any((p / "protocol.py").exists() for p in _lib_candidates):
    _lib_with_protocol = next(p for p in _lib_candidates if (p / "protocol.py").exists())
    sys.path.insert(0, str(_lib_with_protocol))
    from protocol import (  # noqa: E402
        emit, log, setup_log_prefix,
        event_error, event_ready, event_progress, event_detections,
        event_alert, event_live_frame, event_perf_stats, event_log_message,
    )
    setup_log_prefix("Perception")
else:
    # Fallback: local emit/log if protocol.py is not available
    def emit(event: dict):
        print(json.dumps(event, separators=(",", ":")), flush=True)
    def log(msg: str):
        print(f"[Perception] {msg}", file=sys.stderr, flush=True)

if not _env_config_loaded:
    class HardwareEnv:
        def __init__(self):
            self.backend = "cpu"
            self.device = "cpu"
            self.export_format = "none"
            self.gpu_name = ""
            self.gpu_memory_mb = 0
            self.driver_version = ""
            self.framework_ok = False
            self.coral_detected = False
            self.export_ms = 0.0
            self.load_ms = 0.0

        @staticmethod
        def detect(preferred_device="auto"):
            import torch
            env = HardwareEnv()
            preferred_device = preferred_device.lower()
            if preferred_device == "cpu":
                env.backend = "cpu"; env.device = "cpu"
            elif preferred_device == "cuda" and torch.cuda.is_available():
                env.backend = "cuda"; env.device = "cuda"
            elif preferred_device == "mps" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                env.backend = "mps"; env.device = "mps"
            else:
                if torch.cuda.is_available():
                    env.backend = "cuda"; env.device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    env.backend = "mps"; env.device = "mps"
            return env

        def load_optimized(self, model_name, use_optimized=True):
            import time as _t
            from ultralytics import YOLO
            t0 = _t.perf_counter()
            model = YOLO(f"{model_name}.pt")
            model.to(self.device)
            self.load_ms = (_t.perf_counter() - t0) * 1000
            return model, "pytorch"

        def to_dict(self):
            return {"backend": self.backend, "device": self.device}

# ── Local imports ─────────────────────────────────────────────────────────────
sys.path.insert(0, str(_script_dir))
from tracker import ByteTracker, Track  # noqa: E402
from identity_db import IdentityDB      # noqa: E402

# Model size → ultralytics model name
MODEL_SIZE_MAP = {
    "nano": "yolo26n",
    "small": "yolo26s",
    "medium": "yolo26m",
    "large": "yolo26l",
}

PERF_STATS_INTERVAL = 50


# ──────────────────────────────────────────────────────────────────────────────
# Performance Tracker
# ──────────────────────────────────────────────────────────────────────────────

class PerfTracker:
    def __init__(self, interval: int = PERF_STATS_INTERVAL):
        self.interval = interval
        self.frame_count = 0
        self.total_frames = 0
        self.error_count = 0
        self.model_load_ms = 0.0
        self.export_ms = 0.0
        self._timings = {
            "file_read": [],
            "inference": [],
            "tracking": [],
            "face_recog": [],
            "fusion": [],
            "emit": [],
            "total": [],
        }

    def record(self, stage: str, duration_ms: float):
        if stage in self._timings:
            self._timings[stage].append(duration_ms)

    def record_frame(self):
        self.frame_count += 1
        self.total_frames += 1
        if self.frame_count >= self.interval:
            self.emit_stats()
            self.frame_count = 0

    def emit_stats(self):
        stats = {
            "event": "perf_stats",
            "total_frames": self.total_frames,
            "window_size": len(self._timings["total"]) or 1,
            "errors": self.error_count,
            "model_load_ms": round(self.model_load_ms, 1),
            "timings_ms": {},
        }
        if self.export_ms > 0:
            stats["export_ms"] = round(self.export_ms, 1)
        for stage, values in self._timings.items():
            if not values:
                continue
            sorted_v = sorted(values)
            n = len(sorted_v)
            stats["timings_ms"][stage] = {
                "avg": round(sum(sorted_v) / n, 2),
                "min": round(sorted_v[0], 2),
                "max": round(sorted_v[-1], 2),
                "p50": round(sorted_v[n // 2], 2),
                "p95": round(sorted_v[int(n * 0.95)], 2),
                "p99": round(sorted_v[int(n * 0.99)], 2),
            }
        emit(stats)
        for key in self._timings:
            self._timings[key].clear()

    def emit_final(self):
        if self._timings["total"]:
            self.emit_stats()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
# (emit, log imported from skills/lib/protocol.py above)


def is_point_in_zones(px, py, w, h, alert_zones):
    if not alert_zones:
        return False
    import cv2
    import numpy as np
    for zone in alert_zones:
        try:
            pts = np.array([[int(pt[0] * w), int(pt[1] * h)] for pt in zone], dtype=np.int32)
            if cv2.pointPolygonTest(pts, (px, py), False) >= 0:
                return True
        except Exception as e:
            log(f"Error testing alert zone polygon: {e}")
    return False


# ──────────────────────────────────────────────────────────────────────────────
# VLM Threat Assessment Worker (Asynchronous Background Thread)
# ──────────────────────────────────────────────────────────────────────────────

vlm_executor = ThreadPoolExecutor(max_workers=1)
vlm_active = False
last_vlm_time = 0.0
vlm_lock = threading.Lock()

def parse_vlm_response(content):
    # Try parsing direct JSON
    try:
        data = json.loads(content.strip())
        return data.get("threat_level", "info"), data.get("description", content)
    except json.JSONDecodeError:
        pass
    
    # Try finding json block
    match = re.search(r"\{.*?\}", content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return data.get("threat_level", "info"), data.get("description", content)
        except json.JSONDecodeError:
            pass
            
    # Try manual regex parsing or heuristic
    lower_content = content.lower()
    threat_level = "info"
    if "critical" in lower_content:
        threat_level = "critical"
    elif "warning" in lower_content or "suspicious" in lower_content:
        threat_level = "warning"
        
    return threat_level, content


def query_vlm_task(frame_img, frame_path, frame_id, camera_id, timestamp, detected_objects, vlm_url, vlm_model, vlm_prompt):
    global vlm_active, last_vlm_time
    
    try:
        objs_str = ", ".join(detected_objects)
        prompt = vlm_prompt.replace("{objects}", objs_str)
        
        # Read and encode image
        if frame_path and _is_safe_frame_path(frame_path) and os.path.exists(frame_path):
            with open(frame_path, "rb") as f:
                base64_image = base64.b64encode(f.read()).decode('utf-8')
            mime_type = "image/png" if frame_path.lower().endswith(".png") else "image/jpeg"
        elif frame_img is not None:
            import cv2
            ret, jpeg = cv2.imencode('.jpg', frame_img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ret:
                log("Failed to encode frame_img to JPEG for VLM query")
                return
            base64_image = base64.b64encode(jpeg.tobytes()).decode('utf-8')
            mime_type = "image/jpeg"
        else:
            log("No image data available for VLM query")
            return
        
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": vlm_model or "llava",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_image}"
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ],
            "max_tokens": 300,
            "temperature": 0.2
        }
        
        endpoint = f"{vlm_url.rstrip('/')}/v1/chat/completions"
        
        log(f"Submitting frame {frame_id} (trigger objects: {objs_str}) to VLM at {vlm_url}...")
        
        # Call VLM endpoint
        response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            res_data = response.json()
            content = res_data["choices"][0]["message"]["content"]

            # Parse response
            threat_level, description = parse_vlm_response(content)

            # Emit threat_analysis event
            snapshot_path = save_snapshot_jpg(frame_img, camera_id) if frame_img is not None else None
            payload = {
                "event": "threat_analysis",
                "frame_id": frame_id,
                "camera_id": camera_id,
                "timestamp": timestamp,
                "alert_type": threat_level,
                "message": description,
            }
            if snapshot_path is not None:
                payload["snapshot_path"] = snapshot_path
            emit(payload)
            log(f"VLM threat analysis complete for frame {frame_id}: [{threat_level.upper()}] {description}")
        else:
            log(f"VLM Error: HTTP {response.status_code} - {response.text}")
            snapshot_path = save_snapshot_jpg(frame_img, camera_id) if frame_img is not None else None
            payload = {
                "event": "threat_analysis",
                "frame_id": frame_id,
                "camera_id": camera_id,
                "timestamp": timestamp,
                "alert_type": "info",
                "message": f"VLM Analysis Failed: HTTP {response.status_code}",
            }
            if snapshot_path is not None:
                payload["snapshot_path"] = snapshot_path
            emit(payload)

    except Exception as e:
        log(f"Error querying VLM: {e}")
        snapshot_path = save_snapshot_jpg(frame_img, camera_id) if frame_img is not None else None
        payload = {
            "event": "threat_analysis",
            "frame_id": frame_id,
            "camera_id": camera_id,
            "timestamp": timestamp,
            "alert_type": "info",
            "message": f"VLM Connection Error: {str(e)}",
        }
        if snapshot_path is not None:
            payload["snapshot_path"] = snapshot_path
        emit(payload)
    finally:
        with vlm_lock:
            vlm_active = False
            last_vlm_time = time.time()


vlm_stationary_history = {}

def check_and_trigger_vlm(frame_img, frame_path, frame_id, camera_id, timestamp, objects_out, enable_vlm, vlm_trigger_classes, target_classes, vlm_cooldown, vlm_url, vlm_model, vlm_prompt):
    if not enable_vlm:
        return
        
    global vlm_stationary_history
    
    # Prune stationary history for tracks that are no longer present
    active_tids = {obj["track_id"] for obj in objects_out if "track_id" in obj}
    vlm_stationary_history = {tid: val for tid, val in vlm_stationary_history.items() if tid in active_tids}

    # IoU helper
    def get_iou(boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        iou = interArea / float(boxAArea + boxBArea - interArea) if (boxAArea + boxBArea - interArea) > 0 else 0
        return iou

    # Update stationary frame counts
    stationary_tids = set()
    for obj in objects_out:
        if "track_id" in obj:
            tid = obj["track_id"]
            box = obj["box"]
            if tid in vlm_stationary_history:
                last_box, count = vlm_stationary_history[tid]
                iou = get_iou(box, last_box)
                
                # Calculate center displacement in absolute pixels
                cx = (box[0] + box[2]) / 2.0
                cy = (box[1] + box[3]) / 2.0
                last_cx = (last_box[0] + last_box[2]) / 2.0
                last_cy = (last_box[1] + last_box[3]) / 2.0
                
                dx_pixels = abs(cx - last_cx)
                dy_pixels = abs(cy - last_cy)
                
                box_w = max(1.0, box[2] - box[0])
                box_h = max(1.0, box[3] - box[1])
                
                # Depth scaling: small boxes (far away) get wider pixel limits (min 4px) to ignore sensor noise.
                # Large boxes (close) get narrower relative limits (max 16px) to catch true movement.
                allowed_dx = max(4.0, min(16.0, box_w * 0.08))
                allowed_dy = max(4.0, min(16.0, box_h * 0.08))
                
                # Stationary if high overlap (>= 0.80) OR center shift is within dynamic pixel tolerance
                if iou >= 0.80 or (dx_pixels < allowed_dx and dy_pixels < allowed_dy):
                    count += 1
                else:
                    count = 0
                vlm_stationary_history[tid] = (box, count)
                if count >= 30: # 30 frames at 5 FPS = 6 seconds of static position
                    stationary_tids.add(tid)
            else:
                vlm_stationary_history[tid] = (box, 0)

    # Filter out stationary objects
    detected_classes = []
    for obj in objects_out:
        tid = obj.get("track_id")
        if tid in stationary_tids:
            # Skip triggering VLM for stationary objects
            continue
        detected_classes.append(obj["class"])

    trigger_objects = [c for c in detected_classes if c in vlm_trigger_classes]
    
    # Check if a recognized track is actually a person
    for obj in objects_out:
        tid = obj.get("track_id")
        if tid in stationary_tids:
            continue
        is_person_track = "track_id" in obj and obj["class"] not in target_classes
        if (is_person_track and "person" in vlm_trigger_classes) or (obj["class"] in vlm_trigger_classes):
            trigger_objects.append(obj["class"])
            
    if trigger_objects:
        global vlm_active, last_vlm_time
        with vlm_lock:
            now = time.time()
            if not vlm_active and (now - last_vlm_time >= vlm_cooldown):
                vlm_active = True
                img_copy = frame_img.copy() if frame_img is not None else None
                vlm_executor.submit(
                    query_vlm_task,
                    img_copy,
                    frame_path,
                    frame_id,
                    camera_id,
                    timestamp,
                    list(set(trigger_objects)),
                    vlm_url,
                    vlm_model,
                    vlm_prompt
                )
                last_vlm_time = now



def parse_args():
    parser = argparse.ArgumentParser(description="Unified Perception Pipeline")

    # Existing YOLO params
    parser.add_argument("--config", type=str, help="Path to config JSON file")
    parser.add_argument("--model-size", type=str, default="nano",
                        choices=["nano", "small", "medium", "large"])
    parser.add_argument("--confidence", type=float, default=0.8)
    parser.add_argument("--classes", type=str, default="person,car,dog,cat")
    parser.add_argument("--device", type=str, default="auto",
                        choices=["auto", "cpu", "cuda", "mps", "rocm"])
    parser.add_argument("--fps", type=float, default=5)

    # Face Recognition params
    parser.add_argument("--enable-face-recognition", action="store_true")
    parser.add_argument("--face-reid-threshold", type=float, default=0.60)
    parser.add_argument("--reid-every-n", type=int, default=5)
    parser.add_argument("--min-person-width", type=int, default=80)
    parser.add_argument("--detector-threshold", type=float, default=0.50)

    # Motion Gating params
    parser.add_argument("--enable-motion-gating", action="store_true")
    parser.add_argument("--motion-threshold", type=int, default=15)
    parser.add_argument("--min-motion-area", type=int, default=500)

    # VLM Threat Analysis params
    parser.add_argument("--enable-vlm-threat-analysis", action="store_true")
    parser.add_argument("--vlm-trigger-classes", type=str, default="person,car,backpack,handbag")
    parser.add_argument("--vlm-cooldown", type=float, default=10.0)

    return parser.parse_args()


def load_config(args):
    env_params = os.environ.get("HAWKEYE_SKILL_PARAMS")
    if env_params:
        try:
            return json.loads(env_params)
        except json.JSONDecodeError:
            pass
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
    return {
        "model_size": args.model_size,
        "confidence": args.confidence,
        "classes": args.classes.split(","),
        "device": args.device,
        "fps": args.fps,
        "enable_face_recognition": args.enable_face_recognition,
        "face_reid_threshold": args.face_reid_threshold,
        "reid_every_n": args.reid_every_n,
        "min_person_width": args.min_person_width,
        "detector_threshold": args.detector_threshold,
        "enable_motion_gating": args.enable_motion_gating,
        "motion_threshold": args.motion_threshold,
        "min_motion_area": args.min_motion_area,
        "enable_vlm_threat_analysis": args.enable_vlm_threat_analysis,
        "vlm_trigger_classes": args.vlm_trigger_classes.split(","),
        "vlm_cooldown": args.vlm_cooldown,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main Pipeline
# ──────────────────────────────────────────────────────────────────────────────

def main():
    import struct
    import numpy as np
    import cv2

    args = parse_args()
    config = load_config(args)

    model_size = config.get("model_size", "nano")
    confidence = config.get("confidence", 0.8)
    fps = config.get("fps", 5)
    use_optimized = config.get("use_optimized", config.get("use_coreml", True))
    if isinstance(use_optimized, str):
        use_optimized = use_optimized.lower() in ("true", "1", "yes")

    model_name = MODEL_SIZE_MAP.get(model_size, "yolo26n")

    target_classes = config.get("classes", ["person", "car", "dog", "cat"])
    if isinstance(target_classes, str):
        target_classes = [c.strip() for c in target_classes.split(",")]

    # Face Recognition config
    enable_face_recog = config.get("enable_face_recognition", False)
    face_threshold = float(config.get("face_reid_threshold", 0.60))
    reid_every_n = int(config.get("reid_every_n", 5))
    min_person_width = int(config.get("min_person_width", 80))
    det_threshold = float(config.get("detector_threshold", 0.50))

    # VLM config
    enable_vlm = config.get("enable_vlm_threat_analysis", False)
    vlm_cooldown = float(config.get("vlm_cooldown", 10.0))
    vlm_prompt = config.get("vlm_prompt", "")
    if not vlm_prompt:
        vlm_prompt = "You are a professional home security AI assistant. Analyze this camera frame. YOLO detected the following objects: {objects}. Perform threat assessment. Identify any security concerns (accidents, fire, smoke, weapon, theft, break-in, intrusion, or suspicious activity). Determine the threat level: 'critical' (immediate danger like fire, weapons, break-in in progress), 'warning' (suspicious activity, open doors/windows, property damage, accidents), or 'info' (normal activity, people passing by, animals). Respond with ONLY a JSON object in this format:\n{\n  \"threat_level\": \"info\" | \"warning\" | \"critical\",\n  \"description\": \"Short explanation of what you see and why you classified it this way.\"\n}"
    
    vlm_trigger_classes_raw = config.get("vlm_trigger_classes", ["person", "car", "backpack", "handbag"])
    if isinstance(vlm_trigger_classes_raw, str):
        vlm_trigger_classes = [c.strip() for c in vlm_trigger_classes_raw.split(",")]
    else:
        vlm_trigger_classes = vlm_trigger_classes_raw

    vlm_url = os.environ.get("HAWKEYE_VLM_URL") or "http://localhost:5405"
    vlm_model = os.environ.get("HAWKEYE_VLM_MODEL") or ""


    # ── Hardware detection & YOLO model loading ───────────────────────────
    emit({"event": "progress", "stage": "init", "message": "Detecting compute hardware..."})
    device_choice = config.get("device", "auto")
    env = HardwareEnv.detect(device_choice)
    perf = PerfTracker(interval=PERF_STATS_INTERVAL)

    tpu_msg = " [Google Coral TPU detected]" if env.coral_detected else ""
    gpu_msg = f"{env.gpu_name} ({env.backend}){tpu_msg}" if env.gpu_name else f"{env.backend}{tpu_msg}"
    emit({"event": "progress", "stage": "init", "message": f"Hardware: {gpu_msg}"})

    try:
        emit({"event": "progress", "stage": "model",
              "message": f"Loading {model_name} model ({env.export_format} format)..."})
        model, model_format = env.load_optimized(model_name, use_optimized=use_optimized)
        perf.model_load_ms = env.load_ms
        perf.export_ms = env.export_ms

        # Check for silent fallback to CPU on NVIDIA GPU hardware
        if env.backend == "cuda" and env.device == "cpu":
            emit({
                "event": "diagnostic_warning",
                "code": "CUDA_FALLBACK_TO_CPU",
                "message": f"NVIDIA GPU '{env.gpu_name}' detected, but PyTorch fell back to CPU. Your cuda dependencies (torch-cuda) are missing or misconfigured in the virtual environment. Inference will be slow."
            })

        if env.export_ms > 0:
            emit({"event": "progress", "stage": "model",
                  "message": f"Model optimized in {env.export_ms:.0f}ms"})
    except Exception as e:
        emit({"event": "error", "message": f"Failed to load model: {e}", "retriable": False})
        sys.exit(1)

    # ── ONNX Runtime providers for face recognition ──────────────────────
    ort_providers = ["CPUExecutionProvider"]
    if enable_face_recog:
        try:
            import onnxruntime
            available_providers = onnxruntime.get_available_providers()
            if env.backend == "cuda" and "CUDAExecutionProvider" in available_providers:
                ort_providers = ["CUDAExecutionProvider"] + ort_providers
            elif env.backend == "mps" and "CoreMLExecutionProvider" in available_providers:
                ort_providers = ["CoreMLExecutionProvider"] + ort_providers
        except ImportError:
            log("WARNING: onnxruntime not installed. Face Recognition will be disabled.")
            enable_face_recog = False

    # ── Initialize ByteTracker ────────────────────────────────────────────
    tracker = ByteTracker(
        max_lost=30,
        min_hits=3,
        high_threshold=confidence * 0.7,  # slightly below detection threshold
        iou_threshold=0.3,
    )

    # ── Load face recognition module ──────────────────────────────────────
    _skill_dir = _script_dir.parent
    _models_dir = _skill_dir / "models"

    face_module = None

    if enable_face_recog:
        try:
            from modules.face_recog import FaceRecogModule
            scrfd_path = str(_models_dir / "scrfd_500m_bnkps.onnx")
            mfnet_path = str(_models_dir / "mobilefacenet.onnx")
            face_module = FaceRecogModule(
                scrfd_path=scrfd_path,
                mfnet_path=mfnet_path,
                min_person_width=min_person_width,
                min_confidence=0.6,
                reid_every_n=reid_every_n,
                det_threshold=det_threshold,
            )
            face_module.load(ort_providers)
            perf.model_load_ms += face_module.load_ms
            emit({"event": "progress", "stage": "model",
                  "message": "Face Recognition module loaded"})
        except Exception as e:
            log(f"WARNING: Face Recognition module failed to load: {e}")
            emit({
                "event": "diagnostic_warning",
                "code": "FACE_RECOG_LOAD_FAILED",
                "message": f"Face Recognition module failed to load: {str(e)}"
            })
            log("Face Recognition will be disabled for this session.")
            face_module = None

    # ── Initialize identity database ─────────────────────────────────────
    identity_db = None
    if face_module is not None:
        db_dir = _skill_dir / "data"
        identity_db = IdentityDB(db_dir)
        identity_db.setup()
        log(f"Identity database initialized ({identity_db.size} profiles).")

    # ── Initialize motion detector ────────────────────────────────────────
    enable_motion_gating = config.get("enable_motion_gating", False) and not config.get("use_shm", False)
    motion_threshold = int(config.get("motion_threshold", 15))
    min_motion_area = int(config.get("min_motion_area", 500))
    motion_masks = config.get("motion_masks", [])
    alert_zones = config.get("alert_zones", [])

    motion_detector = None
    if enable_motion_gating:
        class MotionDetector:
            def __init__(self, threshold=15, min_area=500, motion_masks=[]):
                self.threshold = threshold
                self.min_area = min_area
                self.motion_masks = motion_masks
                self.avg_frame = None
                self.mask_img = None
                self.alpha = 0.05  # accumulateWeighted learning rate

            def has_motion(self, frame_bgr):
                # 1. Downscale to width=360 for high performance
                h, w = frame_bgr.shape[:2]
                dw = 360
                dh = int(h * (360.0 / w))
                resized = cv2.resize(frame_bgr, (dw, dh))
                gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
                
                # Lazy-initialize binary exclusion mask scaled to downscaled dimensions
                if self.mask_img is None:
                    self.mask_img = np.ones((dh, dw), dtype=np.uint8) * 255
                    if self.motion_masks:
                        for poly in self.motion_masks:
                            try:
                                pts = np.array([[int(pt[0] * dw), int(pt[1] * dh)] for pt in poly], dtype=np.int32)
                                cv2.fillPoly(self.mask_img, [pts], 0) # Fill black to mask out motion
                            except Exception as e:
                                log(f"Error drawing motion mask polygon {poly}: {e}")
                
                if self.mask_img is not None:
                    cv2.bitwise_and(gray, self.mask_img, dst=gray)

                # 2. Gaussian Blur to filter noise
                gray = cv2.GaussianBlur(gray, (9, 9), 0)
                
                if self.avg_frame is None:
                    self.avg_frame = gray.copy().astype("float")
                    return False
                    
                # 3. Running average threshold
                cv2.accumulateWeighted(gray, self.avg_frame, self.alpha)
                frame_delta = cv2.absdiff(gray, cv2.convertScaleAbs(self.avg_frame))
                
                # 4. Adaptive contrast calculation: find 96th percentile of difference values
                # If there are lighting fluctuations, the percentile climbs and raises the threshold
                flat_delta = frame_delta.flatten()
                if len(flat_delta) > 0:
                    p96 = np.percentile(flat_delta, 96)
                    # dynamic threshold scale
                    dynamic_thresh = max(self.threshold, int(p96 * 1.5))
                else:
                    dynamic_thresh = self.threshold

                thresh = cv2.threshold(frame_delta, dynamic_thresh, 255, cv2.THRESH_BINARY)[1]
                thresh = cv2.dilate(thresh, None, iterations=2)
                
                # Scale min_area proportionally to downscaled resolution
                scale_factor = (dw * dh) / (w * h)
                scaled_min_area = max(50, int(self.min_area * scale_factor))
                
                contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for contour in contours:
                    if cv2.contourArea(contour) >= scaled_min_area:
                        return True
                return False

        motion_detector = MotionDetector(threshold=motion_threshold, min_area=min_motion_area, motion_masks=motion_masks)
        log(f"Motion gating enabled (threshold: {motion_threshold}, min_area: {min_motion_area}, masks: {len(motion_masks)})")


    # ── Emit ready event ─────────────────────────────────────────────────
    ready_event = {
        "event": "ready",
        "model": f"yolo26{model_size[0]}",
        "model_size": model_size,
        "device": env.device,
        "backend": env.backend,
        "format": model_format,
        "gpu": env.gpu_name,
        "classes": len(model.names),
        "fps": fps,
        "model_load_ms": round(perf.model_load_ms, 1),
        "available_sizes": list(MODEL_SIZE_MAP.keys()),
        "modules": {
            "face_recognition": face_module is not None,
            "tracking": True,
        },
    }
    if hasattr(env, 'compute_units') and env.backend == "mps":
        ready_event["compute_units"] = env.compute_units
    emit(ready_event)


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot Persistence Helper
# ──────────────────────────────────────────────────────────────────────────────

# Path to the snapshots directory, provided by the Rust supervisor via env.
# The skill writes JPEGs directly to this location and ships the file path
# (not the bytes) in the event JSON, avoiding base64 inflation over stdio.
_SNAPSHOT_DIR: Optional[Path] = None


def _init_snapshot_dir() -> Optional[Path]:
    """Resolve and create the snapshot output directory (one-time at startup)."""
    global _SNAPSHOT_DIR
    if _SNAPSHOT_DIR is not None:
        return _SNAPSHOT_DIR
    raw = os.environ.get("DEEPCAMERA_SNAPSHOTS_DIR", "").strip()
    if not raw:
        _SNAPSHOT_DIR = None
        return None
    try:
        p = Path(raw)
        p.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT_DIR = p
        return p
    except Exception as e:
        log(f"snapshot dir init failed: {e}")
        _SNAPSHOT_DIR = None
        return None


def save_snapshot_jpg(frame_bgr, camera_id: str, max_width: int = 960, quality: int = 70) -> Optional[str]:
    """Downscale + JPEG-encode a BGR frame and write it directly to disk.

    Returns the absolute path on success, or None on failure. The returned
    path is what gets shipped in the event JSON. Returns None if the
    snapshot directory wasn't configured (e.g., running standalone).
    """
    if frame_bgr is None:
        return None
    snap_dir = _init_snapshot_dir()
    if snap_dir is None:
        return None
    try:
        import cv2
        h, w = frame_bgr.shape[:2]
        if w > max_width:
            scale = max_width / float(w)
            new_w = max_width
            new_h = max(1, int(round(h * scale)))
            resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            resized = frame_bgr
        ok, buf = cv2.imencode(
            ".jpg", resized,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)],
        )
        if not ok:
            return None

        # Nanosecond resolution guarantees uniqueness even for back-to-back
        # events on the same camera within the same millisecond.
        safe_cam = "".join(c if c.isalnum() or c in "-_" else "_" for c in camera_id)
        filename = f"{safe_cam}_{time.time_ns()}.jpg"
        out_path = snap_dir / filename
        # Write atomically via a tmp file so a half-written JPEG can never
        # be observed by the Rust consumer.
        tmp_path = out_path.with_suffix(".jpg.tmp")
        with open(tmp_path, "wb") as f:
            f.write(buf.tobytes())
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, out_path)
        return str(out_path)
    except Exception as e:
        log(f"snapshot save failed: {e}")
        return None


    # ── Graceful shutdown ────────────────────────────────────────────────
    def handle_signal(signum, frame):
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        log(f"Received {sig_name}, shutting down gracefully")
        if identity_db is not None:
            identity_db.save()
        perf.emit_final()
        vlm_executor.shutdown(wait=False)
        sys.exit(0)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # ── Determine Execution Mode (Push vs Pull) ───────────────────────────
    source = config.get("source", "webcam")
    camera_id = config.get("camera_id", "unknown")
    rtsp_url = config.get("url") or config.get("rtsp_url")
    
    is_pull_mode = (source == 'rtsp') or bool(rtsp_url)

    if is_pull_mode:
        log(f"Starting in PULL MODE for camera: {camera_id} (source: {source})")
        import cv2
        import numpy as np

        frame_id = 0
        
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue

            if msg.get("command") == "stop":
                log("Received stop command")
                break

            if msg.get("event") == "motion_detected":
                t_frame_start = time.perf_counter()
                
                # Fetch keyframe directly from go2rtc via HTTP
                try:
                    import urllib.parse
                    safe_src = urllib.parse.quote(camera_id)
                    resp = requests.get(f"http://127.0.0.1:1984/api/frame.jpeg?src={safe_src}", timeout=2.0)
                    if resp.status_code != 200:
                        log(f"WARNING: Failed to fetch snapshot from go2rtc (HTTP {resp.status_code})")
                        continue
                    
                    # Decode snapshot JPEG in memory
                    nparr = np.frombuffer(resp.content, np.uint8)
                    frame_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if frame_img is None:
                        log("WARNING: Failed to decode snapshot JPEG")
                        continue
                except Exception as e:
                    log(f"WARNING: Error fetching snapshot: {e}")
                    continue
                
                frame_id += 1
                timestamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
                
                try:
                    # ─── 1. YOLO inference ───
                    t0 = time.perf_counter()
                    results = model(frame_img, conf=confidence, verbose=False)
                    perf.record("inference", (time.perf_counter() - t0) * 1000)
                    
                    # ─── 2. Parse detections ───
                    t0 = time.perf_counter()
                    non_person_objects = []
                    person_detections = []
                    
                    for r in results:
                        for box in r.boxes:
                            cls_id = int(box.cls[0])
                            cls_name = model.names[cls_id]
                            x1, y1, x2, y2 = box.xyxy[0].tolist()
                            det_conf = float(box.conf[0])
                            bbox = [int(x1), int(y1), int(x2), int(y2)]
                            
                            if cls_name == "person":
                                person_detections.append({
                                    "bbox": bbox,
                                    "confidence": det_conf,
                                    "class": "person",
                                })
                            elif cls_name in target_classes or not target_classes:
                                non_person_objects.append({
                                    "class": cls_name,
                                    "confidence": round(det_conf, 3),
                                    "bbox": bbox,
                                })
                                
                    # ─── 3. ByteTrack tracking ───
                    tracked_persons = tracker.update(person_detections)
                    perf.record("tracking", (time.perf_counter() - t0) * 1000)
                    
                    # ─── 4. Face Recognition ───
                    db_changed = False
                    if face_module is not None and tracked_persons:
                        t0 = time.perf_counter()
                        for track in tracked_persons:
                            run_face = face_module.should_run(track, frame_img.shape)
                            if not run_face:
                                continue
                                
                            face_result = None
                            try:
                                face_result = face_module.process(track, frame_img)
                            except Exception as e:
                                log(f"Face recognition error: {e}")
                                
                            face_emb = face_result.embedding if face_result else None
                            face_crop = face_result.crop if face_result else None
                            
                            if face_emb is not None:
                                track.face_embedding = face_emb
                                matched_label, score = identity_db.match(
                                    face_emb=face_emb,
                                    face_threshold=face_threshold,
                                )
                                if matched_label is not None:
                                    track.label = matched_label
                                    track.identity_score = score
                                    identity_db.update_embedding(matched_label, timestamp=timestamp)
                                else:
                                    label = identity_db.register_stranger(
                                        face_emb=face_emb,
                                        face_crop=face_crop,
                                        timestamp=timestamp,
                                    )
                                    track.label = label
                                    db_changed = True
                                    log(f"ALERT: Stranger registered: {label}")
                                    _snap = save_snapshot_jpg(frame_img, camera_id)
                                    _payload = {
                                        "event": "threat_analysis",
                                        "frame_id": frame_id,
                                        "camera_id": camera_id,
                                        "timestamp": timestamp,
                                        "alert_type": "warning",
                                        "message": f"Perception Pipeline: New stranger detected and registered as {label}.",
                                    }
                                    if _snap is not None:
                                        _payload["snapshot_path"] = _snap
                                    emit(_payload)
                        perf.record("face_recog", (time.perf_counter() - t0) * 1000)

                    if db_changed and identity_db is not None:
                        identity_db.save()

                    # ─── 5. Build output detections ───
                    t0 = time.perf_counter()
                    h, w = frame_img.shape[:2]
                    objects_out = []
    
                    for obj in non_person_objects:
                        x1, y1, x2, y2 = obj["bbox"]
                        px = int((x1 + x2) / 2)
                        py = int(y2)
                        in_alert = is_point_in_zones(px, py, w, h, alert_zones)
                        objects_out.append({
                            "class": obj["class"],
                            "confidence": obj["confidence"],
                            "bbox": obj["bbox"],
                            "in_alert_zone": in_alert
                        })
    
                    for track in tracked_persons:
                        x1, y1, x2, y2 = [int(v) for v in track.bbox]
                        px = int((x1 + x2) / 2)
                        py = int(y2)
                        in_alert = is_point_in_zones(px, py, w, h, alert_zones)
                        label = track.label if track.label else "person"
                        objects_out.append({
                            "class": label,
                            "confidence": round(track.confidence, 3),
                            "bbox": [x1, y1, x2, y2],
                            "track_id": track.track_id,
                            "in_alert_zone": in_alert
                        })
                    perf.record("fusion", (time.perf_counter() - t0) * 1000)
                    
                    # ─── 6. Emit detections (NO base64 live_frame emission!) ───
                    _snap = save_snapshot_jpg(frame_img, camera_id)
                    _payload = {
                        "event": "detections",
                        "frame_id": frame_id,
                        "camera_id": camera_id,
                        "timestamp": timestamp,
                        "objects": objects_out,
                    }
                    if _snap is not None:
                        _payload["snapshot_path"] = _snap
                    emit(_payload)
                    
                    check_and_trigger_vlm(
                        frame_img,
                        None,
                        frame_id,
                        camera_id,
                        timestamp,
                        objects_out,
                        enable_vlm,
                        vlm_trigger_classes,
                        target_classes,
                        vlm_cooldown,
                        vlm_url,
                        vlm_model,
                        vlm_prompt
                    )
                    
                except Exception as e:
                    import traceback
                    log(f"Pipeline processing error: {e}")
                    traceback.print_exc(file=sys.stderr)
                    perf.error_count += 1
                    
                perf.record("total", (time.perf_counter() - t_frame_start) * 1000)
                perf.record_frame()
                
        log("Pull mode finished gracefully.")

    else:
        # ── Push Mode (stdin binary AEGS / text JSONL) ───────────────────────
        stdin_buffer = sys.stdin.buffer
        while True:
            # Read protocol header: 4 bytes magic
            magic = stdin_buffer.read(4)
            if not magic:
                break

            frame_img = None
            frame_path = None

            if magic == b'AEGS':
                header = stdin_buffer.read(8)
                if len(header) < 8:
                    break
                json_len, img_len = struct.unpack('>II', header)

                json_bytes = b''
                while len(json_bytes) < json_len:
                    chunk = stdin_buffer.read(json_len - len(json_bytes))
                    if not chunk:
                        break
                    json_bytes += chunk
                if len(json_bytes) < json_len:
                    break

                img_bytes = b''
                while len(img_bytes) < img_len:
                    chunk = stdin_buffer.read(img_len - len(img_bytes))
                    if not chunk:
                        break
                    img_bytes += chunk
                if len(img_bytes) < img_len:
                    break

                try:
                    msg = json.loads(json_bytes.decode('utf-8'))
                except Exception as e:
                    log(f"Failed to parse binary JSON metadata: {e}")
                    continue

                nparr = np.frombuffer(img_bytes, np.uint8)
                frame_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            else:
                line_rest = stdin_buffer.readline()
                if not line_rest:
                    break
                line = magic + line_rest
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line.decode('utf-8'))
                except json.JSONDecodeError:
                    continue

            if msg.get("command") == "stop":
                break

            if msg.get("event") == "frame":
                t_frame_start = time.perf_counter()

                frame_id = msg.get("frame_id")
                camera_id = msg.get("camera_id", "unknown")
                timestamp = msg.get("timestamp", "")
                frame_path = msg.get("frame_path")

                t0 = time.perf_counter()
                if frame_img is None:
                    if not frame_path or not _is_safe_frame_path(frame_path) or not Path(frame_path).exists():
                        emit({
                            "event": "error", "frame_id": frame_id,
                            "message": f"Frame not found or unsafe path: {frame_path}",
                            "retriable": True,
                        })
                        perf.error_count += 1
                        continue
                    frame_img = cv2.imread(frame_path)
                    if frame_img is None:
                        emit({
                            "event": "error", "frame_id": frame_id,
                            "message": f"Failed to read image from path: {frame_path}",
                            "retriable": True,
                        })
                        perf.error_count += 1
                        continue
                
                perf.record("file_read", (time.perf_counter() - t0) * 1000)

                if motion_detector is not None:
                    try:
                        has_motion = motion_detector.has_motion(frame_img)
                    except Exception as e:
                        log(f"WARNING: Motion detector failed: {e}. Bypassing gating for this frame.")
                        has_motion = True
                    if not has_motion:
                        emit({
                            "event": "detections",
                            "frame_id": frame_id,
                            "camera_id": camera_id,
                            "timestamp": timestamp,
                            "objects": [],
                            "motion_gated": True
                        })
                        perf.record("total", (time.perf_counter() - t_frame_start) * 1000)
                        perf.record_frame()
                        continue

                try:
                    # ─── 1. YOLO inference ───
                    t0 = time.perf_counter()
                    results = model(frame_img, conf=confidence, verbose=False)
                    perf.record("inference", (time.perf_counter() - t0) * 1000)

                    # ─── 2. Parse detections ───
                    t0 = time.perf_counter()
                    non_person_objects = []
                    person_detections = []

                    for r in results:
                        for box in r.boxes:
                            cls_id = int(box.cls[0])
                            cls_name = model.names[cls_id]
                            x1, y1, x2, y2 = box.xyxy[0].tolist()
                            det_conf = float(box.conf[0])
                            bbox = [int(x1), int(y1), int(x2), int(y2)]

                            if cls_name == "person":
                                person_detections.append({
                                    "bbox": bbox,
                                    "confidence": det_conf,
                                    "class": "person",
                                })
                            elif cls_name in target_classes or not target_classes:
                                non_person_objects.append({
                                    "class": cls_name,
                                    "confidence": round(det_conf, 3),
                                    "bbox": bbox,
                                })

                    # ─── 3. ByteTrack tracking ───
                    tracked_persons = tracker.update(person_detections)
                    perf.record("tracking", (time.perf_counter() - t0) * 1000)

                    # ─── 4. Face Recognition ───
                    db_changed = False

                    if face_module is not None and tracked_persons:
                        t0 = time.perf_counter()

                        for track in tracked_persons:
                            run_face = face_module.should_run(track, frame_img.shape)

                            if not run_face:
                                continue

                            face_result = None
                            try:
                                face_result = face_module.process(track, frame_img)
                            except Exception as e:
                                log(f"Face recognition error: {e}")

                            face_emb = face_result.embedding if face_result else None
                            face_crop = face_result.crop if face_result else None

                            if face_emb is not None:
                                track.face_embedding = face_emb

                                matched_label, score = identity_db.match(
                                    face_emb=face_emb,
                                    face_threshold=face_threshold,
                                )

                                if matched_label is not None:
                                    track.label = matched_label
                                    track.identity_score = score
                                    identity_db.update_embedding(matched_label, timestamp=timestamp)
                                else:
                                    label = identity_db.register_stranger(
                                        face_emb=face_emb,
                                        face_crop=face_crop,
                                        timestamp=timestamp,
                                    )
                                    track.label = label
                                    db_changed = True

                                    log(f"ALERT: Stranger registered: {label}")
                                    _snap = save_snapshot_jpg(frame_img, camera_id)
                                    _payload = {
                                        "event": "threat_analysis",
                                        "frame_id": frame_id,
                                        "camera_id": camera_id,
                                        "timestamp": timestamp,
                                        "alert_type": "warning",
                                        "message": f"Perception Pipeline: New stranger detected and registered as {label}.",
                                    }
                                    if _snap is not None:
                                        _payload["snapshot_path"] = _snap
                                    emit(_payload)

                        perf.record("face_recog", (time.perf_counter() - t0) * 1000)

                    if db_changed and identity_db is not None:
                        identity_db.save()

                    # ─── 5. Build output detections ───
                    t0 = time.perf_counter()
                    h, w = frame_img.shape[:2]
                    objects_out = []

                    for obj in non_person_objects:
                        x1, y1, x2, y2 = obj["bbox"]
                        px = int((x1 + x2) / 2)
                        py = int(y2)
                        in_alert = is_point_in_zones(px, py, w, h, alert_zones)
                        objects_out.append({
                            "class": obj["class"],
                            "confidence": obj["confidence"],
                            "bbox": obj["bbox"],
                            "in_alert_zone": in_alert
                        })

                    for track in tracked_persons:
                        x1, y1, x2, y2 = [int(v) for v in track.bbox]
                        px = int((x1 + x2) / 2)
                        py = int(y2)
                        in_alert = is_point_in_zones(px, py, w, h, alert_zones)
                        label = track.label if track.label else "person"
                        objects_out.append({
                            "class": label,
                            "confidence": round(track.confidence, 3),
                            "bbox": [x1, y1, x2, y2],
                            "track_id": track.track_id,
                            "in_alert_zone": in_alert
                        })

                    perf.record("fusion", (time.perf_counter() - t0) * 1000)

                    # ─── 6. Emit detections ───
                    t0 = time.perf_counter()
                    _snap = save_snapshot_jpg(frame_img, camera_id)
                    _payload = {
                        "event": "detections",
                        "frame_id": frame_id,
                        "camera_id": camera_id,
                        "timestamp": timestamp,
                        "objects": objects_out,
                    }
                    if _snap is not None:
                        _payload["snapshot_path"] = _snap
                    emit(_payload)
                    perf.record("emit", (time.perf_counter() - t0) * 1000)

                    check_and_trigger_vlm(
                        frame_img,
                        frame_path,
                        frame_id,
                        camera_id,
                        timestamp,
                        objects_out,
                        enable_vlm,
                        vlm_trigger_classes,
                        target_classes,
                        vlm_cooldown,
                        vlm_url,
                        vlm_model,
                        vlm_prompt
                    )

                except Exception as e:
                    import traceback
                    log(f"Pipeline processing exception caught in push mode: {e}")
                    traceback.print_exc(file=sys.stderr)
                    emit({
                        "event": "error", "frame_id": frame_id,
                        "message": f"Pipeline error: {e}",
                        "retriable": True,
                    })
                    perf.error_count += 1
                    continue

                perf.record("total", (time.perf_counter() - t_frame_start) * 1000)
                perf.record_frame()

    # ── Cleanup ──────────────────────────────────────────────────────────
    if identity_db is not None:
        identity_db.save()
    perf.emit_final()
    vlm_executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
