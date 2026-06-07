---
name: rf-detr-detection-segmentation
description: "RF-DETR perception skill with object detection and optional instance segmentation masks"
version: 1.0.0
entry: scripts/detect.py
deploy: deploy.sh

requirements:
  python: ">=3.10"
  torch: ">=2.6.0,<3.0.0"
  transformers: ">=5.9.0,<6.0.0"
  platforms: ["linux", "macos", "windows"]

parameters:
  - name: task_mode
    label: "Output Mode"
    type: select
    options: ["both", "segmentation", "detection"]
    default: "both"
    group: Model
  - name: model_size
    label: "Detection Model Size"
    type: select
    options: ["nano", "small", "medium", "large", "base", "base2"]
    default: "nano"
    group: Model
  - name: seg_model_size
    label: "Segmentation Model Size"
    type: select
    options: ["nano", "small", "medium", "large", "xlarge", "xxlarge", "preview", "segmentation"]
    default: "nano"
    group: Model
  - name: confidence
    label: "Confidence Threshold"
    type: number
    default: 0.55
    group: Model

capabilities:
  live_detection:
    script: scripts/detect.py
    description: "RF-DETR object detection with bbox output"
  instance_segmentation:
    script: scripts/detect.py
    description: "RF-DETR-Seg instance masks plus bbox output"
---

# RF-DETR Detection + Segmentation

This skill runs RF-DETR through Hugging Face Transformers and keeps the existing Hawkeye JSONL protocol.

## How Combined Mode Works

Set `task_mode: both` to load an RF-DETR-Seg checkpoint. The segmentation model emits an instance mask and a bounding box for each object, so the app gets object detection and segmentation from one forward pass instead of running two models.

Use `task_mode: detection` when you only need bounding boxes and want the fastest RF-DETR detector variant.

## Deployable Model Sizes

### Object Detection Checkpoints

| Size | Hugging Face model | Notes |
|---|---|---|
| nano | `Roboflow/rf-detr-nano` | Fastest detector |
| small | `Roboflow/rf-detr-small` | Better accuracy |
| medium | `Roboflow/rf-detr-medium` | Balanced default for GPU |
| large | `Roboflow/rf-detr-large` | Highest Apache-licensed detector size in this skill |
| base | `Roboflow/rf-detr-base` | Original base checkpoint |
| base2 | `Roboflow/rf-detr-base-2` | Updated base checkpoint |

RF-DETR-XL and RF-DETR-2XL detection models exist in the upstream RF-DETR package behind `rfdetr[plus]` and use the PML 1.0 license, so this skill does not install them by default.

### Instance Segmentation Checkpoints

| Size | Hugging Face model | Notes |
|---|---|---|
| preview | `Roboflow/rf-detr-seg-preview` | Preview alias |
| nano | `Roboflow/rf-detr-seg-nano` | Fastest segmentation |
| small | `Roboflow/rf-detr-seg-small` | Good edge default |
| medium | `Roboflow/rf-detr-seg-medium` | Balanced segmentation |
| large | `Roboflow/rf-detr-seg-large` | Higher accuracy |
| xlarge | `Roboflow/rf-detr-seg-xlarge` | Accuracy-focused |
| xxlarge | `Roboflow/rf-detr-seg-xxlarge` | Most accurate segmentation |
| segmentation | `Roboflow/rf-detr-segmentation` | General segmentation alias |

## Output

Objects always include a COCO class, confidence, and `bbox` in `[x1, y1, x2, y2]` pixel format. Segmentation objects additionally include a polygon:

```json
{
  "class": "person",
  "confidence": 0.86,
  "bbox": [100, 50, 300, 420],
  "segmentation": {
    "type": "polygon",
    "points": [[120, 60], [280, 62], [298, 418], [110, 410]],
    "area": 42100
  }
}
```

## Performance And Accuracy Defaults

- Combined mode uses one RF-DETR-Seg model for masks and boxes, avoiding duplicate detection and segmentation passes.
- `enable_motion_gating` is enabled by default to skip inference on static camera frames.
- CUDA half precision is enabled by default for throughput and memory reduction.
- `torch_compile` is available for CUDA deployments but disabled by default because it adds startup time.
- Keep the class filter narrow for security cameras, for example `person,car,dog,cat`, to reduce downstream alert noise.
