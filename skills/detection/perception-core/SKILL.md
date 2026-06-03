---
name: perception-core
description: "Perception Core Pipeline — real-time object detection with ByteTrack tracking, ROI face detection, face recognition, and hardware backend abstraction"
version: 3.0.0
icon: assets/icon.png
entry: scripts/detect.py
deploy: deploy.sh

requirements:
  python: ">=3.9"
  ultralytics: ">=8.3.0"
  torch: ">=2.4.0"
  onnxruntime: ">=1.10.0"
  scipy: ">=1.7.0"
  platforms: ["linux", "macos", "windows"]

parameters:
  - name: auto_start
    label: "Auto Start"
    type: boolean
    default: false
    description: "Start this skill automatically when Hawkeye launches"
    group: Lifecycle

  - name: model_size
    label: "Model Size"
    type: select
    options: ["nano", "small", "medium", "large"]
    default: "nano"
    description: "Larger models are more accurate but slower"
    group: Model

  - name: confidence
    label: "Confidence Threshold"
    type: number
    min: 0.1
    max: 1.0
    default: 0.8
    group: Model

  - name: classes
    label: "Detect Classes"
    type: string
    default: "person,car,dog,cat"
    description: "Comma-separated COCO class names (80 classes available)"
    group: Model

  - name: fps
    label: "Processing FPS"
    type: select
    options: [0.2, 0.5, 1, 3, 5, 15]
    default: 5
    description: "Frames per second — higher = more CPU/GPU usage"
    group: Performance

  - name: device
    label: "Inference Device"
    type: select
    options: ["auto", "cpu", "cuda", "mps", "rocm"]
    default: "auto"
    description: "auto = best available GPU, else CPU"
    group: Performance

  - name: use_optimized
    label: "Hardware Acceleration"
    type: boolean
    default: true
    description: "Auto-convert model to optimized format for faster inference"
    group: Performance

  - name: compute_units
    label: "Apple Compute Units"
    type: select
    options: ["auto", "cpu_and_ne", "all", "cpu_only", "cpu_and_gpu"]
    default: "auto"
    description: "CoreML compute target — 'auto' routes to Neural Engine (NPU), leaving GPU free for LLM/VLM"
    group: Performance
    platform: macos
  - name: enable_face_recognition
    label: "Face Recognition"
    type: boolean
    default: false
    description: "Enable face detection (SCRFD) + recognition (MobileFaceNet)"
    group: Recognition

  - name: face_reid_threshold
    label: "Face Recognition Threshold"
    type: number
    min: 0.1
    max: 0.99
    default: 0.60
    description: "Cosine similarity threshold for face matching"
    group: Recognition

  - name: reid_every_n
    label: "Re-ID Interval (frames)"
    type: number
    min: 1
    max: 30
    default: 5
    description: "Run re-ID every N frames per tracked person (lower = more accurate, higher = faster)"
    group: Recognition

  - name: min_person_width
    label: "Min Person Width (px)"
    type: number
    min: 20
    max: 400
    default: 80
    description: "Skip re-ID for persons smaller than this width (saves computation on distant pedestrians)"
    group: Recognition

  - name: enable_motion_gating
    label: "Enable Motion Gating"
    type: boolean
    default: false
    description: "Enable OpenCV CPU frame-differencing to bypass YOLO inference on static scenes"
    group: Performance

  - name: motion_threshold
    label: "Motion Threshold"
    type: number
    min: 1
    max: 255
    default: 15
    description: "Pixel intensity change threshold for motion. Lower = more sensitive."
    group: Performance

  - name: min_motion_area
    label: "Min Motion Area (px)"
    type: number
    min: 10
    max: 50000
    default: 500
    description: "Minimum contour pixel size to classify as motion"
    group: Performance

capabilities:
  live_detection:
    script: scripts/detect.py
    description: "Real-time object detection with optional person tracking and identity recognition"
  face_recognition:
    script: scripts/detect.py
    description: "Face detection + recognition using SCRFD and MobileFaceNet"
---

# YOLO 2026 Object Detection

Real-time object detection using the latest YOLO 2026 models. Detects 80+ COCO object classes including people, vehicles, animals, and everyday objects. Outputs bounding boxes with labels and confidence scores.

## Model Sizes

| Size | Speed | Accuracy | Best For |
|------|-------|----------|----------|
| nano | Fastest | Good | Real-time on CPU, edge devices |
| small | Fast | Better | Balanced speed/accuracy |
| medium | Moderate | High | Accuracy-focused deployments |
| large | Slower | Highest | Maximum detection quality |

## Hardware Acceleration

The skill uses [`env_config.py`](../../lib/env_config.py) to **automatically detect hardware** and convert the model to the fastest format for your platform. Conversion happens once during deployment and is cached.

| Platform | Backend | Optimized Format | Compute Units | Expected Speedup |
|----------|---------|------------------|:-------------:|:----------------:|
| NVIDIA GPU | CUDA | TensorRT `.engine` | GPU | ~3-5x |
| Apple Silicon (M1+) | MPS | CoreML `.mlpackage` | **Neural Engine** (NPU) | ~2x |
| Intel CPU/GPU/NPU | OpenVINO | OpenVINO IR `.xml` | CPU/GPU/NPU | ~2-3x |
| AMD GPU | ROCm | ONNX Runtime | GPU | ~1.5-2x |
| CPU (any) | CPU | ONNX Runtime | CPU | ~1.5x |

> **Apple Silicon Note**: Detection defaults to `cpu_and_ne` (CPU + Neural Engine), keeping the GPU free for LLM/VLM inference. Set `compute_units: all` to include GPU if not running local LLM.

### How It Works

1. `deploy.sh` detects your hardware via `env_config.HardwareEnv.detect()`
2. Installs the matching `requirements_{backend}.txt` (e.g. CUDA → includes `tensorrt`)
3. Pre-converts the default model to the optimal format
4. At runtime, `detect.py` loads the cached optimized model automatically
5. Falls back to PyTorch if optimization fails

Set `use_optimized: false` to disable auto-conversion and use raw PyTorch.

## Auto Start

Set `auto_start: true` in the skill config to start detection automatically when Hawkeye launches. The skill will begin processing frames from the selected camera immediately.

```yaml
auto_start: true
model_size: nano
fps: 5
```

## Performance Monitoring

The skill emits `perf_stats` events every 50 frames with aggregate timing:

```jsonl
{"event": "perf_stats", "total_frames": 50, "timings_ms": {
  "inference": {"avg": 3.4, "p50": 3.2, "p95": 5.1},
  "postprocess": {"avg": 0.15, "p50": 0.12, "p95": 0.31},
  "total": {"avg": 3.6, "p50": 3.4, "p95": 5.5}
}}
```

## Protocol

Communicates via **JSON lines** over stdin/stdout.

### Hawkeye → Skill (stdin)
```jsonl
{"event": "frame", "frame_id": 42, "camera_id": "front_door", "timestamp": "...", "frame_path": "/tmp/hawkeye_detection/frame_front_door.jpg", "width": 1920, "height": 1080}
```

### Skill → Hawkeye (stdout)
```jsonl
{"event": "ready", "model": "yolo2026n", "device": "mps", "backend": "mps", "format": "coreml", "gpu": "Apple M3", "classes": 80, "fps": 5}
{"event": "detections", "frame_id": 42, "camera_id": "front_door", "timestamp": "...", "objects": [
  {"class": "person", "confidence": 0.92, "bbox": [100, 50, 300, 400]}
]}
{"event": "perf_stats", "total_frames": 50, "timings_ms": {"inference": {"avg": 3.4}}}
{"event": "error", "message": "...", "retriable": true}
```

### Bounding Box Format
`[x_min, y_min, x_max, y_max]` — pixel coordinates (xyxy).

### Stop Command
```jsonl
{"command": "stop"}
```

## Installation

The `deploy.sh` bootstrapper handles everything — Python environment, GPU backend detection, dependency installation, and model optimization. No manual setup required.

```bash
./deploy.sh
```

### Requirements Files

| File | Backend | Key Deps |
|------|---------|----------|
| `requirements_cuda.txt` | NVIDIA | `torch` (cu124), `tensorrt` |
| `requirements_mps.txt` | Apple | `torch`, `coremltools` |
| `requirements_intel.txt` | Intel | `torch`, `openvino` |
| `requirements_rocm.txt` | AMD | `torch` (rocm6.2), `onnxruntime-rocm` |
| `requirements_cpu.txt` | CPU | `torch` (cpu), `onnxruntime` |
