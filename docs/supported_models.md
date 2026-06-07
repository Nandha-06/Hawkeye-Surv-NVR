# Supported AI Models

This document lists the models that are actually wired into this Hawkeye workspace.

## Detection Skills

### Perception Core (YOLO + Tracking + Face Recognition)

- **Skill**: [`skills/detection/perception-core`](../skills/detection/perception-core/)
- **Purpose**: Real-time object detection with optional person tracking and face recognition
- **Model sizes exposed in the app**:
  - `nano` → `yolo26n`
  - `small` → `yolo26s`
  - `medium` → `yolo26m`
  - `large` → `yolo26l`
- **Acceleration paths**: PyTorch, ONNX Runtime, TensorRT / CUDA, CoreML / MPS, OpenVINO / Intel, ROCm where available (selected automatically by [`skills/lib/env_config.py`](../skills/lib/env_config.py))
- **Optional modules**:
  - ByteTrack-style person tracking
  - SCRFD face detection: `scrfd_500m_bnkps.onnx`
  - MobileFaceNet face embeddings: `mobilefacenet.onnx`
  - FastReID MobileNetV2 model is present in `models/fast-reid_mobilenetv2.onnx`

### RF-DETR Detection + Segmentation

- **Skill**: [`skills/detection/rf-detr-detection-segmentation`](../skills/detection/rf-detr-detection-segmentation/)
- **Purpose**: Transformer object detection and RF-DETR-Seg instance segmentation
- **Output modes**:
  - `detection`: bounding boxes only
  - `segmentation`: masks and boxes
  - `both`: uses RF-DETR-Seg to emit masks and boxes from one forward pass

#### RF-DETR Detection Sizes

| App size | Hugging Face model |
|---|---|
| `nano` | `Roboflow/rf-detr-nano` |
| `small` | `Roboflow/rf-detr-small` |
| `medium` | `Roboflow/rf-detr-medium` |
| `large` | `Roboflow/rf-detr-large` |
| `base` | `Roboflow/rf-detr-base` |
| `base2` | `Roboflow/rf-detr-base-2` |

#### RF-DETR-Seg Segmentation Sizes

| App size | Hugging Face model |
|---|---|
| `preview` | `Roboflow/rf-detr-seg-preview` |
| `nano` | `Roboflow/rf-detr-seg-nano` |
| `small` | `Roboflow/rf-detr-seg-small` |
| `medium` | `Roboflow/rf-detr-seg-medium` |
| `large` | `Roboflow/rf-detr-seg-large` |
| `xlarge` | `Roboflow/rf-detr-seg-xlarge` |
| `xxlarge` | `Roboflow/rf-detr-seg-xxlarge` |
| `segmentation` | `Roboflow/rf-detr-segmentation` |

Note: upstream RF-DETR detection `XL` and `2XL` exist in the `rfdetr[plus]` package, but they are not installed by this skill by default because of their separate PML 1.0 license path.

## Hardware Support In This Workspace

| Backend | Used by |
|---|---|
| CUDA / TensorRT | `perception-core`, `rf-detr-detection-segmentation` via PyTorch CUDA |
| Apple MPS / CoreML | `perception-core`, `rf-detr-detection-segmentation` via PyTorch MPS |
| OpenVINO | `perception-core` Intel path |
| ROCm | `perception-core` where ROCm packages are installed |
| Coral Edge TPU | _Planned_ — Hawkeye supports the LiteRT delegate but no Coral skill ships yet |
| CPU | All Python detection skills as fallback, with lower throughput |
