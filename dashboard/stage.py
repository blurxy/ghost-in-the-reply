"""Stage controls for the presenter: switch the projector, type fixed demo commands into the visible terminal.

POST /api/stage {"action": "<name>"} runs ONE allowlisted action. Rules, each enforced here and tested:
- The action name must be one of ACTIONS. Anything else, including names with shell metacharacters, is a 400
  and runs nothing.
- Every command is a constant argument list. Nothing from the request becomes part of a command, and nothing
  runs through a shell (no shell=True).
- The endpoint exists only when the dashboard is started with --stage (the demo instance on 127.0.0.1). Without
  the flag it is a 404, so the teammates' instance (port 8814, behind a public tunnel) never exposes it.
- The request must carry the header X-Haunted-Stage: 1. A browser can't send a custom header cross-origin without
  a CORS preflight, which this server never grants, so another web page can't trigger stage actions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
STAGE_HEADER = "X-Haunted-Stage"
TMUX_SESSION = "demoterm"
TIMEOUT_S = 10


def _type_into_terminal(step: int) -> list[str]:
    return ["tmux", "send-keys", "-t", TMUX_SESSION, f"bash demo/raw/demo.sh {step}", "Enter"]


# name -> (button label, constant argv). The labels match planning/PATIENT-CHAT-SPEC.md, update 3:32 PM.
ACTIONS: dict[str, tuple[str, list[str]]] = {
    "show-terminal": ("Show terminal", ["bash", "demo/raw/stage.sh", "show-terminal"]),
    "show-dashboard": ("Show dashboard", ["bash", "demo/raw/stage.sh", "show-dashboard"]),
    "term-ollama-ps": ("Terminal: ollama ps", _type_into_terminal(0)),
    "term-curl-guard-off": ("Terminal: raw curl, guard off", _type_into_terminal(2)),
    "term-curl-guard-on": ("Terminal: raw curl, guard on", _type_into_terminal(3)),
    "term-tests": ("Terminal: run the tests", _type_into_terminal(5)),
}


def labels() -> dict[str, str]:
    """For the presenter buttons: action name -> label, in demo order."""
    return {name: label for name, (label, _) in ACTIONS.items()}


def handle(body: object, headers: Any = None, *,  # a dict or the request's http.client.HTTPMessage
           runner: Callable[..., subprocess.CompletedProcess] | None = None) -> tuple[int, dict]:
    """Validate and run one stage action. Returns (HTTP status, JSON body). Never raises."""
    runner = runner or subprocess.run  # looked up per call, so a test that patches subprocess.run is honoured
    if str((headers or {}).get(STAGE_HEADER, "")).strip() != "1":
        return 403, {"error": f"stage actions need the {STAGE_HEADER}: 1 header"}
    action = body.get("action") if isinstance(body, dict) else None
    if not isinstance(action, str) or action not in ACTIONS:
        return 400, {"error": "unknown stage action", "allowed": list(ACTIONS)}
    label, argv = ACTIONS[action]
    try:
        done = runner(list(argv), cwd=ROOT, shell=False, capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as e:
        return 500, {"action": action, "ok": False, "error": type(e).__name__}
    if done.returncode != 0:
        return 500, {"action": action, "ok": False, "error": (done.stderr or "").strip()[:200] or "failed"}
    return 200, {"action": action, "ok": True, "label": label}
