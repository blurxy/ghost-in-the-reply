"""Preflight checks for the presenter view: green, red or grey pills, checked on load. Nothing here can raise.

Each check is {"id", "label", "state": "ok" | "fail" | "skip", "detail"}. "skip" is for a check that does not apply
(Ollama in --replay-only mode) and is shown grey, never green, so a skipped check can't pass for a good one.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable

from .viewmodel import hhmm

EXPECTED = {"msg-004-guard0.json": "possessed", "msg-004-guard1.json": "whispering"}


def _check(id: str, label: str, state: str, detail: str) -> dict:
    return {"id": id, "label": label, "state": state, "detail": detail}


def check_ollama(model: str, host: str, *, replay_only: bool,
                 fetch: Callable[[str, float], bytes] | None = None) -> dict:
    label = f"Ollama · {model}"
    if replay_only:
        return _check("ollama", label, "skip", "replay-only: Ollama is not used")
    fetch = fetch or (lambda url, timeout: urllib.request.urlopen(url, timeout=timeout).read())
    try:
        names = [m.get("name", "") for m in json.loads(fetch(host.rstrip("/") + "/api/tags", 2.0)).get("models", [])]
    except Exception as exc:
        return _check("ollama", label, "fail", f"not reachable: {type(exc).__name__}")
    if any(n == model or n.split(":")[0] == model for n in names):
        return _check("ollama", label, "ok", "reachable, model installed")
    return _check("ollama", label, "fail", "reachable, but the model is not installed")


def check_loaded(model: str, host: str, *, replay_only: bool,
                 fetch: Callable[[str, float], bytes] | None = None) -> dict:
    """Is the model in GPU memory right now (Ollama's /api/ps)? A loaded model starts the demo run at once."""
    label = "Model loaded"
    if replay_only:
        return _check("loaded", label, "skip", "replay-only: no model is used")
    fetch = fetch or (lambda url, timeout: urllib.request.urlopen(url, timeout=timeout).read())
    try:
        running = json.loads(fetch(host.rstrip("/") + "/api/ps", 2.0)).get("models", [])
    except Exception as exc:
        return _check("loaded", label, "fail", f"cannot ask Ollama: {type(exc).__name__}")
    for m in running:
        if m.get("name") == model or str(m.get("name", "")).split(":")[0] == model:
            vram, size = m.get("size_vram") or 0, m.get("size") or 0
            where = f"{vram * 100 // size}% on the GPU" if size else "loaded"
            return _check("loaded", label, "ok" if size and vram >= size else "fail" if size else "ok", where)
    return _check("loaded", label, "fail", "not loaded yet; the first run will be slow")


def warm_up(model: str, host: str, *, post: Callable[[str, bytes, float], object] | None = None,
            timeout: float = 90.0) -> bool:
    """One 1-token call, so the model is loaded into the GPU (and kept there) before step 3. Never raises."""
    body = json.dumps({
        "model": model, "messages": [{"role": "user", "content": "Reply with one word."}], "stream": False,
        "think": False, "keep_alive": -1, "options": {"num_predict": 1, "temperature": 0},
    }).encode("utf-8")

    def default_post(url: str, data: bytes, seconds: float) -> object:
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(request, timeout=seconds).read()

    try:
        (post or default_post)(host.rstrip("/") + "/api/chat", body, timeout)
        return True
    except Exception:
        return False


def check_power(run: Callable | None = None) -> dict:
    label = "Power profile"
    run = run or subprocess.run
    try:
        out = run(["powerprofilesctl", "get"], capture_output=True, text=True, timeout=2)
        profile = (out.stdout or "").strip()
    except Exception:
        return _check("power", label, "skip", "powerprofilesctl is not available")
    if not profile:
        return _check("power", label, "skip", "powerprofilesctl gave no answer")
    return _check("power", label, "ok" if profile == "performance" else "fail", profile)


def check_replays(replay_dir: Path | str) -> dict:
    label = "Saved real runs"
    folder = Path(replay_dir)
    details, bad = [], False
    for name, want in EXPECTED.items():
        try:
            run = json.loads((folder / name).read_text(encoding="utf-8"))
            level = (run.get("verdict") or {}).get("level")
        except Exception:
            details.append(f"{name}: missing or unreadable")
            bad = True
            continue
        when = hhmm(run.get("started_at"))
        details.append(f"{name.split('-', 2)[2][:-5]} {level} {when}".strip())
        bad = bad or level != want
    return _check("replays", label, "fail" if bad else "ok", " · ".join(details))


def check_pushed(run: Callable | None = None, cwd: Path | str | None = None) -> dict:
    label = "HEAD pushed"
    run = run or subprocess.run
    try:
        out = run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], capture_output=True, timeout=3, cwd=cwd)
    except Exception:
        return _check("pushed", label, "skip", "git is not available")
    return _check("pushed", label, "ok" if out.returncode == 0 else "skip",
                  "HEAD is on origin/main" if out.returncode == 0 else "informational: HEAD is not on origin/main yet")


def run_checks(replay_dir: Path | str, *, replay_only: bool, model: str, host: str,
               cwd: Path | str | None = None) -> list[dict]:
    checks: list[Callable[[], dict]] = [
        lambda: check_ollama(model, host, replay_only=replay_only),
        lambda: check_loaded(model, host, replay_only=replay_only),
        check_power,
        lambda: check_replays(replay_dir),
        lambda: check_pushed(cwd=cwd),
    ]
    out = []
    for check in checks:
        try:
            out.append(check())
        except Exception as exc:  # a broken check shows red; it never breaks the page
            out.append(_check("error", "A preflight check", "fail", type(exc).__name__))
    return out
