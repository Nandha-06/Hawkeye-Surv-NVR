#!/usr/bin/env python3
"""
Home Assistant Bridge Skill — SharpAI Hawkeye.
Connects to the Hawkeye WebSocket gateway, listens for detections and threat analysis,
and forwards filtered events to Home Assistant REST API or Webhook.
"""

import os
import sys
import json
import time
import signal
from pathlib import Path
import requests
import websocket
import threading

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
        from protocol import emit, log, setup_log_prefix, event_error, event_ready, event_progress  # noqa: E402
        setup_log_prefix("HomeAssistant-Bridge")
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)
    def log(msg):
        print(f"[HomeAssistant-Bridge] {msg}", file=sys.stderr, flush=True)

# Print to stdout helper
# Print to stderr helper
def load_config():
    # SvelteKit passes params in HAWKEYE_SKILL_PARAMS env var
    env_params = os.environ.get("HAWKEYE_SKILL_PARAMS")
    config = {}
    if env_params:
        try:
            config = json.loads(env_params)
        except json.JSONDecodeError as e:
            log(f"Warning: Failed to decode HAWKEYE_SKILL_PARAMS: {e}")

    # Fallback / Defaults
    hass_url = config.get("hass_url") or "http://homeassistant.local:8123"
    access_token = config.get("access_token") or ""
    webhook_id = config.get("webhook_id") or ""
    
    # trigger_threat_levels can be a comma-separated string or a list
    ttl = config.get("trigger_threat_levels") or "warning,critical"
    if isinstance(ttl, str):
        trigger_threat_levels = [x.strip().lower() for x in ttl.split(",") if x.strip()]
    else:
        trigger_threat_levels = [str(x).strip().lower() for x in ttl]

    # trigger_classes can be a comma-separated string or a list
    tc = config.get("trigger_classes") or "person"
    if isinstance(tc, str):
        trigger_classes = [x.strip().lower() for x in tc.split(",") if x.strip()]
    else:
        trigger_classes = [str(x).strip().lower() for x in tc]

    # Get WebSocket URL and force secure WebSocket (wss://) protocol
    ws_url = os.environ.get("HAWKEYE_WS_URL") or "ws://localhost:5173/api/ws"
    if ws_url.startswith("ws://"):
        ws_url = "wss://" + ws_url[5:]

    return {
        "hass_url": hass_url.rstrip("/"),
        "access_token": access_token,
        "webhook_id": webhook_id,
        "trigger_threat_levels": trigger_threat_levels,
        "trigger_classes": trigger_classes,
        "ws_url": ws_url
    }

def forward_to_home_assistant(config, payload):
    hass_url = config["hass_url"]
    webhook_id = config["webhook_id"]
    access_token = config["access_token"]

    if webhook_id:
        url = f"{hass_url}/api/webhook/{webhook_id}"
        headers = {"Content-Type": "application/json"}
        try:
            log(f"Posting to Webhook: {url}")
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            log(f"Error posting to Home Assistant webhook: {e}")
            emit({"event": "error", "message": f"HASS webhook post failed: {e}", "retriable": True})
            return False
    elif access_token:
        url = f"{hass_url}/api/events/sharpai_detection"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        try:
            log(f"Posting to REST Event Bus: {url}")
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            log(f"Error posting to Home Assistant REST API: {e}")
            emit({"event": "error", "message": f"HASS event post failed: {e}", "retriable": True})
            return False
    else:
        log("No HASS access token or webhook ID configured. Cannot forward event.")
        emit({"event": "error", "message": "No HASS access token or webhook ID configured. Forwarding skipped.", "retriable": False})
        return False

def run_bridge():
    config = load_config()
    
    log(f"Loaded config: WS={config['ws_url']}, HASS={config['hass_url']}, Webhook={'Set' if config['webhook_id'] else 'None'}, Token={'Set' if config['access_token'] else 'None'}")
    log(f"Rules: threat_levels={config['trigger_threat_levels']}, classes={config['trigger_classes']}")

    running = True
    ws_app = None

    def handle_signal(sig, frame):
        nonlocal running
        log("Shutdown signal received. Exiting.")
        running = False
        if ws_app:
            ws_app.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Background stdin reader for stop command
    def read_stdin_thread():
        log("Started stdin listener thread.")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
                if cmd.get("command") == "stop":
                    log("Received stop command on stdin. Stopping websocket client.")
                    nonlocal running
                    running = False
                    if ws_app:
                        ws_app.close()
                    break
            except Exception as e:
                log(f"Error parsing stdin: {e}")

    stdin_thread = threading.Thread(target=read_stdin_thread, daemon=True)
    stdin_thread.start()

    while running:
        emit({"event": "progress", "stage": "connect", "message": f"Connecting to Hawkeye Gateway at {config['ws_url']}..."})
        
        def on_open(ws):
            log("WebSocket connection established.")
            emit({"event": "ready", "message": "Connected to Hawkeye Gateway and listening for events."})

        def on_message(ws, message):
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                return

            event_type = data.get("event")
            camera_id = data.get("camera_id") or "unknown"
            timestamp = data.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            if event_type == "detections":
                objects = data.get("objects", [])
                matching_objects = []
                matching_classes = []
                for obj in objects:
                    obj_class = obj.get("class", "").strip().lower()
                    if obj_class in config["trigger_classes"]:
                        matching_objects.append(obj)
                        matching_classes.append(obj_class)

                if matching_objects:
                    matching_classes_set = list(set(matching_classes))
                    log(f"Filtered detection matched: {matching_classes_set} on camera {camera_id}")
                    payload = {
                        "event_type": "visual_detection",
                        "threat_level": "info",
                        "message": f"Detected objects: {', '.join(matching_classes_set)}",
                        "camera": camera_id,
                        "timestamp": timestamp,
                        "objects": matching_objects
                    }
                    success = forward_to_home_assistant(config, payload)
                    if success:
                        emit({
                            "event": "forwarded",
                            "event_type": "visual_detection",
                            "camera_id": camera_id,
                            "objects": matching_classes_set
                        })

            elif event_type == "threat_analysis":
                alert_type = str(data.get("alert_type", "info")).strip().lower()
                message_text = data.get("message", "")
                
                if alert_type in config["trigger_threat_levels"]:
                    log(f"Filtered threat matched: [{alert_type.upper()}] {message_text} on camera {camera_id}")
                    payload = {
                        "event_type": "threat_alert",
                        "threat_level": alert_type,
                        "message": message_text,
                        "camera": camera_id,
                        "timestamp": timestamp
                    }
                    success = forward_to_home_assistant(config, payload)
                    if success:
                        emit({
                            "event": "forwarded",
                            "event_type": "threat_alert",
                            "camera_id": camera_id,
                            "threat_level": alert_type
                        })

        def on_error(ws, error):
            log(f"WebSocket error: {error}")
            emit({"event": "error", "message": f"WebSocket error: {error}", "retriable": True})

        def on_close(ws, close_status_code, close_msg):
            log(f"WebSocket connection closed: {close_status_code} - {close_msg}")
            emit({"event": "error", "message": "Connection lost. Reconnecting in 5s...", "retriable": True})

        try:
            ws_app = websocket.WebSocketApp(
                config["ws_url"],
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws_app.run_forever(ping_interval=10, ping_timeout=5)
        except Exception as e:
            log(f"Connection exception: {e}")
            emit({"event": "error", "message": f"Connection exception: {e}. Retrying in 5s...", "retriable": True})
            
        if running:
            time.sleep(5)

if __name__ == "__main__":
    run_bridge()
