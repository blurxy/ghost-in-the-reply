"""The HTTP side: stdlib http.server, JSON, one HTML file. Binds to 127.0.0.1 unless told otherwise.

    GET  /               the audience view            GET  /api/state    the small, pollable state
    GET  /presenter      the presenter view           GET  /api/views    view models for the source in use
    GET  /api/replay     view models, replays only (autoplay)
    GET  /backup.mp4     dashboard/backup.mp4, if it exists
    POST /api/action     {"action": "next"|"back"|"goto"|"rerun"|"toggle_mode", "arg": ...}
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .state import ACTIONS, DashboardState

PAGE = Path(__file__).with_name("page.html")
MAX_BODY = 4096


class Handler(BaseHTTPRequestHandler):
    server_version = "ghost-dashboard"
    state: DashboardState  # set on the subclass by make_server
    page: Path

    def log_message(self, *args) -> None:  # the terminal belongs to the presenter
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: object, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path in ("/", "/presenter"):
                self._send(200, self.page.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(self.state.snapshot())
            elif path == "/api/views":
                self._json(self.state.views())
            elif path == "/api/replay":
                self._json(self.state.views(force_replay=True))
            elif path == "/backup.mp4" and self.state.backup and self.state.backup.is_file():
                self._send(200, self.state.backup.read_bytes(), "video/mp4")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # a handler bug must not take the server down mid-demo
            self._json({"error": type(exc).__name__}, 500)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/action":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                raise ValueError("body size")
            body = json.loads(self.rfile.read(length))
            action = body.get("action") if isinstance(body, dict) else None
            if action not in ACTIONS:
                raise ValueError("action")
            self._json(self.state.act(action, body.get("arg")))
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad request"}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass


class DashboardServer(ThreadingHTTPServer):
    """Never shares a port: if anything already holds it, binding fails instead of quietly serving beside it, so a
    presenter can't end up looking at some other project's page."""

    allow_reuse_address = False
    allow_reuse_port = False
    daemon_threads = True


def make_server(state: DashboardState, host: str = "127.0.0.1", port: int = 8765,
                page: Path = PAGE) -> ThreadingHTTPServer:
    """A server for this state. Pass port 0 to let the OS pick one (the tests do). Raises OSError if the port is
    taken."""
    handler = type("BoundHandler", (Handler,), {"state": state, "page": page})
    return DashboardServer((host, port), handler)
