from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._send_json({"ok": True, "service": "mixed-demo-api"})
            return
        self._send_json({
            "message": "Hello from the MicroVM mixed Python API",
            "workspace": os.environ.get("MICROVM_WORKSPACE"),
            "instance": os.environ.get("MICROVM_INSTANCE_ID"),
        })

    def log_message(self, format, *args):
        return

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.environ.get("MICROVM_DEMO_PORT", "8766"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
