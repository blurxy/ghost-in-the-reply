"""The HTTP side: stdlib http.server, JSON, one HTML file. Binds to 127.0.0.1 unless told otherwise.

    GET  /               the audience view            GET  /api/state    the small, pollable state
    GET  /presenter      the presenter view           GET  /api/views    view models for the source in use
    GET  /api/replay     view models, replays only (autoplay)
    GET  /backup.mp4     dashboard/backup.mp4, if it exists
    POST /api/action     {"action": "next"|"back"|"goto"|"rerun"|"toggle_mode", "arg": ...}
    GET  /sandbox        try your own ghost (a model is needed: not in --replay-only)
    GET  /api/sandbox/info   POST /api/sandbox/run {"visible", "hidden", "trick"}
    GET  /chat           the patient portal: Barnaby chats with Wisp (dashboard/chatportal.py)
    GET  /api/chat/state[?full=0]   POST /api/chat/send {"preset"} | {"text"}   POST /api/chat/act {"action": "view"|"guard"|"reset"}
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import stage
from .sandbox import MAX_BODY as SANDBOX_MAX_BODY
from .sandbox import NEEDS_MODEL
from .state import ACTIONS, DashboardState

PAGE = Path(__file__).with_name("page.html")
SANDBOX_PAGE = Path(__file__).with_name("sandbox.html")
CHAT_PAGE = Path(__file__).with_name("chat.html")
MAX_BODY = 4096
CHAT_MAX_BODY = 64 * 1024  # 2,000 characters even at four bytes each, with room to spare


class Handler(BaseHTTPRequestHandler):
    server_version = "ghost-dashboard"
    state: DashboardState  # set on the subclass by make_server
    page: Path
    sandbox_page: Path

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
            elif path == "/sandbox":  # served always: without a model the page itself says so
                self._send(200, self.sandbox_page.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/sandbox/info":
                sandbox = self.state.sandbox
                self._json(sandbox.info() if sandbox is not None else {"available": False, "reason": NEEDS_MODEL})
            elif path == "/chat":
                self._send(200, CHAT_PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/chat/activity":
                chat = self.state.chat
                self._json(chat.activity() if chat is not None else {"turn": None, "state": "idle", "events": [], "source": None, "thinking": None})
            elif path == "/api/chat/state":
                chat = self.state.chat
                full = "full=0" not in (self.path.split("?", 1) + [""])[1].split("&")
                self._json(chat.snapshot(full=full) if chat is not None else {"available": False, "reason": "no chat"})
            elif path == "/backup.mp4" and self.state.backup and self.state.backup.is_file():
                self._send(200, self.state.backup.read_bytes(), "video/mp4")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # a handler bug must not take the server down mid-demo
            self._json({"error": type(exc).__name__}, 500)

    def _sandbox_run(self) -> None:
        """POST /api/sandbox/run {"visible", "hidden", "trick"}: GUARD=0 then GUARD=1, live, one at a time."""
        sandbox = self.state.sandbox
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= SANDBOX_MAX_BODY:
                raise ValueError("body size")
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad request"}, 400)
            return
        if sandbox is None:  # --replay-only
            self._json({"error": NEEDS_MODEL}, 503)
            return
        code, payload = sandbox.run(body)
        self._json(payload, code)

    def _chat(self, route: str) -> None:
        """POST /api/chat/send {"preset": name} | {"text": str}, POST /api/chat/act {"action": name}. Names and one capped
        text only; the chat session validates both and answers 202 at once (the page polls /api/chat/state)."""
        chat = self.state.chat
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= CHAT_MAX_BODY:
                raise ValueError("body size")
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "bad request"}, 400)
            return
        if chat is None:
            self._json({"error": "no chat"}, 503)
            return
        code, payload = chat.send(body) if route == "/api/chat/send" else chat.act(body.get("action") if isinstance(body, dict) else None)
        self._json(payload, code)

    def do_POST(self) -> None:
        route = self.path.split("?", 1)[0]
        if route == "/api/sandbox/run":
            try:
                self._sandbox_run()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # whatever the sandbox hits, the demo server keeps serving
                self._json({"error": type(exc).__name__}, 500)
            return
        if route in ("/api/chat/send", "/api/chat/act"):
            try:
                self._chat(route)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # whatever the chat hits, the demo server keeps serving
                self._json({"error": type(exc).__name__}, 500)
            return
        if route == "/api/stage":  # only with --stage; see dashboard/stage.py for the allowlist and its rules
            if not getattr(self.state, "stage_enabled", False):
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if 0 < length <= MAX_BODY else None
            except (ValueError, json.JSONDecodeError):
                body = None
            try:
                code, payload = stage.handle(body, self.headers)
                self._json(payload, code)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # a failing hyprctl/tmux can never take the demo server down
                self._json({"error": type(exc).__name__}, 500)
            return
        if route != "/api/action":
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
                page: Path = PAGE, sandbox_page: Path = SANDBOX_PAGE) -> ThreadingHTTPServer:
    """A server for this state. Pass port 0 to let the OS pick one (the tests do). Raises OSError if the port is
    taken."""
    handler = type("BoundHandler", (Handler,), {"state": state, "page": page, "sandbox_page": sandbox_page})
    return DashboardServer((host, port), handler)
