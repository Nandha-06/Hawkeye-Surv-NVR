#!/usr/bin/env python3
"""
Moondream Live Video Analyzer Skill — SharpAI Hawkeye.
Ported from Moondream Live Video Player.

Queries the Moondream Cloud VLM or the local Hawkeye VLM in the background to
narrate visual summaries and check for predefined/custom gestures or action triggers.
"""

import sys
import os
import json
import argparse
import signal
import time
import base64
import requests
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

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
        from protocol import emit, log, setup_log_prefix, event_error, event_ready, event_progress, event_detections  # noqa: E402
        setup_log_prefix("VisualEventAnalyzer")
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)
    def log(msg):
        print(f"[VisualEventAnalyzer] {msg}", file=sys.stderr, flush=True)

# Predefined trigger mappings matching the React application
PREDEFINED_TRIGGERS = {
    "smiling": {
        "query": "is anyone smiling? yes or no",
        "trigger_text": "yes",
        "notification_text": "😊 Smile Detected!"
    },
    "thumbs-up": {
        "query": "is anyone giving a thumbs-up gesture? yes or no",
        "trigger_text": "yes",
        "notification_text": "👍 Thumbs Up Detected!"
    },
    "tongue-out": {
        "query": "is anyone sticking their tongue out? yes or no",
        "trigger_text": "yes",
        "notification_text": "👅 Tongue Out Detected!"
    },
    "peace-sign": {
        "query": "is anyone making a peace sign? yes or no",
        "trigger_text": "yes",
        "notification_text": "✌️ Peace Sign Detected!"
    },
    "drinking-water": {
        "query": "is anyone drinking water? yes or no",
        "trigger_text": "yes",
        "notification_text": "💧 Drinking Water Detected!"
    }
}


def parse_args():
    parser = argparse.ArgumentParser(description="Moondream Live Video Analyzer Skill")
    parser.add_argument("--config", type=str, help="Path to config JSON file")
    return parser.parse_args()


def load_config(args):
    # Hawkeye passes parameter values in the environment variable HAWKEYE_SKILL_PARAMS
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

    # Defaults
    return {
        "auto_start": True,
        "predefined_trigger": "smiling",
        "moondream_api_key": "",
        "query_summary": "summarize what you see in one short sentence",
        "query_trigger": "is anyone smiling? yes or no",
        "trigger_text": "yes",
        "notification_text": "😊 Smile Detected!",
        "alert_severity": "warning",
        "cooldown": 5,
        "fps": 1
    }




# VLM worker thread pool
vlm_executor = ThreadPoolExecutor(max_workers=2)
vlm_active = False
last_vlm_time = 0.0
vlm_lock = threading.Lock()


def query_vlm_http(base64_image, question, api_key, vlm_url, vlm_model):
    """
    Submits a visual query to Moondream Cloud API (if key provided) or local Hawkeye VLM.
    """
    # 1. Cloud Moondream VLM
    if api_key and api_key.strip():
        endpoint = "https://api.moondream.ai/v1/query"
        headers = {
            "Content-Type": "application/json",
            "X-Moondream-Auth": api_key.strip()
        }
        payload = {
            "image_url": f"data:image/jpeg;base64,{base64_image}",
            "question": question
        }
        
        response = requests.post(endpoint, headers=headers, json=payload, timeout=25)
        if response.status_code == 200:
            return response.json().get("answer", "").strip()
        else:
            raise Exception(f"Moondream API HTTP {response.status_code}: {response.text}")

    # 2. Local Hawkeye VLM (OpenAI compatible)
    else:
        endpoint = f"{vlm_url.rstrip('/')}/v1/chat/completions"
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
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        },
                        {
                            "type": "text",
                            "text": question
                        }
                    ]
                }
            ],
            "max_tokens": 150,
            "temperature": 0.2
        }
        
        response = requests.post(endpoint, headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            res_data = response.json()
            return res_data["choices"][0]["message"]["content"].strip()
        else:
            raise Exception(f"Hawkeye VLM API HTTP {response.status_code}: {response.text}")


def run_inference_task(frame_path, frame_id, camera_id, timestamp, config, vlm_url, vlm_model):
    global vlm_active, last_vlm_time
    
    try:
        # Load and base64-encode the frame image
        with open(frame_path, "rb") as f:
            base64_image = base64.b64encode(f.read()).decode('utf-8')
            
        # Determine actual query strings based on predefined vs custom trigger settings
        trigger_type = config.get("predefined_trigger", "smiling")
        api_key = config.get("moondream_api_key", "").strip()
        
        summary_query = config.get("query_summary", "summarize what you see in one short sentence").strip()
        
        trigger_future = None
        if trigger_type != "none":
            if trigger_type in PREDEFINED_TRIGGERS:
                predef = PREDEFINED_TRIGGERS[trigger_type]
                trigger_query = predef["query"]
                trigger_match = predef["trigger_text"]
                alert_msg = predef["notification_text"]
                severity = "warning"
            else:
                # Custom trigger mode
                trigger_query = config.get("query_trigger", "is anyone smiling? yes or no").strip()
                trigger_match = config.get("trigger_text", "yes").strip()
                alert_msg = config.get("notification_text", "😊 Smile Detected!").strip()
                severity = config.get("alert_severity", "warning").strip()

        log(f"Submitting frame {frame_id} (Summary Query: '{summary_query}'" + (f", Trigger Query: '{trigger_query}'" if trigger_type != "none" else "") + ") to VLM...")

        # Run queries using python threads
        summary_future = vlm_executor.submit(query_vlm_http, base64_image, summary_query, api_key, vlm_url, vlm_model)
        if trigger_type != "none":
            trigger_future = vlm_executor.submit(query_vlm_http, base64_image, trigger_query, api_key, vlm_url, vlm_model)

        summary_ans = summary_future.result()
        trigger_ans = trigger_future.result() if trigger_future else None

        log(f"VLM results: Summary: '{summary_ans}'" + (f" | Trigger: '{trigger_ans}'" if trigger_future else ""))

        # 1. Emit continuous visual summary description
        if summary_ans:
            emit({
                "event": "threat_analysis",
                "frame_id": frame_id,
                "camera_id": camera_id,
                "timestamp": timestamp,
                "alert_type": "info",
                "message": f"Summary: {summary_ans}"
            })

        # 2. Check trigger conditions and emit alert if matched
        if trigger_future and trigger_ans:
            is_triggered = trigger_match.lower() in trigger_ans.lower()
            if is_triggered:
                emit({
                    "event": "threat_analysis",
                    "frame_id": frame_id,
                    "camera_id": camera_id,
                    "timestamp": timestamp,
                    "alert_type": severity,
                    "message": alert_msg
                })
                log(f"*** ALERT TRIGGERED: {alert_msg} ***")

    except Exception as e:
        log(f"VLM query failed: {e}")
        emit({
            "event": "threat_analysis",
            "frame_id": frame_id,
            "camera_id": camera_id,
            "timestamp": timestamp,
            "alert_type": "info",
            "message": f"VLM Error: {str(e)}"
        })
    finally:
        # Clear frame cache in server by emitting empty detections
        emit({
            "event": "detections",
            "frame_id": frame_id,
            "camera_id": camera_id,
            "timestamp": timestamp,
            "objects": []
        })
        
        with vlm_lock:
            vlm_active = False
            last_vlm_time = time.time()


def main():
    global vlm_active, last_vlm_time
    
    args = parse_args()
    config = load_config(args)
    
    fps = config.get("fps", 1)
    cooldown = float(config.get("cooldown", 5.0))
    
    # Retrieve Hawkeye VLM backend settings
    vlm_url = os.environ.get("HAWKEYE_VLM_URL") or "http://localhost:5405"
    vlm_model = os.environ.get("HAWKEYE_VLM_MODEL") or ""
    
    # Emit progress and ready events
    emit({"event": "progress", "stage": "init", "message": "Starting Visual Event Analyzer Skill..."})
    time.sleep(0.2)
    
    ready_event = {
        "event": "ready",
        "model": "visual-event-analyzer",
        "model_size": "nano",
        "device": "cpu",
        "backend": "cpu",
        "format": "api",
        "gpu": "none",
        "classes": 0,
        "fps": fps,
        "model_load_ms": 0.0,
        "available_sizes": ["nano"]
    }
    emit(ready_event)
    log(f"Skill loaded successfully. Listening on stdin. Cooldown: {cooldown}s | FPS: {fps}")

    # Graceful shutdown handler
    def handle_signal(signum, frame):
        log("Received terminate signal, shutting down.")
        vlm_executor.shutdown(wait=False)
        sys.exit(0)
        
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Frame consumer loop (JSONL stdin/stdout protocol)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if msg.get("command") == "stop":
            break

        if msg.get("event") == "frame":
            frame_path = msg.get("frame_path")
            frame_id = msg.get("frame_id")
            camera_id = msg.get("camera_id", "unknown")
            timestamp = msg.get("timestamp", "")

            if not frame_path or not Path(frame_path).exists():
                # Make sure to clear cache in server even on error
                emit({
                    "event": "detections",
                    "frame_id": frame_id,
                    "camera_id": camera_id,
                    "timestamp": timestamp,
                    "objects": []
                })
                continue

            # Query VLM in a background thread if not active and cooldown finished
            with vlm_lock:
                now = time.time()
                if not vlm_active and (now - last_vlm_time >= cooldown):
                    vlm_active = True
                    last_vlm_time = now  # prevent duplicate starts
                    
                    # Submit visual analysis tasks to the ThreadPoolExecutor
                    vlm_executor.submit(
                        run_inference_task,
                        frame_path,
                        frame_id,
                        camera_id,
                        timestamp,
                        config,
                        vlm_url,
                        vlm_model
                    )
                else:
                    # Not querying VLM due to cooldown/active task, clear frame cache
                    emit({
                        "event": "detections",
                        "frame_id": frame_id,
                        "camera_id": camera_id,
                        "timestamp": timestamp,
                        "objects": []
                    })

    vlm_executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
