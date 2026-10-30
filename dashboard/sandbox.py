"""/sandbox: try your own ghost (planning/SANDBOX-SPEC.md). Someone types a hidden instruction and watches Wisp react,
guard off and guard on, live on the model.

This is the one place the dashboard runs a stranger's text, so it is strict:
  - 2,000 characters per field, enforced here before anything else (the page's limit is only a convenience); a longer
    field is a 400 and the model is never called;
  - one run at a time behind a lock; a second request is told "Running both modes. One laptop GPU; one run at a time.". A run that times out
    keeps holding the lock until its thread has really finished, so a second click can never overlap it on the GPU;
  - GUARD=0 gets 60 s and GUARD=1 gets 45 s (Rafi: 30 s timed out under a shared GPU); past that the side says "timed out". There is NO replay fallback: it is
    somebody's own input, and a saved run would be a lie about it;
  - every user string travels as JSON and reaches the page through textContent only (see sandbox.html).
The model work is the lead's run_custom (ghostreply.variants / ghostreply.sandbox), one guard per call. Runs go to
runs/sandbox/, never runs/demo/ or runs/pool/.
"""

from __future__ import annotations

import importlib
import inspect
import threading
from typing import Any, Callable

from ghostreply import ui
from ghostreply.harness import ROOT, save_run

from . import viewmodel

BUSY = "Running both modes. One laptop GPU; one run at a time."
NEEDS_MODEL = "The sandbox needs a model: start the dashboard without --replay-only."
NO_BACKEND = "The sandbox needs ghostreply's run_custom, which is not in this checkout."
NO_REPLAY = "No replay fallback here: this is your own input, so every result is a live run or it says why not."
TIMEOUTS = {0: 60, 1: 45}  # seconds, per side (guard on was 30 s: the lead measured 27 s on a shared GPU)
GRACE_S = 10.0  # wall-clock slack on top of the harness's own deadline before the request stops waiting for a thread
MAX_BODY = 64 * 1024  # bytes of JSON: 2 x 2,000 characters even at four bytes each, with room to spare
SANDBOX_DIR = ROOT / "runs" / "sandbox"
CAP_FALLBACK = 2000
VISIBLE_FALLBACK = ("Hello,\nI have a follow-up on Wednesday, November 4 at 9:00 AM, but I have a conflict that morning. "
                    "Could we move it to Tuesday, November 3 at 2:00 PM?\nThank you,\nBarnaby Gourdsworth")


class Rejected(Exception):
    """A request the sandbox refuses before running anything."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class Backend:
    """What the sandbox needs from the model side, whichever module provides it: the tricks and their labels, the cap,
    the prefilled message, and one call that returns a run log."""

    def __init__(self, run_custom: Callable, *, tricks: list[str], labels: dict[str, str], cap: int, prefill: str,
                 refusals: tuple = (), saves: bool = False, takes_deadline: bool = True) -> None:
        self._run_custom, self.tricks, self.labels = run_custom, list(tricks), dict(labels)
        self.cap, self.prefill, self.refusals = int(cap), str(prefill), tuple(refusals)
        self._saves, self._takes_deadline = saves, takes_deadline

    def run(self, visible: str, hidden: str, trick: str, guard: int, model: Any, deadline_s: float) -> Any:
        kwargs = {"deadline_s": deadline_s} if self._takes_deadline else {}
        run = self._run_custom(visible, hidden, trick, bool(guard), model, **kwargs)
        if not self._saves and isinstance(run, dict):  # the backend leaves saving to us
            try:
                save_run(run, SANDBOX_DIR)
            except Exception:
                pass  # a full disk must not hide a result that was already produced
        return run


def resolve_backend() -> Backend | None:
    """Find the lead's run_custom: ghostreply.variants and ghostreply.sandbox each hold part of the interface, and either
    may be missing. None if there is no run_custom at all. Imported on first use, so the demo never depends on it."""
    mods = []
    for name in ("ghostreply.sandbox", "ghostreply.variants"):  # sandbox first: its run_custom takes the deadline
        try:
            mods.append(importlib.import_module(name))
        except ImportError:
            continue
    owner = next((m for m in mods if callable(getattr(m, "run_custom", None))), None)
    if owner is None:
        return None
    raw = next((m.TRICKS for m in mods if hasattr(m, "TRICKS")), None)
    if raw is None:
        return None
    labels = dict(raw) if isinstance(raw, dict) else dict(next((m.TRICK_LABELS for m in mods if hasattr(m, "TRICK_LABELS")), {}))
    cap = next((m.MAX_CHARS for m in mods if hasattr(m, "MAX_CHARS")), CAP_FALLBACK)
    prefill = next((m.DEFAULT_VISIBLE for m in mods if hasattr(m, "DEFAULT_VISIBLE")), VISIBLE_FALLBACK)
    refusals = tuple(c for m in mods for c in (getattr(m, "InputTooLong", None), getattr(m, "UnknownTrick", None))
                     if isinstance(c, type))
    params = inspect.signature(owner.run_custom).parameters
    return Backend(owner.run_custom, tricks=list(raw), labels=labels, cap=cap, prefill=prefill, refusals=refusals,
                   saves="save" in params and params["save"].default is True, takes_deadline="deadline_s" in params)


def model_label(model_name: object) -> str:
    """The honest model name for a badge: the small fallback model must not be called 12B."""
    return "Gemma 4 E4B" if "e4b" in str(model_name or "").lower() else "Gemma 4 12B"


def read_view(run: dict, guard: int) -> dict:
    """What the model read. Guard off: the whole message, the hidden text marked (the seance). Guard on: the cleaned
    text strip() handed over."""
    message = run.get("message") if isinstance(run.get("message"), dict) else {}
    info = run.get("strip") if isinstance(run.get("strip"), dict) else {}
    if guard:
        return {"kind": "clean", "text": viewmodel._text(info.get("clean"), 4000)}
    body = str(message.get("body_html", ""))
    removed = info.get("removed") if isinstance(info.get("removed"), list) else None
    if removed is None:
        removed = viewmodel.strip(body)[1]
    segments = [viewmodel._segment(s) for s in ui.seance_segments(body, removed)]
    return {"kind": "seance", "segs": segments, "ghosts": sum(1 for s in segments if s["t"] in ("ghost", "mark"))}


def side_view(run: dict, guard: int) -> dict:
    """One side of the page: badge, what the model read, trace, draft (leaks marked) and meter, from the run log."""
    view = viewmodel.run_view(run, "live")
    seconds = run.get("duration_ms")
    took = f" · {seconds / 1000:.1f} s" if isinstance(seconds, (int, float)) and seconds > 0 else ""
    badge = f"LIVE · your ghost · {model_label(run.get('model'))}{took}"
    return {"state": "ok", "guard": guard, "badge": badge, "read": read_view(run, guard), "trace": view["trace"],
            "draft": view["draft"], "meter": view["meter"], "status": view["status"], "held": view["held"],
            "urgent": view["urgent"], "intent": view["intent"], "run_id": view["run_id"], "error": view["error"]}


class Sandbox:
    def __init__(self, model_factory: Callable[[], Any], *, backend: Backend | Any = None,
                 timeouts: dict[int, float] | None = None, grace: float = GRACE_S) -> None:
        self._model = model_factory
        self._backend = backend
        self.timeouts = dict(TIMEOUTS if timeouts is None else timeouts)
        self.grace = grace
        self._lock = threading.Lock()  # released by whichever thread ends last, so not an RLock

    @property
    def backend(self) -> Any:
        if self._backend is None:
            return resolve_backend()  # looked up again next time if it was missing: it may land while we run
        return self._backend

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def info(self) -> dict:
        backend = self.backend
        if backend is None:
            return {"available": False, "reason": NO_BACKEND}
        return {"available": True, "cap": backend.cap, "visible": backend.prefill,
                "tricks": [{"key": k, "label": str(backend.labels.get(k, k))} for k in backend.tricks],
                "timeouts": {str(g): s for g, s in self.timeouts.items()}, "no_replay": NO_REPLAY, "busy": self.busy}

    def validate(self, body: object, backend: Any) -> tuple[str, str, str]:
        if not isinstance(body, dict):
            raise Rejected(400, "bad request")
        visible, hidden, trick = body.get("visible"), body.get("hidden", ""), body.get("trick")
        if not isinstance(visible, str) or not isinstance(hidden, str) or not isinstance(trick, str):
            raise Rejected(400, "visible, hidden and trick must be text")
        for name, text in (("visible", visible), ("hidden", hidden)):
            if len(text) > backend.cap:
                raise Rejected(400, f"The {name} text is {len(text)} characters; the limit is {backend.cap}.")
        if trick not in backend.tricks:
            raise Rejected(400, "Unknown hiding trick.")
        return visible, hidden, trick

    def run(self, body: object) -> tuple[int, dict]:
        """(HTTP status, JSON) for one request: GUARD=0 then GUARD=1 on the real model, one request at a time."""
        backend = self.backend
        if backend is None:
            return 503, {"error": NO_BACKEND}
        try:
            visible, hidden, trick = self.validate(body, backend)
        except Rejected as why:
            return why.code, {"error": why.message}
        if not self._lock.acquire(blocking=False):
            return 409, {"error": BUSY}
        threads: list[threading.Thread] = []
        try:
            sides = {str(g): self._side(backend, visible, hidden, trick, g, threads) for g in (0, 1)}
        finally:
            threading.Thread(target=self._release_when_done, args=(threads,), daemon=True).start()
        return 200, {"sides": sides, "no_replay": NO_REPLAY}

    def _side(self, backend: Any, visible: str, hidden: str, trick: str, guard: int,
              threads: list[threading.Thread]) -> dict:
        box: dict = {}

        def work() -> None:
            try:
                box["run"] = backend.run(visible, hidden, trick, guard, self._model(), self.timeouts[guard])
            except Exception as exc:  # the backend refused (it re-checks the caps) or the model path broke
                box["error"] = str(exc) if isinstance(exc, backend.refusals) else type(exc).__name__

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        threads.append(thread)
        thread.join(self.timeouts[guard] + self.grace)
        timed_out = {"state": "timed out", "guard": guard, "timeout": self.timeouts[guard]}
        if thread.is_alive():
            return timed_out
        run = box.get("run")
        if not isinstance(run, dict):
            return {"state": "error", "guard": guard, "error": box.get("error") or "no result"}
        if run.get("status") == "error" and str(run.get("error") or "").startswith("timed out"):
            return timed_out
        try:
            return side_view(run, guard)
        except Exception as exc:  # a view bug must show as an error on that side, not as a dead page
            return {"state": "error", "guard": guard, "error": type(exc).__name__}

    def _release_when_done(self, threads: list[threading.Thread]) -> None:
        """A run that timed out is still using the model: hold the lock until its thread has really ended."""
        for thread in threads:
            thread.join()
        self._lock.release()
