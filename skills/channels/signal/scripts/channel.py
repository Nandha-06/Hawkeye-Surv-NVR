#!/usr/bin/env python3
"""
Signal Channel Skill — Connect Clawdbot agent to Signal via signal-cli.
"""

import sys
import json
import argparse
import signal as sig
import subprocess
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
        from protocol import emit, event_error, event_ready, event_message_sent  # noqa: E402
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Signal Channel")
    parser.add_argument("--config", type=str)
    parser.add_argument("--signal-cli-path", type=str, default="signal-cli")
    parser.add_argument("--phone-number", type=str)
    return parser.parse_args()


def load_config(args):
    if args.config and Path(args.config).exists():
        with open(args.config) as f:
            return json.load(f)
    return {
        "signal_cli_path": args.signal_cli_path,
        "phone_number": args.phone_number,
    }



def validate_phone_number(num):
    if not num:
        return False
    num_str = str(num).strip()
    if not num_str.startswith("+"):
        return False
    # Phone number should only contain digits, spaces, and hyphens (and + at the start)
    return all(c.isdigit() or c in " -()" for c in num_str[1:])


def validate_recipient(rec):
    if not rec:
        return False
    rec_str = str(rec).strip()
    if rec_str.startswith("-"):
        return False
    # A recipient in signal-cli can be a phone number (e.g. +12345) or a group ID (usually base64 or hex)
    # Check for sane characters to avoid option injection or shell-like behavior
    # Sane characters: alphanumeric, +, -, =, / (base64 characters)
    return all(c.isalnum() or c in "+-=/ " for c in rec_str)


def main():
    args = parse_args()
    config = load_config(args)

    cli = config.get("signal_cli_path", "signal-cli")
    phone = config.get("phone_number")

    if cli.startswith("-"):
        emit({"event": "error", "message": "Invalid signal_cli_path (cannot start with a dash)", "retriable": False})
        sys.exit(1)

    if not phone or not validate_phone_number(phone):
        emit({"event": "error", "message": "Invalid or missing phone_number (must start with + and contain only digits/dashes/spaces/parentheses)", "retriable": False})
        sys.exit(1)

    # Verify signal-cli is available
    try:
        result = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version = result.stdout.strip()
            emit({"event": "ready", "channel": "signal", "version": version, "phone": phone})
        else:
            emit({"event": "error", "message": f"signal-cli version check failed: {result.stderr.strip()}", "retriable": False})
            sys.exit(1)
    except FileNotFoundError:
        emit({"event": "error", "message": f"signal-cli not found at: {cli}", "retriable": False})
        sys.exit(1)

    running = True
    def handle_signal(s, f):
        nonlocal running
        running = False
    sig.signal(sig.SIGTERM, handle_signal)
    sig.signal(sig.SIGINT, handle_signal)

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

        if msg.get("command") == "send":
            recipient = msg.get("recipient")
            text = msg.get("text", "")
            
            if not validate_recipient(recipient):
                emit({"event": "error", "message": f"Invalid recipient format: {recipient}", "retriable": False})
                continue
                
            try:
                result = subprocess.run(
                    [cli, "-a", phone, "send", "-m", text, recipient],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    emit({"event": "message_sent", "channel": "signal", "recipient": recipient})
                else:
                    error_msg = result.stderr.strip() or f"signal-cli exited with code {result.returncode}"
                    emit({"event": "error", "message": f"Send failed: {error_msg}", "retriable": True})
            except Exception as e:
                emit({"event": "error", "message": f"Send failed: {e}", "retriable": True})


if __name__ == "__main__":
    main()
