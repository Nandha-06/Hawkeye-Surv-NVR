#!/usr/bin/env python3
"""
Tapo Camera Provider — RTSP streaming and ONVIF for TP-Link Tapo cameras.
"""

import sys
import json
import argparse
import signal
import hashlib
from pathlib import Path

# ── Import shared protocol from skills/lib/ ────────────────────────────────
_script_dir = Path(__file__).resolve().parent
_protocol_loaded = False
for _lib_path in [
    _script_dir,                                  # local override
    _script_dir.parent / "lib",                   # tapo/lib/
    _script_dir.parent.parent / "lib",            # camera-providers/lib/
    _script_dir.parent.parent.parent / "lib",     # skills/lib/ (canonical)
]:
    if (_lib_path / "protocol.py").exists():
        sys.path.insert(0, str(_lib_path))
        from protocol import (  # noqa: E402
            emit, log, setup_log_prefix,
            event_error, event_ready, event_live_stream,
        )
        setup_log_prefix("Tapo")
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)
    def log(msg):
        print(f"[Tapo] {msg}", file=sys.stderr, flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Tapo Camera Provider")
    parser.add_argument("--config", type=str)
    parser.add_argument("--host", type=str)
    parser.add_argument("--username", type=str)
    parser.add_argument("--password", type=str)
    return parser.parse_args()


def load_config(args):
    if args.config and Path(args.config).exists():
        with open(args.config) as f:
            return json.load(f)
    return {
        "host": args.host,
        "username": args.username,
        "password": args.password,
    }


def main():
    args = parse_args()
    config = load_config(args)

    host = config.get("host")
    username = config.get("username")
    password = config.get("password")

    if not all([host, username, password]):
        emit({"event": "error", "message": "Missing required config: host, username, password", "retriable": False})
        sys.exit(1)

    # Tapo cameras use cloud credentials hashed for RTSP
    # Stream 1 = high quality, Stream 2 = low quality
    rtsp_url = f"rtsp://{username}:{password}@{host}:554/stream1"
    rtsp_sub = f"rtsp://{username}:{password}@{host}:554/stream2"

    def _redact(url: str) -> str:
        try:
            from urllib.parse import urlparse, urlunparse
            p = urlparse(url)
            if p.username or p.password:
                netloc = f"{p.username or '***'}:***@{p.hostname}"
                if p.port:
                    netloc += f":{p.port}"
                return urlunparse(p._replace(netloc=netloc))
        except Exception:
            pass
        return url

    emit({"event": "ready", "provider": "tapo", "host": host})

    emit({
        "event": "live_stream",
        "camera_id": f"tapo_{host.replace('.', '_')}",
        "camera_name": f"Tapo {host}",
        "url": _redact(rtsp_url),
        "sub_url": _redact(rtsp_sub),
        "url_authenticated": False,
    })

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


if __name__ == "__main__":
    main()
