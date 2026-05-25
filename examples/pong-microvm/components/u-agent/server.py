from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

from pong_common import env_int, read_json, run_server, send_json


PORT = env_int("PONG_U_AGENT_PORT", 8894)
STATS_PATH = Path(__file__).with_name("u_stats.json")


def load_stats() -> dict[str, object]:
    if STATS_PATH.exists():
        try:
            return json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"samples": 0, "averageError": 0.0, "lastTargets": []}


def save_stats(stats: dict[str, object]) -> None:
    STATS_PATH.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def predict(payload: dict[str, object]) -> dict[str, object]:
    ball = payload.get("ball") if isinstance(payload.get("ball"), dict) else {}
    canvas = payload.get("canvas") if isinstance(payload.get("canvas"), dict) else {}
    stats = load_stats()

    height = float(canvas.get("height", 720) or 720)
    ball_y = float(ball.get("y", height / 2) or height / 2)
    velocity_y = float(ball.get("vy", 0) or 0)
    paddle_height = float(payload.get("paddleHeight", 96) or 96)
    target_y = max(paddle_height / 2, min(height - paddle_height / 2, ball_y + velocity_y * 9))

    samples = int(stats.get("samples", 0)) + 1
    last_targets = list(stats.get("lastTargets", []))[-14:]
    last_targets.append(round(target_y, 2))
    stats["samples"] = samples
    stats["lastTargets"] = last_targets
    save_stats(stats)

    confidence = min(0.95, 0.35 + samples * 0.025)
    return {
        "ok": True,
        "agent": payload.get("agent", "player 2u"),
        "targetY": target_y,
        "confidence": confidence,
        "trainingSamples": samples,
        "stats": stats,
    }


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        send_json(self, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(self, {"ok": True, "service": "u-agent", "stats": load_stats()})
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:
        payload = read_json(self)
        if self.path == "/predict":
            send_json(self, predict(payload))
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    run_server(PORT, Handler, "pong u-agent")
