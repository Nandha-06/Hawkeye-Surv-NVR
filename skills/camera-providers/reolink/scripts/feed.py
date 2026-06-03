#!/usr/bin/env python3
"""
Reolink Camera Provider — RTSP streaming and HTTP API integration.
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
        from protocol import emit, event_error, event_ready, event_snapshot  # noqa: E402
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Reolink Camera Provider")
    parser.add_argument("--config", type=str)
    parser.add_argument("--host", type=str)
    parser.add_argument("--username", type=str, default="admin")
    parser.add_argument("--password", type=str)
    parser.add_argument("--channel", type=int, default=0)
    return parser.parse_args()


def load_config(args):
    if args.config and Path(args.config).exists():
        with open(args.config) as f:
            return json.load(f)
    return {
        "host": args.host,
        "username": args.username,
        "password": args.password,
        "channel": args.channel,
    }



def main():
    args = parse_args()
    config = load_config(args)

    host = config.get("host")
    username = config.get("username", "admin")
    password = config.get("password")
    channel = config.get("channel", 0)

    if not host or not password:
        emit({"event": "error", "message": "Missing required config: host, password", "retriable": False})
        sys.exit(1)

    # Reolink RTSP URL format — only the authenticated form is used internally;
    # what is emitted on the wire is REDACTED to avoid leaking the camera
    # password via WS/logs/UI.
    rtsp_url = f"rtsp://{username}:{password}@{host}:554/h264Preview_{channel + 1:02d}_main"
    rtsp_sub = f"rtsp://{username}:{password}@{host}:554/h264Preview_{channel + 1:02d}_sub"

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

    emit({
        "event": "ready",
        "provider": "reolink",
        "host": host,
    })

    # Emit live stream URL for go2rtc registration (password redacted)
    emit({
        "event": "live_stream",
        "camera_id": f"reolink_{host.replace('.', '_')}",
        "camera_name": f"Reolink {host}",
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
        if msg.get("command") == "snapshot":
            import urllib.request
            import tempfile
            try:
                snapshot_url = f"http://{host}/cgi-bin/api.cgi?cmd=Snap&channel={channel}&rs=&user={username}&password={password}"
                tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False).name
                urllib.request.urlretrieve(snapshot_url, tmp)
                emit({"event": "snapshot", "camera_id": f"reolink_{host.replace('.', '_')}", "path": tmp})
            except Exception as e:
                emit({"event": "error", "message": f"Snapshot error: {e}", "retriable": True})


if __name__ == "__main__":
    main()
