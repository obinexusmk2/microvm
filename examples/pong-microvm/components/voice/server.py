from __future__ import annotations

import sys
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

from pong_common import env_int, now_ms, read_json, run_server, send_json
from microvm.mmuko import summarize_stream


PORT = env_int("PONG_VOICE_PORT", 8893)
ROOMS: dict[str, dict[str, object]] = {}


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        send_json(self, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(self, {"ok": True, "service": "voice", "mode": "signaling-only", "rooms": len(ROOMS)})
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:
        payload = read_json(self)
        if self.path == "/negotiate":
            room_id = f"voice-{uuid.uuid4().hex[:10]}"
            mmuko = summarize_stream(bytes([0xAA, 0x55]) + room_id.encode("utf-8"))
            room = {
                "roomId": room_id,
                "mode": "signaling-only",
                "players": payload.get("players", ["player 1", "player 2"]),
                "state": mmuko["dominant"],
                "mmuko": mmuko,
                "createdAt": now_ms(),
                "liveAudio": False,
            }
            ROOMS[room_id] = room
            send_json(self, {"ok": True, **room})
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    run_server(PORT, Handler, "pong voice")
