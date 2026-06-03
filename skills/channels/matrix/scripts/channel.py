#!/usr/bin/env python3
"""
Matrix Channel Skill — Connect Clawdbot agent to Matrix/Element.
"""

import sys
import json
import argparse
import signal
import asyncio
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
    parser = argparse.ArgumentParser(description="Matrix Channel")
    parser.add_argument("--config", type=str)
    parser.add_argument("--homeserver", type=str, default="https://matrix.org")
    parser.add_argument("--access-token", type=str)
    parser.add_argument("--room-id", type=str)
    return parser.parse_args()


def load_config(args):
    if args.config and Path(args.config).exists():
        with open(args.config) as f:
            return json.load(f)
    return {
        "homeserver": args.homeserver,
        "access_token": args.access_token,
        "room_id": args.room_id,
    }



async def run_client(config):
    from nio import AsyncClient, MatrixRoom, RoomMessageText

    client = AsyncClient(config["homeserver"])
    client.access_token = config["access_token"]
    client.user_id = "@bot:matrix.org"  # Will be set by login

    room_id = config["room_id"]

    async def message_callback(room: MatrixRoom, event: RoomMessageText):
        if room.room_id != room_id:
            return
        if event.sender == client.user_id:
            return
        emit({
            "event": "message_received",
            "channel": "matrix",
            "room_id": room.room_id,
            "sender": event.sender,
            "text": event.body,
            "message_id": event.event_id,
            "timestamp": event.server_timestamp,
        })

    client.add_event_callback(message_callback, RoomMessageText)

    emit({"event": "ready", "channel": "matrix", "homeserver": config["homeserver"], "room_id": room_id})

    # Start sync loop in background
    sync_task = asyncio.create_task(client.sync_forever(timeout=30000))

    # Read commands from stdin
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        line = line.decode().strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if msg.get("command") == "stop":
            break

        if msg.get("command") == "send":
            text = msg.get("text", "")
            try:
                await client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": text},
                )
                emit({"event": "message_sent", "channel": "matrix", "room_id": room_id})
            except Exception as e:
                emit({"event": "error", "message": f"Send failed: {e}", "retriable": True})

    sync_task.cancel()
    await client.close()


def main():
    args = parse_args()
    config = load_config(args)

    if not config.get("access_token") or not config.get("room_id"):
        emit({"event": "error", "message": "Missing access_token or room_id", "retriable": False})
        sys.exit(1)

    asyncio.run(run_client(config))


if __name__ == "__main__":
    main()
