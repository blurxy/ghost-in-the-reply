"""/chat: Barnaby, signed in to the patient portal, chats with Wisp (planning/PATIENT-CHAT-SPEC.md).

One ChatSession per dashboard. Its state lives on the server so the presenter's buttons, the audience's key presses and
the chat page all see the same thing: who is looking (patient or Security team), whether the Warding Circle is on, and the
latest exchange. The model work is the lead's ghostreply.chat.run_chat, one guard per call.

Strict, because this is the one place a typed line reaches the model:
  - 2,000 characters, enforced here before anything else; a longer message is a 400 and the model is never called;
  - one run at a time behind a lock that the model's own thread releases when it really ends, so a timed-out run can never
    overlap the next one on the GPU;
  - guard off gets 60 s, guard on 45 s. Past that a preset falls back to its saved real run and the badge says REPLAY; a
    message somebody typed has NO fallback (a saved run would be a lie about it) and says "timed out";
  - the browser sends preset NAMES, never preset text; every string goes back to the page as JSON and is drawn with textContent.
"""

from __future__ import annotations

import importlib
import threading
import time
from typing import Any, Callable

from ghostreply import ui
from ghostreply.harness import load_patients, load_run, save_run
from ghostreply.strip import rendered

from . import viewmodel
from .inside import patient_banner
from .sandbox import GRACE_S, SANDBOX_DIR, TIMEOUTS, Rejected

NO_BACKEND = "The chat needs ghostreply's chat entry, which is not in this checkout."
NEEDS_MODEL = "Typed messages need a model: start the dashboard without --replay-only. The two suggested messages still replay."
BUSY = "Wisp is answering. One laptop GPU; one run at a time."
NO_REPLAY = "No replay for a typed message: it is your own input, so the answer is live or it says why not."
CAP_FALLBACK = 2000
ACTIONS = ("view", "guard", "reset")  # the whole control surface besides sending; names only
EXPECTED_LEVEL = {0: "possessed", 1: "whispering"}  # what a validated preset scores, per guard
SENDER = "Barnaby Gourdsworth"


class Backend:
    """What the chat needs from the model side: the presets, the cap, one call that returns a run log, and where a
    preset's saved real runs live."""

    def __init__(self, run_chat: Callable, *, presets: dict[str, dict], cap: int, preset_paths: Callable,
                 refusals: tuple = ()) -> None:
        self._run_chat, self.presets, self.cap = run_chat, presets, int(cap)
        self.preset_paths, self.refusals = preset_paths, tuple(refusals)

    def run(self, guard: int, model: Any, deadline_s: float, *, preset: str | None, text: str | None) -> Any:
        return self._run_chat(bool(guard), model, preset=preset, text=None if preset else text, deadline_s=deadline_s)


def resolve_backend() -> Backend | None:
    """The lead's ghostreply.chat, imported on first use so the rest of the demo never depends on it. None if missing."""
    try:
        mod = importlib.import_module("ghostreply.chat")
    except ImportError:
        return None
    if not callable(getattr(mod, "run_chat", None)) or not isinstance(getattr(mod, "PRESETS", None), dict):
        return None
    refusals = tuple(c for c in (getattr(mod, "InputTooLong", None), getattr(mod, "UnknownPreset", None)) if isinstance(c, type))
    return Backend(mod.run_chat, presets=mod.PRESETS, cap=getattr(mod, "MAX_CHARS", CAP_FALLBACK),
                   preset_paths=mod.preset_paths, refusals=refusals)


def bubble(preset: dict | None, text: str | None) -> dict:
    """What the patient's bubble shows. A pasted message renders only what its HTML shows a person, folded like the portal
    preview; the model gets the raw HTML, which this never sends to the page."""
    if preset is None:
        return {"text": viewmodel._text(text, 4000), "pasted": False, "folded": False}
    if "body_html" in preset:
        shown, folded = ui._fold(viewmodel._text(rendered(str(preset["body_html"])), 4000).strip())
        return {"text": shown, "pasted": True, "folded": bool(folded)}
    return {"text": viewmodel._text(preset.get("text"), 4000), "pasted": False, "folded": False}


def reply_lines(run: dict, view: dict) -> list[str]:
    """Wisp's reply as the patient sees it: the draft's text, or one plain line saying why there is none."""
    draft = run.get("draft") if isinstance(run.get("draft"), dict) else None
    text = viewmodel._text(draft.get("text") if draft else None, 6000)
    if text.strip():
        return text.split("\n")
    return [view.get("held") or view.get("urgent") or view.get("error") or "Wisp did not write a reply."]


class ChatSession:
    def __init__(self, model_factory: Callable[[], Any] | None, *, backend: Backend | Any = None,
                 timeouts: dict[int, float] | None = None, grace: float = GRACE_S,
                 clock: Callable[[], float] = time.time, save: bool = True) -> None:
        self._model = model_factory  # None: --replay-only, nothing live
        self._backend = backend
        self.timeouts = dict(TIMEOUTS if timeouts is None else timeouts)
        self.grace, self._clock, self._save = grace, clock, save
        self._run_lock = threading.Lock()  # released by the model's thread when it ends, so not an RLock
        self._lock = threading.RLock()
        self.version = 1
        self.role, self.guard = "patient", 0
        self.turn: dict | None = None
        self.last: tuple[str | None, str | None] | None = None  # (preset, text) of the latest send

    # ------------------------------------------------------------------------------------------------ backend

    @property
    def backend(self) -> Any:
        return resolve_backend() if self._backend is None else self._backend  # looked up again: it may land while we run

    @property
    def busy(self) -> bool:
        return self._run_lock.locked()

    def _bump(self) -> None:
        self.version += 1

    # ------------------------------------------------------------------------------------------------ state

    def snapshot(self, full: bool = True) -> dict:
        """The JSON the pages poll. `full=False` leaves out the exchange and the Security view (the presenter's cheap poll)."""
        backend = self.backend
        with self._lock:
            out = {"available": backend is not None, "role": self.role, "guard": self.guard, "busy": self.busy,
                   "version": self.version, "can_type": self._model is not None, "no_replay": NO_REPLAY,
                   "timeouts": {str(g): s for g, s in self.timeouts.items()}}
            if backend is None:
                out["reason"] = NO_BACKEND
                return out
            out["cap"] = backend.cap
            out["chips"] = [{"preset": k, "label": str(v.get("label", k)), "text": v.get("text")} for k, v in backend.presets.items()]
            t = self.turn
            out["turn_state"] = t["state"] if t else "idle"
            if not full or t is None:
                out["turn"] = None
                return out
            out["turn"] = {k: t[k] for k in ("n", "preset", "guard", "patient", "state", "started", "reply", "badge", "note", "warn", "timeout")}
            out["turn"]["elapsed"] = round(max(0.0, self._clock() - t["started"]), 1)
            if self.role == "security" and t.get("view"):
                out["security"] = t["view"]
            return out

    # ------------------------------------------------------------------------------------------------ actions

    def act(self, name: object) -> tuple[int, dict]:
        if not isinstance(name, str) or name not in ACTIONS:
            return 400, {"error": "unknown action", "allowed": list(ACTIONS)}
        with self._lock:
            if name == "view":
                self.role = "security" if self.role == "patient" else "patient"
            elif name == "guard":
                self.guard = 0 if self.guard else 1
            elif name == "reset":
                if self.busy:
                    return 409, {"error": BUSY}
                self.role, self.guard, self.turn, self.last = "patient", 0, None, None
            self._bump()
        return 200, self.snapshot(full=False)

    def validate(self, body: object, backend: Any) -> tuple[str | None, str | None]:
        """(preset name, typed text): exactly one is set. A typed text that is exactly a preset's text is that preset."""
        if not isinstance(body, dict):
            raise Rejected(400, "bad request")
        preset, text = body.get("preset"), body.get("text")
        if (preset is None) == (text is None):
            raise Rejected(400, "send a preset name or a text, not both")
        if preset is not None:
            if not isinstance(preset, str) or preset not in backend.presets:
                raise Rejected(400, "Unknown preset.")
            return preset, None
        if not isinstance(text, str) or not text.strip():
            raise Rejected(400, "The message is empty.")
        if len(text) > backend.cap:
            raise Rejected(400, f"The message is {len(text)} characters; the limit is {backend.cap}.")
        same = next((k for k, v in backend.presets.items() if v.get("text") == text), None)
        return (same, None) if same else (None, text)

    def send(self, body: object) -> tuple[int, dict]:
        """Start one chat turn: the patient's bubble appears at once, Wisp "is typing" until the run ends. Returns at once."""
        backend = self.backend
        if backend is None:
            return 503, {"error": NO_BACKEND}
        try:
            preset, text = self.validate(body, backend)
        except Rejected as why:
            return why.code, {"error": why.message}
        if preset is None and self._model is None:
            return 503, {"error": NEEDS_MODEL}
        if not self._run_lock.acquire(blocking=False):
            return 409, {"error": BUSY}
        with self._lock:
            guard = self.guard
            n = (self.turn["n"] + 1) if self.turn else 1
            self.last = (preset, text)
            self.turn = {"n": n, "preset": preset, "guard": guard, "state": "typing", "started": self._clock(),
                         "patient": bubble(backend.presets.get(preset) if preset else None, text), "reply": None,
                         "badge": None, "note": None, "warn": None, "timeout": self.timeouts[guard], "view": None}
            self._bump()
        threading.Thread(target=self._orchestrate, args=(backend, n, preset, text, guard), daemon=True).start()
        return 202, self.snapshot(full=False)

    # ------------------------------------------------------------------------------------------------ the run

    def _orchestrate(self, backend: Any, n: int, preset: str | None, text: str | None, guard: int) -> None:
        box: dict = {}

        def work() -> None:
            try:
                if self._model is None:
                    box["error"] = "this dashboard is replay-only"
                    return
                box["run"] = backend.run(guard, self._model(), self.timeouts[guard], preset=preset, text=text)
            except Exception as exc:  # the backend refused (it re-checks the caps) or the model path broke
                box["error"] = str(exc) if isinstance(exc, backend.refusals) else type(exc).__name__
            finally:
                self._run_lock.release()  # only now: a run that outlives its timeout still holds the GPU

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(self.timeouts[guard] + self.grace)
        run, source, why = None, "live", None
        candidate = box.get("run")
        if thread.is_alive():
            why = f"the live run took longer than {self.timeouts[guard]:.0f} s"
        elif not isinstance(candidate, dict):
            why = box.get("error") or "no result"
        elif candidate.get("status") == "error":
            why = str(candidate.get("error") or "the live run failed")[:160]
        else:
            run = candidate
            if self._save:
                try:
                    save_run(run, SANDBOX_DIR)
                except Exception:
                    pass  # a full disk must not hide a result that was already produced
        if run is None and preset is not None:  # only the two presets have an honest saved run
            try:
                run, source = load_run(backend.preset_paths(preset)[guard]), "replay"
            except Exception as exc:
                why = f"{why}; saved run not loaded ({type(exc).__name__})"
        self._finish(n, run, source, why, guard, timed_out=thread.is_alive() or "took longer" in str(why))

    def _finish(self, n: int, run: dict | None, source: str, why: str | None, guard: int, timed_out: bool) -> None:
        with self._lock:
            t = self.turn
            if t is None or t["n"] != n:  # reset (or a newer turn) while this one ran
                return
            if run is None:
                t["state"] = "timed out" if timed_out else "error"
                t["reply"] = None
                t["badge"] = {"source": "none", "text": "TIMED OUT" if timed_out else "NO ANSWER"}
                t["note"] = (f"Timed out after {self.timeouts[guard]:.0f} s. " if timed_out else "This run failed. ") + NO_REPLAY
                t["warn"] = why
            else:
                try:
                    view = viewmodel.run_view(run, source)
                    t["reply"], t["badge"] = reply_lines(run, view), view["badge"]
                    t["view"] = self._security(run, view)
                    t["state"] = "done"
                    t["note"] = f"Live run unavailable ({why}). This is the saved real run." if source == "replay" and why else None
                    level = (run.get("verdict") or {}).get("level") if isinstance(run.get("verdict"), dict) else None
                    if source == "live" and t["preset"] and level != EXPECTED_LEVEL[guard]:
                        t["warn"] = f"the live run scored {level or 'nothing'}, expected {EXPECTED_LEVEL[guard]}"
                except Exception as exc:  # a view bug must show as an error, never a dead page
                    t["state"], t["reply"], t["badge"] = "error", None, {"source": "none", "text": "NO ANSWER"}
                    t["note"], t["warn"] = "The result could not be drawn.", type(exc).__name__
            self._bump()

    def _security(self, run: dict, view: dict) -> dict:
        """The Security team's view of this run: from the run log only (dashboard.inside), plus the patient banner and the
        message as the model read it (hidden text marked), so the draft sits under the chart it does not belong to."""
        message = view["message"]
        try:
            record = load_patients().get(run["message"]["sender_patient_id"])
            banner = patient_banner(record, on=(run.get("message") or {}).get("received_at"))
        except Exception:
            banner = None
        return {"inside": view["inside"], "banner": banner, "guard": view["guard"], "draft": view["draft"],
                "seance": message["seance"], "ghosts": message["ghosts"], "badge": view["badge"],
                "meter": view["meter"]}
