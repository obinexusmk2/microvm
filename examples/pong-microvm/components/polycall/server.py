from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

from pong_common import env_int, read_json, run_server, send_json, send_sse_headers, write_sse
from microvm.polycall_adapter import health, invoke, stream_events


PORT = env_int("PONG_POLYCALL_PORT", 8892)
REPO_ROOT = Path(os.environ["PONG_REPO_ROOT"]) if os.environ.get("PONG_REPO_ROOT") else None


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        send_json(self, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(self, health(REPO_ROOT))
            return
        if self.path == "/stream":
            send_sse_headers(self)
            for event in stream_events(REPO_ROOT, count=10, delay=0.25):
                write_sse(self, event)
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:
        payload = read_json(self)
        if self.path == "/invoke":
            action = str(payload.get("action", "pong.health"))
            send_json(self, invoke(action, payload, REPO_ROOT))
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    run_server(PORT, Handler, "pong polycall")
