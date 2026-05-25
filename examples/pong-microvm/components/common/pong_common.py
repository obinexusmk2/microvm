from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def add_common_path(file: str) -> None:
    import sys

    common = Path(file).resolve().parents[1] / "common"
    if str(common) not in sys.path:
        sys.path.insert(0, str(common))


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def now_ms() -> int:
    return int(time.time() * 1000)


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if not length:
        return {}
    raw = handler.rfile.read(length)
    try:
        decoded = json.loads(raw.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {"value": decoded}
    except json.JSONDecodeError:
        return {"raw": raw.decode("utf-8", errors="replace")}


def send_json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json_bytes(payload)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "content-type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def send_sse_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()


def write_sse(handler: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
    handler.wfile.write(b"data: " + json_bytes(payload) + b"\n\n")
    handler.wfile.flush()


def post_json(url: str, payload: dict[str, Any], timeout: float = 3.0) -> dict[str, Any]:
    data = json_bytes(payload)
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def proxy_json(handler: BaseHTTPRequestHandler, method: str, url: str, payload: dict[str, Any] | None = None) -> None:
    try:
        if method == "POST":
            result = post_json(url, payload or {})
        else:
            result = get_json(url)
        send_json(handler, result)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        send_json(handler, {"ok": False, "error": str(exc), "target": url}, status=502)


def parse_ipconfig(text: str) -> list[str]:
    addresses: list[str] = []
    for match in re.finditer(r"IPv4[^:\n]*:\s*([0-9]+(?:\.[0-9]+){3})", text, flags=re.IGNORECASE):
        address = match.group(1)
        if not address.startswith(("127.", "169.254.")) and address not in addresses:
            addresses.append(address)
    return addresses


def local_ipv4s() -> list[str]:
    addresses: list[str] = []
    if os.name == "nt":
        try:
            result = subprocess.run(["ipconfig"], text=True, capture_output=True, timeout=2, check=False)
            addresses.extend(parse_ipconfig(result.stdout))
        except (OSError, subprocess.TimeoutExpired):
            pass

    try:
        host = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(host, None, socket.AF_INET):
            address = sockaddr[0]
            if not address.startswith(("127.", "169.254.")) and address not in addresses:
                addresses.append(address)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if not address.startswith(("127.", "169.254.")) and address not in addresses:
                addresses.append(address)
    except OSError:
        pass

    return addresses or ["127.0.0.1"]


def run_server(port: int, handler_type: type[BaseHTTPRequestHandler], label: str) -> None:
    server = ReusableThreadingHTTPServer(("127.0.0.1", port), handler_type)
    print(f"{label} listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
