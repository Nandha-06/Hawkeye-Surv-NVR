# Project: Hawkeye NVR and AI Surveillance System

## Architecture
Hawkeye is a local-AI NVR and surveillance application structured into:
1. **Rust Backend (Tauri / Axum Server)**:
   - Manages camera configs, recording schedules, SQL database indexes.
   - Spawns and manages `go2rtc` as a sidecar process for RTSP stream routing.
   - Controls background `ffmpeg` processes for continuous camera stream recording into 5-second segments.
   - Handles HLS VOD playlist generation (`.m3u8`) and serves video segments over HTTP.
2. **Python CV Pipeline (perception-core)**:
   - Runs as a standalone skill communicating with the Tauri backend via stdin/stdout JSONL messages.
   - Implements motion gating, YOLO-based object detection, SCRFD face detection, MobileFaceNet face recognition, and local VLM threat analysis.
   - Saves face crops to `.data/crops/` and event snapshots to `.data/snapshots/`.
3. **Frontend (SvelteKit)**:
   - Desktop and Web GUI for live streaming, history playback, event log viewing, and configuration.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Diagnostic Path Fixes | Update scratch scripts to eliminate hardcoded DeepCamera paths and verify running tools | None | PLANNED |
| 2 | NVR Bug Fix | Fix .ts vs .mp4 filename timestamp parser extension mismatch in recording_manager.rs | M1 | PLANNED |
| 3 | CV Pipeline Integration | Verify motion, object detection, and face recognition output formats and hardware optimization | M1 | PLANNED |
| 4 | Snapshot & Crop Validation | Validate snapshot extraction and aligned face cropping logic | M2, M3 | PLANNED |
| 5 | E2E Testing Track | Design and implement Tier 1-4 programmatic test suite (~93 cases) and publish TEST_READY.md | M1 | PLANNED |
| 6 | Implementation Verification | Run E2E test suite against implementation and verify 100% pass | M2, M3, M4, M5 | PLANNED |
| 7 | Performance Benchmarking | Run benchmarking script measuring CV pipeline FPS/latency and compile reports | M6 | PLANNED |

## Interface Contracts
### Tauri/Axum ↔ Python Skill (perception-core)
- **Input (stdin)**: `{"event": "frame", "frame_id": i32, "camera_id": "string", "timestamp": "ISO_8601", "frame_path": "string", "width": i32, "height": i32}`
- **Output (stdout)**:
  - Ready: `{"event": "ready", "model": "string", "device": "string", "backend": "string", "format": "string", "gpu": "string", "classes": i32, "fps": i32}`
  - Detections: `{"event": "detections", "frame_id": i32, "camera_id": "string", "timestamp": "ISO_8601", "objects": [{"class": "string", "confidence": f64, "bbox": [f64, f64, f64, f64]}]}`
  - Perf Stats: `{"event": "perf_stats", "total_frames": i32, "timings_ms": {"inference": {"avg": f64, "p50": f64, "p95": f64}}}`
  - Error: `{"event": "error", "message": "string", "retriable": bool}`
