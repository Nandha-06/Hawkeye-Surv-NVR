"""
protocol.py — Shared event protocol for Hawkeye skills.

All skills communicate with the Rust backend (src-tauri/src/skills_manager.rs)
via line-delimited JSON events on stdout. This module provides:

  1. Low-level primitives (emit, log) that match what every skill already does
  2. Typed helpers for the common event types (error, ready, progress, etc.)
     so callers don't have to remember the field shape

The wire format is intentionally identical to what the skills already emit,
so this module is a drop-in replacement for the per-skill boilerplate.

Usage:
    from protocol import emit, log, setup_log_prefix
    from protocol import event_error, event_ready, event_progress, event_detections

    setup_log_prefix("Telegram-Channel")
    log("starting up")
    event_ready(channel="telegram", version="1.0")
    event_error("connection lost", retriable=True)
"""
import json
import sys


# Per-skill log prefix. Skills call setup_log_prefix("MySkill") in main().
_log_prefix = "skill"


def setup_log_prefix(prefix: str) -> None:
    """Set the prefix used by log(). Call once at skill startup."""
    global _log_prefix
    _log_prefix = prefix


# ─── Low-level primitives ──────────────────────────────────────────────────


def emit(event: dict) -> None:
    """Emit a JSON event to stdout (one line, flushed).

    The Rust WS handler reads these lines, parses them, and forwards to
    connected clients. Any dict with at least an "event" key is valid.
    """
    print(json.dumps(event, separators=(",", ":")), flush=True)


def log(msg: str) -> None:
    """Log a message to stderr with the configured prefix.

    Stderr is not read by the Rust handler — it's only for human debugging.
    """
    print(f"[{_log_prefix}] {msg}", file=sys.stderr, flush=True)


# ─── Typed event helpers ───────────────────────────────────────────────────
# Field names use camelCase to match the rest of the Hawkeye WS protocol.


def event_error(message: str, retriable: bool = False, **extra) -> None:
    """Emit an error event. retriable=True means the supervisor may restart."""
    emit({"event": "error", "message": message, "retriable": retriable, **extra})


def event_ready(**fields) -> None:
    """Emit a ready event after successful initialization."""
    emit({"event": "ready", **fields})


def event_progress(stage: str, message: str, **extra) -> None:
    """Emit a progress event for long-running operations (model load, etc.)."""
    emit({"event": "progress", "stage": stage, "message": message, **extra})


def event_detections(camera_id: str, objects: list, **extra) -> None:
    """Emit a detection result event with object bounding boxes.

    Each object should have: label, confidence, bbox (xyxy pixels).
    """
    emit({"event": "detections", "cameraId": camera_id, "objects": objects, **extra})


def event_alert(
    alert_type: str,
    camera_id: str,
    message: str,
    confidence: float,
    **extra,
) -> None:
    """Emit an alert event for high-confidence detections."""
    emit({
        "event": "alert",
        "alertType": alert_type,
        "cameraId": camera_id,
        "message": message,
        "confidence": confidence,
        **extra,
    })


def event_log_message(skill_id: str, camera_id: str, message: str) -> None:
    """Emit a log event for forwarding to the UI's log panel."""
    emit({"event": "log", "skillId": skill_id, "cameraId": camera_id, "message": message})


def event_live_frame(frame_id, camera_id: str, timestamp: str, frame_b64: str) -> None:
    """Emit a live frame event with base64-encoded JPEG."""
    emit({
        "event": "live_frame",
        "frameId": frame_id,
        "cameraId": camera_id,
        "timestamp": timestamp,
        "frame": frame_b64,
    })


def event_perf_stats(stats: dict) -> None:
    """Emit a perf_stats event (CPU/GPU/inference/timings)."""
    emit({"event": "perf_stats", **stats})


def event_live_stream(camera_id: str, camera_name: str, url: str) -> None:
    """Emit a live stream URL event (e.g. go2rtc stream ready)."""
    emit({
        "event": "live_stream",
        "cameraId": camera_id,
        "cameraName": camera_name,
        "url": url,
    })


def event_snapshot(camera_id: str, path: str) -> None:
    """Emit a snapshot saved event with file path."""
    emit({"event": "snapshot", "cameraId": camera_id, "path": path})


def event_message_sent(channel: str, **extra) -> None:
    """Emit a channel send confirmation (telegram/matrix/signal/line)."""
    emit({"event": "message_sent", "channel": channel, **extra})


def event_webhook_sent(status_code: int) -> None:
    """Emit a webhook delivery confirmation."""
    emit({"event": "webhook_sent", "status_code": status_code})


def event_published(topic: str) -> None:
    """Emit an MQTT publish confirmation."""
    emit({"event": "published", "topic": topic})


def event_ha_event_fired(event_type: str, **extra) -> None:
    """Emit a Home Assistant event fire confirmation."""
    emit({"event": "ha_event_fired", "eventType": event_type, **extra})


def event_threat_analysis(
    frame_id: int,
    camera_id: str,
    timestamp: str,
    alert_type: str,
    message: str,
    snapshot_path: str = None,
    **extra,
) -> None:
    """Emit a VLM threat analysis event."""
    payload = {
        "event": "threat_analysis",
        "frame_id": frame_id,
        "cameraId": camera_id,
        "timestamp": timestamp,
        "alert_type": alert_type,
        "message": message,
    }
    if snapshot_path is not None:
        payload["snapshot_path"] = snapshot_path
    payload.update(extra)
    emit(payload)
