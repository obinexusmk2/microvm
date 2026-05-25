from __future__ import annotations

import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

from pong_common import env_int, local_ipv4s, now_ms, read_json, run_server, send_json, send_sse_headers, write_sse
from microvm.mmuko import CalibrationSession, summarize_stream


PORT = env_int("PONG_MATCHMAKING_PORT", 8891)
NODE_ID = os.environ.get("PONG_NODE_ID", f"pong-{uuid.uuid4().hex[:8]}")
INVITES: dict[str, dict[str, Any]] = {}
MATCHES: dict[str, dict[str, Any]] = {}
GAME_EVENTS: list[dict[str, Any]] = []


def mmuko_payload(label: str) -> dict[str, Any]:
    stream = bytes([0xAA, 0x55]) + label.encode("utf-8")
    return summarize_stream(stream)


def scan_players() -> list[dict[str, Any]]:
    players: list[dict[str, Any]] = []
    for index, address in enumerate(local_ipv4s()):
        calibration = mmuko_payload(f"{NODE_ID}:{address}")
        players.append(
            {
                "id": f"{NODE_ID}-{index}",
                "name": "unknown player" if address != "127.0.0.1" else "local player",
                "address": address,
                "port": PORT,
                "state": calibration["dominant"],
                "mmuko": calibration,
                "seenAt": now_ms(),
            }
        )
    if not players:
        players.append(
            {
                "id": f"{NODE_ID}-fallback",
                "name": "unknown player",
                "address": "127.0.0.1",
                "port": PORT,
                "state": "NOSIGNAL",
                "mmuko": summarize_stream(bytes(16)),
                "seenAt": now_ms(),
            }
        )
    return players


def create_game_event(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    event = {"kind": kind, "timestamp": now_ms(), **payload}
    GAME_EVENTS.append(event)
    del GAME_EVENTS[:-80]
    return event


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        send_json(self, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(self, {"ok": True, "service": "matchmaking", "nodeId": NODE_ID, "addresses": local_ipv4s()})
            return
        if self.path == "/mmuko/status":
            session = CalibrationSession()
            result = session.run([b"OBINexus::NSIGII::PONG", os.urandom(24), bytes(16)])
            send_json(self, {"ok": True, "service": "matchmaking", **result})
            return
        if self.path == "/events":
            send_sse_headers(self)
            for index in range(8):
                event = create_game_event(
                    "heartbeat",
                    {"sequence": index, "players": len(scan_players()), "state": "SIGNAL" if index % 2 == 0 else "NONOISE"},
                )
                write_sse(self, event)
                time.sleep(0.25)
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:
        payload = read_json(self)
        if self.path == "/scan":
            players = scan_players()
            create_game_event("scan", {"players": players})
            send_json(self, {"ok": True, "nodeId": NODE_ID, "players": players})
            return
        if self.path == "/invite":
            points = int(payload.get("points", 5))
            if points not in (5, 10, 15):
                points = 5
            invite_id = f"invite-{uuid.uuid4().hex[:10]}"
            invite = {
                "inviteId": invite_id,
                "targetId": payload.get("targetId", "unknown-player"),
                "points": points,
                "state": "SIGNAL",
                "mmuko": mmuko_payload(invite_id),
                "createdAt": now_ms(),
            }
            INVITES[invite_id] = invite
            create_game_event("invite", invite)
            send_json(self, {"ok": True, **invite})
            return
        if self.path == "/accept":
            invite_id = str(payload.get("inviteId", ""))
            invite = INVITES.get(invite_id)
            if not invite:
                send_json(self, {"ok": False, "error": "unknown invite"}, status=404)
                return
            match_id = f"match-{uuid.uuid4().hex[:10]}"
            match = {
                "matchId": match_id,
                "inviteId": invite_id,
                "points": invite["points"],
                "players": ["player 1", "player 2"],
                "state": "SIGNAL",
                "createdAt": now_ms(),
            }
            MATCHES[match_id] = match
            create_game_event("match.accepted", match)
            send_json(self, {"ok": True, **match})
            return
        if self.path == "/game/input":
            match_id = str(payload.get("matchId", "local-practice"))
            event = create_game_event("game.input", {"matchId": match_id, "input": payload})
            send_json(self, {"ok": True, "event": event})
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    run_server(PORT, Handler, "pong matchmaking")
