#!/usr/bin/env python3
"""
Webhook Trigger Skill — POST Hawkeye events to webhook URLs.
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
        from protocol import emit, event_error, event_ready, event_webhook_sent  # noqa: E402
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Webhook Trigger")
    parser.add_argument("--config", type=str)
    parser.add_argument("--webhook-url", type=str)
    parser.add_argument("--secret", type=str)
    return parser.parse_args()


def load_config(args):
    if args.config and Path(args.config).exists():
        with open(args.config) as f:
            return json.load(f)
    return {
        "webhook_url": args.webhook_url,
        "secret": args.secret,
    }


import ipaddress
import socket
from urllib.parse import urlparse


def _resolve_and_check(host: str) -> bool:
    """Resolve a hostname and return True if all resolved IPs are non-private.

    This is best-effort DNS-rebinding mitigation: it resolves at request
    time and rejects if any answer is in a private/loopback/link-local
    range. Note: it does NOT prevent a TOCTOU race between resolution and
    the actual request, but it raises the bar significantly.
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _is_safe_webhook_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    if len(url) > 2048:
        return False
    if "\x00" in url or "\n" in url or "\r" in url:
        return False
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    if not p.hostname:
        return False
    host = p.hostname
    # Reject literal IPs in private ranges; for hostnames, resolve and check
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    except ValueError:
        # It's a hostname; resolve it
        if not _resolve_and_check(host):
            return False
    return True


def main():
    args = parse_args()
    config = load_config(args)

    import requests

    url = config.get("webhook_url")
    secret = config.get("secret")

    if not url:
        emit({"event": "error", "message": "Missing webhook_url", "retriable": False})
        sys.exit(1)

    if not _is_safe_webhook_url(url):
        emit({"event": "error", "message": "Webhook URL is unsafe (rejected: private/loopback/link-local/unsupported scheme)", "retriable": False})
        sys.exit(1)

    emit({"event": "ready", "webhook_url": url.split("@")[-1] if "@" in url else url})

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
        if event_type in ("clip_completed", "person_detected", "alert", "camera_offline"):
            headers = {"Content-Type": "application/json"}
            if secret:
                headers["X-Hawkeye-Secret"] = secret

            try:
                resp = requests.post(url, json=msg, headers=headers, timeout=10)
                emit({"event": "webhook_sent", "status_code": resp.status_code})
            except Exception as e:
                emit({"event": "error", "message": f"Webhook failed: {e}", "retriable": True})


if __name__ == "__main__":
    main()
