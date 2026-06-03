#!/usr/bin/env python3
"""
Telegram Channel Skill — Connect Clawdbot agent to Telegram.
"""

import os
import sys
import json
import time
import signal
import argparse
import threading
import urllib.request
import urllib.error
from pathlib import Path

# ── Import shared protocol from skills/lib/ ────────────────────────────────
_script_dir = Path(__file__).resolve().parent
_protocol_loaded = False
for _lib_path in [
    _script_dir,                                  # local override
    _script_dir.parent / "lib",                   # telegram/lib/
    _script_dir.parent.parent / "lib",            # channels/lib/
    _script_dir.parent.parent.parent / "lib",     # skills/lib/ (canonical)
]:
    if (_lib_path / "protocol.py").exists():
        sys.path.insert(0, str(_lib_path))
        from protocol import (  # noqa: E402
            emit, log, setup_log_prefix,
            event_error, event_ready, event_message_sent,
        )
        setup_log_prefix("Telegram-Channel")
        _protocol_loaded = True
        break
if not _protocol_loaded:
    def emit(event):
        print(json.dumps(event), flush=True)
    def log(msg):
        print(f"[Telegram-Channel] {msg}", file=sys.stderr, flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Telegram Channel")
    parser.add_argument("--config", type=str)
    parser.add_argument("--token", type=str)
    parser.add_argument("--chat-id", type=str)
    return parser.parse_args()


def load_config(args):
    # Try environment variable first (Hawkeye default)
    env_params = os.environ.get("HAWKEYE_SKILL_PARAMS")
    if env_params:
        try:
            return json.loads(env_params)
        except json.JSONDecodeError as e:
            print(f"[Telegram-Channel] Error decoding HAWKEYE_SKILL_PARAMS: {e}", file=sys.stderr, flush=True)

    # Try config file
    if args.config and Path(args.config).exists():
        try:
            with open(args.config) as f:
                return json.load(f)
        except Exception as e:
            print(f"[Telegram-Channel] Error reading config file: {e}", file=sys.stderr, flush=True)

    # Fallback to CLI args
    return {
        "token": args.token,
        "chat_id": args.chat_id,
    }


def telegram_api_call(token, method, payload=None, timeout=40):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {
        "User-Agent": "Hawkeye-Telegram-Bot/1.0"
    }
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers["Content-Type"] = "application/json"
    
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            if not res_data.get("ok"):
                desc = res_data.get("description", "Unknown error")
                raise Exception(f"Telegram API Error: {desc}")
            return res_data
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode('utf-8')
            res_data = json.loads(body)
            desc = res_data.get("description", e.reason)
            raise Exception(f"HTTP {e.code}: {desc}")
        except Exception:
            raise Exception(f"HTTP Error {e.code}: {e.reason}")
    except Exception as e:
        raise Exception(f"Network error: {e}")


def polling_loop(token, chat_id, stop_event):
    offset = None
    log("Starting Telegram updates polling thread...")
    while not stop_event.is_set():
        try:
            payload = {"timeout": 30}
            if offset is not None:
                payload["offset"] = offset
            
            response = telegram_api_call(token, "getUpdates", payload=payload, timeout=45)
            updates = response.get("result", [])
            for update in updates:
                update_id = update.get("update_id")
                if update_id is not None:
                    offset = update_id + 1
                
                message = update.get("message")
                if not message:
                    continue
                
                msg_chat_id = str(message.get("chat", {}).get("id", ""))
                msg_chat_username = message.get("chat", {}).get("username", "")
                
                # Check match against configured chat_id
                matched = False
                if msg_chat_id == chat_id:
                    matched = True
                elif chat_id.startswith("@") and msg_chat_username == chat_id[1:]:
                    matched = True
                elif msg_chat_username == chat_id:
                    matched = True
                
                if not matched:
                    continue
                
                text = message.get("text", "")
                sender_info = message.get("from", {})
                sender = sender_info.get("username") or sender_info.get("first_name") or "Unknown"
                message_id = message.get("message_id")
                timestamp = message.get("date")
                
                emit({
                    "event": "message_received",
                    "channel": "telegram",
                    "chat_id": chat_id,
                    "sender": sender,
                    "text": text,
                    "message_id": message_id,
                    "timestamp": timestamp
                })
        except Exception as e:
            if stop_event.is_set():
                break
            log(f"Polling error: {e}. Retrying in 5 seconds...")
            for _ in range(5):
                if stop_event.is_set():
                    break
                time.sleep(1)


def stdin_loop(token, chat_id, stop_event):
    for line in sys.stdin:
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        
        command = msg.get("command")
        if command == "stop":
            log("Received stop command from stdin.")
            stop_event.set()
            break
        elif command == "send":
            text = msg.get("text", "")
            if not text:
                continue
            
            try:
                payload = {
                    "chat_id": chat_id,
                    "text": text
                }
                telegram_api_call(token, "sendMessage", payload=payload)
                emit({
                    "event": "message_sent",
                    "channel": "telegram",
                    "chat_id": chat_id,
                    "text": text
                })
            except Exception as e:
                emit({
                    "event": "error",
                    "message": f"Send failed: {e}",
                    "retriable": True
                })


def main():
    args = parse_args()
    config = load_config(args)
    
    token = str(config.get("token", "")).strip()
    chat_id = str(config.get("chat_id", "")).strip()
    
    if not token or not chat_id:
        emit({
            "event": "error",
            "message": "Missing Telegram token or chat_id parameter.",
            "retriable": False
        })
        log("Error: token and chat_id are required in the configuration.")
        sys.exit(1)
        
    stop_event = threading.Event()
    
    def signal_handler(signum, frame):
        log(f"Received signal {signum}. Stopping...")
        stop_event.set()
        sys.exit(0)
        
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except ValueError:
        # Signal handler can fail if not in main thread (e.g. embedded)
        pass

    # Start polling thread
    poll_thread = threading.Thread(
        target=polling_loop, 
        args=(token, chat_id, stop_event), 
        daemon=True
    )
    poll_thread.start()

    # Emit ready event
    emit({
        "event": "ready", 
        "channel": "telegram", 
        "chat_id": chat_id
    })

    # Read commands from stdin (blocks until stop command, EOF or signal)
    stdin_loop(token, chat_id, stop_event)


if __name__ == "__main__":
    main()
