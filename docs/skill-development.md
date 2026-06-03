# Skill Development Guide

This guide explains how to create a new skill for the Hawkeye skill catalog.

## What is a Skill?

A skill is a self-contained folder that provides an AI capability to [SharpAI Hawkeye](https://sharpai.org). Skills communicate with Hawkeye via **JSON lines** over stdin/stdout.

## Skill Structure

```
skills/<category>/<skill-name>/
├── SKILL.md              # Manifest + setup instructions
├── config.yaml           # Configuration schema for Hawkeye UI
├── deploy.sh             # Zero-assumption installer
├── requirements.txt      # Default Python dependencies
├── requirements_cuda.txt # NVIDIA GPU dependencies
├── requirements_rocm.txt # AMD GPU dependencies
├── requirements_mps.txt  # Apple Silicon dependencies
├── requirements_cpu.txt  # CPU-only dependencies
├── scripts/
│   └── main.py           # Entry point
├── assets/
│   └── icon.png          # 64×64 icon (optional)
└── tests/
    └── test_main.py      # Tests (optional)
```

## SKILL.md Format

The `SKILL.md` file has two parts:
1. **YAML frontmatter** — machine-readable parameters and capabilities
2. **Markdown body** — human/LLM-readable setup instructions

```yaml
---
name: my-skill
description: "What this skill does"
version: 1.0.0

parameters:
  - name: model
    label: "Model"
    type: select
    options: ["option1", "option2"]
    default: "option1"
    group: Model

capabilities:
  my_capability:
    script: scripts/main.py
    description: "What this capability does"
---

# My Skill

Description of the skill.

## Setup

Step-by-step setup instructions that SharpAI Hawkeye's
LLM agent can read and execute.
```

## Parameter Types

| Type | Renders As | Example |
|------|-----------|---------|
| `string` | Text input | Email, URL, API key |
| `password` | Masked input | Passwords, tokens |
| `number` | Number input with min/max | Confidence threshold |
| `boolean` | Toggle switch | Enable/disable feature |
| `select` | Dropdown | Model selection |
| `url` | URL input with validation | Server address |
| `camera_select` | Camera picker | Target cameras |

## config.yaml — Configuration Schema

Defines user-configurable options shown in the Hawkeye Skills UI. Parsed by `parseConfigYaml()`.

```yaml
params:
  - key: auto_start
    label: Auto Start
    type: boolean
    default: false
    description: "Start automatically on Hawkeye launch"

  - key: model_size
    label: Model Size
    type: select
    default: nano
    description: "Choose model variant"
    options:
      - { value: nano, label: "Nano (fastest)" }
      - { value: small, label: "Small (balanced)" }

  - key: confidence
    label: Confidence
    type: number
    default: 0.5
    description: "Min confidence (0.1–1.0)"
```

### Reserved Keys

| Key | Type | Behavior |
|-----|------|----------|
| `auto_start` | boolean | Hawkeye auto-starts the skill on boot when `true` |

## deploy.sh — Zero-Assumption Installer

Bootstraps the environment from scratch. Must handle:

1. **Find Python** — check system → conda → pyenv
2. **Create venv** — isolated `.venv/` inside skill directory
3. **Detect GPU** — CUDA → ROCm → MPS → CPU fallback
4. **Install deps** — from matching `requirements_<backend>.txt`
5. **Verify** — import test

Emit JSONL progress for Hawkeye UI:
```bash
echo '{"event": "progress", "stage": "gpu", "backend": "mps"}'
echo '{"event": "complete", "backend": "mps", "message": "Installed!"}'
```

## Environment Variables

Hawkeye injects these into every skill process:

| Variable | Description |
|----------|-------------|
| `HAWKEYE_SKILL_ID` | Skill identifier |
| `HAWKEYE_SKILL_PARAMS` | JSON string of user config values |
| `HAWKEYE_GATEWAY_URL` | LLM gateway URL |
| `HAWKEYE_VLM_URL` | VLM server URL |
| `HAWKEYE_LLM_MODEL` | Active LLM model name |
| `HAWKEYE_VLM_MODEL` | Active VLM model name |
| `PYTHONUNBUFFERED` | Set to `1` for real-time output |

## JSON Lines Protocol

Scripts communicate with Hawkeye via stdin/stdout. Each line is a JSON object.

### Script → Hawkeye (stdout)

```jsonl
{"event": "ready", "model": "...", "device": "..."}
{"event": "detections", "camera_id": "...", "objects": [...]}
{"event": "error", "message": "...", "retriable": true}
```

### Hawkeye → Script (stdin)

```jsonl
{"event": "frame", "camera_id": "...", "frame_path": "...", "timestamp": "..."}
{"command": "stop"}
```

## Categories

| Category | Directory | Use For |
|----------|-----------|---------|
| `detection` | `skills/detection/` | Object detection, person recognition |
| `analysis` | `skills/analysis/` | VLM scene understanding, offline analysis |
| `camera-providers` | `skills/camera-providers/` | Blink, Eufy, Ring, Reolink, Tapo |
| `streaming` | `skills/streaming/` | RTSP/WebRTC via go2rtc |
| `channels` | `skills/channels/` | Messaging: Matrix, LINE, Signal, Telegram |
| `automation` | `skills/automation/` | MQTT, webhooks, HA triggers |
| `integrations` | `skills/integrations/` | Home Assistant bridge |

## Testing Locally

```bash
# Test your skill without Hawkeye by piping JSON:
echo '{"event": "frame", "camera_id": "test", "frame_path": "/tmp/test.jpg"}' | python scripts/main.py
```

## skills.json — Catalog Registration

Register skills in the repo root `skills.json`:

```json
{
  "skills": [
    {
      "id": "my-skill",
      "name": "My Skill",
      "description": "What it does",
      "category": "detection",
      "tags": ["tag1"],
      "path": "skills/detection/my-skill",
      "status": "testing",
      "platforms": ["darwin-arm64", "linux-x64"]
    }
  ]
}
```

### Status Values

| Status | Emoji | Meaning |
|--------|-------|---------|
| `ready` | ✅ | Production-quality, tested |
| `testing` | 🧪 | Functional, needs validation |
| `experimental` | ⚗️ | Proof of concept |
| `planned` | 📐 | Not yet implemented |

## Reference

See [`skills/detection/perception-core/`](../skills/detection/perception-core/) for a complete working example.
