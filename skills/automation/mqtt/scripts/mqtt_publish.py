#!/usr/bin/env python3
"""
MQTT Automation Skill — Publish Hawkeye events to MQTT broker.
"""

import sys
import json
import argparse
import signal
from pathlib import Path

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
        from protocol import emit, event_error, event_ready, event_published  # noqa: E402
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="MQTT Automation")
    parser.add_argument("--config", type=str)
    parser.add_argument("--broker", type=str, default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username", type=str)
    parser.add_argument("--password", type=str)
    parser.add_argument("--topic-prefix", type=str, default="hawkeye")
    parser.add_argument("--use-tls", action="store_true")
    return parser.parse_args()


def load_config(args):
    if args.config and Path(args.config).exists():
        with open(args.config) as f:
            cfg = json.load(f)
            if "use_tls" not in cfg:
                cfg["use_tls"] = False
            return cfg
    return {
        "broker": args.broker,
        "port": args.port,
        "username": args.username,
        "password": args.password,
        "topic_prefix": args.topic_prefix,
        "use_tls": args.use_tls,
    }


def main():
    args = parse_args()
    config = load_config(args)

    try:
        import paho.mqtt.client as mqtt
        import ssl

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        if config.get("use_tls") or config.get("port") == 8883:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

        if config.get("username"):
            client.username_pw_set(config["username"], config.get("password"))

        client.connect(config["broker"], config.get("port", 1883), 60)
        client.loop_start()

        prefix = config.get("topic_prefix", "hawkeye")
        emit({"event": "ready", "broker": config["broker"], "topic_prefix": prefix})
    except Exception as e:
        emit({"event": "error", "message": f"MQTT connection failed: {e}", "retriable": False})
        sys.exit(1)

    running = True
    def handle_signal(s, f):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    for line in sys.stdin:
        if not running:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if msg.get("command") == "stop":
            break

        event_type = msg.get("event")
        camera_id = msg.get("camera_id", "unknown")

        if event_type in ("clip_completed", "person_detected", "alert", "camera_offline"):
            topic = f"{prefix}/{camera_id}/{event_type}"
            payload = json.dumps(msg)
            client.publish(topic, payload, qos=1)
            emit({"event": "published", "topic": topic})

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
