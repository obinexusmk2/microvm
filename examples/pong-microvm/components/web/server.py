from __future__ import annotations

import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

from pong_common import env_int, proxy_json, read_json, run_server, send_json, send_sse_headers


PORT = env_int("PONG_WEB_PORT", 8890)
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ASSETS = ROOT / "assets"
MATCHMAKING_URL = os.environ.get("PONG_MATCHMAKING_URL", "http://127.0.0.1:8891")
POLYCALL_URL = os.environ.get("PONG_POLYCALL_URL", "http://127.0.0.1:8892")
VOICE_URL = os.environ.get("PONG_VOICE_URL", "http://127.0.0.1:8893")
U_AGENT_URL = os.environ.get("PONG_U_AGENT_URL", "http://127.0.0.1:8894")


def safe_static_path(base: Path, requested: str) -> Path | None:
    relative = requested.strip("/").replace("\\", "/")
    target = (base / relative).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        send_json(self, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            send_json(
                self,
                {
                    "ok": True,
                    "service": "web",
                    "ports": {
                        "web": PORT,
                        "matchmaking": MATCHMAKING_URL,
                        "polycall": POLYCALL_URL,
                        "voice": VOICE_URL,
                        "uAgent": U_AGENT_URL,
                    },
                },
            )
            return
        if self.path == "/config.json":
            send_json(
                self,
                {
                    "matchmakingUrl": MATCHMAKING_URL,
                    "polycallUrl": POLYCALL_URL,
                    "voiceUrl": VOICE_URL,
                    "uAgentUrl": U_AGENT_URL,
                },
            )
            return
        if self.path == "/api/mmuko/status":
            proxy_json(self, "GET", f"{MATCHMAKING_URL}/mmuko/status")
            return
        if self.path == "/api/game/events":
            self.proxy_sse(f"{MATCHMAKING_URL}/events")
            return
        if self.path == "/api/polycall/stream":
            self.proxy_sse(f"{POLYCALL_URL}/stream")
            return
        if self.path == "/" or self.path == "/index.html":
            self.send_file(STATIC / "index.html")
            return
        if self.path.startswith("/static/"):
            target = safe_static_path(STATIC, self.path.removeprefix("/static/"))
            self.send_file(target)
            return
        if self.path.startswith("/assets/"):
            target = safe_static_path(ASSETS, self.path.removeprefix("/assets/"))
            self.send_file(target)
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def do_POST(self) -> None:
        payload = read_json(self)
        routes = {
            "/api/scan": f"{MATCHMAKING_URL}/scan",
            "/api/invite": f"{MATCHMAKING_URL}/invite",
            "/api/accept": f"{MATCHMAKING_URL}/accept",
            "/api/game/input": f"{MATCHMAKING_URL}/game/input",
            "/api/voice/negotiate": f"{VOICE_URL}/negotiate",
            "/api/polycall/invoke": f"{POLYCALL_URL}/invoke",
            "/api/u/predict": f"{U_AGENT_URL}/predict",
        }
        target = routes.get(self.path)
        if target:
            proxy_json(self, "POST", target, payload)
            return
        send_json(self, {"ok": False, "error": "not found"}, status=404)

    def proxy_sse(self, target_url: str) -> None:
        send_sse_headers(self)
        try:
            with urllib.request.urlopen(target_url, timeout=15) as response:
                while True:
                    chunk = response.read(1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.wfile.write(f"data: {{\"ok\":false,\"error\":\"{str(exc)}\"}}\n\n".encode("utf-8"))
            self.wfile.flush()

    def send_file(self, path: Path | None) -> None:
        if path is None or not path.is_file():
            send_json(self, {"ok": False, "error": "not found"}, status=404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    run_server(PORT, Handler, "pong web")
