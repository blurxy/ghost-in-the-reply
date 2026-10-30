"""The dashboard's state: which step, which source, and the JSON both views draw. One object, one lock.

Both views poll snapshot() (small, every ~300 ms) and fetch views() only when `views_version` changes. Keys and
buttons call act(). In replay-only mode there is no LiveRunner and every badge says REPLAY.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from ghostreply.harness import load_inbox, load_patients, load_run

from .live import Choice, LiveRunner, resolve
from .steps import STEPS
from .viewmodel import badge, inbox_view, message_view, run_view

ACTIONS = ("next", "back", "goto", "rerun", "toggle_mode")
PREFLIGHT_EVERY_S = 10.0
REPLAY_FILES = {0: "msg-004-guard0.json", 1: "msg-004-guard1.json"}


class DashboardState:
    def __init__(self, replay_dir: Path | str, *, live: LiveRunner | None = None, mode: str = "live",
                 clock: Callable[[], float] = time.time, preflight: Callable[[], list[dict]] | None = None,
                 backup: Path | str | None = None, inbox: dict | None = None, patients: dict | None = None) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self.live = live
        self.mode = "live" if live is not None and mode == "live" else "replay"
        self.step = 0
        self.epoch = 1
        self.step_started = clock()
        self.demo_started: float | None = None
        self.note: str | None = None
        self.backup = Path(backup) if backup else None
        self.replay: dict[int, dict] = {}
        self.replay_error: str | None = None
        for guard, name in REPLAY_FILES.items():
            try:
                self.replay[guard] = load_run(Path(replay_dir) / name)
            except Exception as exc:
                self.replay_error = f"{name}: {type(exc).__name__}"
        try:  # read-only: the other messages on the portal screen, and the (synthetic) names of the senders
            self._inbox = load_inbox() if inbox is None else inbox
            self._patients = load_patients() if patients is None else patients
        except Exception:
            self._inbox, self._patients = {}, {}
        self._preflight = preflight
        self._preflight_result: list[dict] = []
        self._preflight_at = 0.0
        self._preflight_busy = False
        self.refresh_preflight()
        if self.live is not None and self.mode == "live":
            self.live.start()  # step 0 is open: start both live runs now

    # ------------------------------------------------------------------------------------------ preflight

    def refresh_preflight(self) -> None:
        """Run the checks on a thread, so a slow check never holds up the page."""
        if self._preflight is None:
            return
        with self._lock:
            if self._preflight_busy:
                return
            self._preflight_busy = True
            self._preflight_at = self._clock()

        def work() -> None:
            try:
                result = self._preflight()
            except Exception:
                result = [{"id": "error", "label": "Preflight", "state": "fail", "detail": "the checks crashed"}]
            with self._lock:
                self._preflight_result = result
                self._preflight_busy = False

        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------------------------------------------- choice

    def _choice(self, guard: int) -> tuple[Choice, dict | None]:
        outcome = self.live.outcome(guard) if self.live is not None else None
        choice = resolve(self.mode, guard, outcome)
        if choice.source == "live" and outcome is not None and isinstance(outcome.run, dict):
            return choice, outcome.run
        if choice.source == "wait":
            return choice, None  # a live run still inside its timeout: the screen waits, it shows no replay
        return Choice("replay", choice.reason, choice.warning), self.replay.get(guard)

    # ----------------------------------------------------------------------------------------- the API

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            if self._preflight is not None and now - self._preflight_at > PREFLIGHT_EVERY_S:
                self.refresh_preflight()  # pills follow reality: the model finishes loading, the power profile changes
            runs = {}
            for guard in (0, 1):
                choice, run = self._choice(guard)
                runs[str(guard)] = {
                    "source": choice.source, "reason": choice.reason, "warning": choice.warning,
                    "badge": badge(choice.source, run or {}),
                    "run_id": (run or {}).get("run_id", ""),
                }
                if choice.source == "wait" and self.live is not None:
                    waiting = self.live.outcome(guard)
                    runs[str(guard)]["wait"] = {"elapsed": round(waiting.elapsed, 1), "timeout": waiting.timeout}
            version = "|".join(f"{g}:{r['source']}:{r['run_id']}" for g, r in runs.items()) + f"|{self.mode}"
            return {
                "step": self.step, "n": len(STEPS), "epoch": self.epoch, "mode": self.mode,
                "replay_only": self.live is None, "live_available": self.live is not None,
                "step_started": self.step_started, "demo_started": self.demo_started, "server_time": now,
                "steps": STEPS, "runs": runs, "views_version": version, "note": self.note,
                "backup": bool(self.backup and self.backup.is_file()),
                "replay_error": self.replay_error, "preflight": list(self._preflight_result),
            }

    def views(self, *, force_replay: bool = False) -> dict:
        """The per-run view models for the source currently in use (or the replays, for autoplay)."""
        with self._lock:
            runs = {}
            for guard in (0, 1):
                if force_replay:
                    choice, run = Choice("replay"), self.replay.get(guard)
                else:
                    choice, run = self._choice(guard)
                if run is not None:
                    runs[str(guard)] = run_view(run, choice.source)
            first = self.replay.get(0) or self.replay.get(1) or {}
            shown = first.get("message") if isinstance(first.get("message"), dict) else {}
            name = (self._patients.get(shown.get("sender_patient_id")) or {}).get("name") if isinstance(self._patients, dict) else None
            message = message_view(shown, first.get("strip"), sender_name=name) if first else None
            return {"message": message, "inbox": inbox_view(self._inbox, self._patients, shown.get("id")) if first else [],
                    "runs": runs, "replay_error": self.replay_error}

    def act(self, action: str, arg: object = None) -> dict:
        """Apply a key or button. Unknown actions are ignored. Returns the new snapshot."""
        with self._lock:
            now = self._clock()
            self.note = None
            if action in ("next", "back", "goto"):
                last = len(STEPS) - 1
                target = (self.step + 1 if action == "next" else self.step - 1 if action == "back"
                          else arg if isinstance(arg, int) and not isinstance(arg, bool) else self.step)
                target = max(0, min(last, target))
                if target != self.step:
                    self.step = target
                    self.step_started = now
                    self.epoch += 1
                    if target > 0 and self.demo_started is None:
                        self.demo_started = now
                    if target == 0:
                        self.demo_started = None
                        if self.live is not None and self.mode == "live":
                            self.live.start()
            elif action == "rerun":
                self.epoch += 1
                self.step_started = now
                if self.live is not None and self.mode == "live" and self.step in (0, 3, 4):
                    self.live.restart()
            elif action == "toggle_mode":
                if self.live is None:
                    self.note = "Replay-only: there is no live mode to switch to."
                else:
                    self.mode = "replay" if self.mode == "live" else "live"
                    self.epoch += 1
                    if self.mode == "live":
                        self.live.start()
            return self.snapshot()
