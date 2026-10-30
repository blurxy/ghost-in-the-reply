"""The dashboard's state: which step, which source, and the JSON both views draw. One object, one lock.

Both views poll snapshot() (small, every ~300 ms) and fetch views() only when `views_version` changes. Keys and
buttons call act(). In replay-only mode there is no LiveRunner and every badge says REPLAY.

Two optional modes (both off by default, so the default is exactly the canonical demo):
  random   one kept variant from the tested pool (runs/pool/), picked with random.SystemRandom at startup; R re-rolls.
           Its own saved runs are the replay and the fallback. Fewer than POOL_MIN kept: canonical, and it says so.
  practice hands-free rehearsal: each step lasts as long as its line takes to say, then three judge questions.
"""

from __future__ import annotations

import random
import threading
import time
from pathlib import Path
from typing import Callable

from ghostreply.harness import load_inbox, load_patients, load_run

from . import qa
from .live import Choice, LiveRunner, resolve
from .pool import POOL_DIR, POOL_MIN, load_pool, pick, run_paths
from .steps import STEPS, personalise, practice_ms
from .viewmodel import badge, inbox_view, message_view, run_view

ACTIONS = ("next", "back", "goto", "rerun", "toggle_mode", "reveal")
PREFLIGHT_EVERY_S = 10.0
REPLAY_FILES = {0: "msg-004-guard0.json", 1: "msg-004-guard1.json"}


class DashboardState:
    def __init__(self, replay_dir: Path | str, *, live: LiveRunner | None = None, mode: str = "live",
                 clock: Callable[[], float] = time.time, preflight: Callable[[], list[dict]] | None = None,
                 backup: Path | str | None = None, inbox: dict | None = None, patients: dict | None = None,
                 random_mode: bool = False, pool_dir: Path | str | None = None, rng: random.Random | None = None,
                 live_factory: Callable[[dict | None], LiveRunner] | None = None, practice: bool = False) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._rng = rng or random.SystemRandom()
        self._replay_dir = Path(replay_dir)
        self._pool_dir = Path(pool_dir) if pool_dir else POOL_DIR
        self.random_mode = random_mode
        self.variant: dict | None = None
        self.pool: list[dict] = []
        self.pool_total = 0
        self.pool_note: str | None = None
        if random_mode:
            self.pool, self.pool_total, self.pool_note = load_pool(self._pool_dir)
            if len(self.pool) >= POOL_MIN:
                self.variant = pick(self.pool, self._rng)
        self._live_factory = live_factory
        self.live = live_factory(self.variant) if live is None and live_factory is not None else live
        self.mode = "live" if self.live is not None and mode == "live" else "replay"
        self.steps = self._steps_for_variant()
        self.step = 0
        self.epoch = 1
        self.step_started = clock()
        self.demo_started: float | None = None
        self.note: str | None = None
        self.backup = Path(backup) if backup else None
        self.sandbox = None  # dashboard.sandbox.Sandbox when a model is available (set by __main__), else None
        self.practice = practice
        self._practice_started = False
        self._reset_practice()
        self.replay: dict[int, dict] = {}
        self.replay_error: str | None = None
        self._load_replays()
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

    # ------------------------------------------------------------------------------- variant and replays

    @property
    def fallback(self) -> bool:
        """Random was asked for, but fewer than POOL_MIN variants are kept: this is the canonical attack."""
        return self.random_mode and self.variant is None

    def _steps_for_variant(self) -> list[dict]:
        return personalise(STEPS, self.variant, len(self.pool), self.fallback)

    def _load_replays(self) -> None:
        """The saved runs this dashboard replays: the variant's own pool runs, or the canonical runs/demo pair."""
        paths = (run_paths(self.variant["id"], self._pool_dir) if self.variant
                 else {g: self._replay_dir / name for g, name in REPLAY_FILES.items()})
        self.replay, self.replay_error = {}, None
        for guard, path in paths.items():
            try:
                self.replay[guard] = load_run(path)
            except Exception as exc:
                self.replay_error = f"{Path(path).name}: {type(exc).__name__}"

    def reroll(self) -> None:
        """R in `--random`: another variant, from step 0, with its own runs (live ones start again)."""
        with self._lock:
            now = self._clock()
            avoid = self.variant["id"] if self.variant else None
            self.variant = pick(self.pool, self._rng, avoid=avoid) if len(self.pool) >= POOL_MIN else None
            self.steps = self._steps_for_variant()
            self._load_replays()
            if self._live_factory is not None:
                self.live = self._live_factory(self.variant)
                if self.mode == "live":
                    self.live.start()
            self.step, self.demo_started, self.step_started = 0, None, now
            self.epoch += 1
            self._reset_practice()

    # --------------------------------------------------------------------------------------------- practice

    def _reset_practice(self) -> None:
        self.phase = "run"  # run (the seven steps) | qa (the judge questions) | done
        self._qa_order = qa.pick(self._rng) if self.practice else []
        self._qa_pos = 0
        self._qa_shown = False

    def _step_seconds(self, index: int) -> float:
        return practice_ms(self.steps[index]) / 1000

    def _enter_qa(self, now: float) -> None:
        self.phase, self._qa_pos, self._qa_shown = "qa", 0, False
        self.step_started = now
        self.epoch += 1

    def _practice_tick(self, now: float) -> None:
        """Hands-free: move on when the step has lasted as long as its line takes to say. The clock starts at the
        first poll, so a rehearsal never runs ahead of the screen that opens it."""
        if not self.practice:
            return
        if not self._practice_started:
            self._practice_started, self.step_started = True, now
            return
        if self.phase != "run" or now - self.step_started < self._step_seconds(self.step):
            return
        if self.step >= len(self.steps) - 1:
            self._enter_qa(now)
        else:
            self.step += 1
            self.step_started = now
            self.epoch += 1
            if self.demo_started is None:
                self.demo_started = now

    def _practice_view(self) -> dict | None:
        if not self.practice:
            return None
        view: dict = {"phase": self.phase, "step_s": round(self._step_seconds(self.step), 1) if self.phase == "run" else 0}
        if self.phase == "qa":
            question, answer = qa.QUESTIONS[self._qa_order[self._qa_pos]]
            view["qa"] = {"pos": self._qa_pos + 1, "of": len(self._qa_order), "q": question,
                          "a": answer if self._qa_shown else None}
        return view

    def _act_qa(self, action: str, arg: object, now: float) -> None:
        self.epoch += 1
        if action == "reveal":
            self._qa_shown = self.phase == "qa"
        elif action == "next":
            if self.phase == "qa":
                self._qa_pos, self._qa_shown = self._qa_pos + 1, False
                if self._qa_pos >= len(self._qa_order):
                    self.phase = "done"
        elif action == "back":
            if self.phase == "done":
                self.phase, self._qa_pos, self._qa_shown = "qa", len(self._qa_order) - 1, False
            elif self._qa_pos > 0:
                self._qa_pos, self._qa_shown = self._qa_pos - 1, False
            else:
                self.phase, self.step_started = "run", now
        elif action == "goto":
            last = len(self.steps) - 1
            self.phase = "run"
            self.step = max(0, min(last, arg)) if isinstance(arg, int) and not isinstance(arg, bool) else last
            self.step_started = now

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

    def _random_view(self) -> dict | None:
        if not self.random_mode:
            return None
        v = self.variant or {}
        return {"fallback": self.fallback, "id": v.get("id"), "decoy": v.get("decoy"), "phone": v.get("phone"),
                "hiding": v.get("hiding"), "kept": len(self.pool), "total": self.pool_total, "note": self.pool_note}

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            self._practice_tick(now)
            if self._preflight is not None and now - self._preflight_at > PREFLIGHT_EVERY_S:
                self.refresh_preflight()  # pills follow reality: the model finishes loading, the power profile changes
            runs = {}
            for guard in (0, 1):
                choice, run = self._choice(guard)
                runs[str(guard)] = {
                    "source": choice.source, "reason": choice.reason, "warning": choice.warning,
                    "badge": badge(choice.source, run or {}, bool(self.variant)),
                    "run_id": (run or {}).get("run_id", ""),
                }
                if choice.source == "wait" and self.live is not None:
                    waiting = self.live.outcome(guard)
                    runs[str(guard)]["wait"] = {"elapsed": round(waiting.elapsed, 1), "timeout": waiting.timeout}
            version = ("|".join(f"{g}:{r['source']}:{r['run_id']}" for g, r in runs.items())
                       + f"|{self.mode}|{(self.variant or {}).get('id', '')}")
            return {
                "step": self.step, "n": len(self.steps), "epoch": self.epoch, "mode": self.mode,
                "replay_only": self.live is None, "live_available": self.live is not None,
                "step_started": self.step_started, "demo_started": self.demo_started, "server_time": now,
                "steps": self.steps, "runs": runs, "views_version": version, "note": self.note,
                "backup": bool(self.backup and self.backup.is_file()),
                "replay_error": self.replay_error, "preflight": list(self._preflight_result),
                "random": self._random_view(), "practice": self._practice_view(),
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
                    runs[str(guard)] = run_view(run, choice.source, bool(self.variant))
            first = self.replay.get(0) or self.replay.get(1) or {}
            shown = first.get("message") if isinstance(first.get("message"), dict) else {}
            name = (self._patients.get(shown.get("sender_patient_id")) or {}).get("name") if isinstance(self._patients, dict) else None
            message = message_view(shown, first.get("strip"), sender_name=name) if first else None
            inbox = self._inbox
            if self.variant and isinstance(inbox, dict) and shown.get("id"):  # this variant's message replaces msg-004
                inbox = {k: v for k, v in inbox.items() if k != "msg-004"} | {shown["id"]: shown}
            return {"message": message, "inbox": inbox_view(inbox, self._patients, shown.get("id")) if first else [],
                    "runs": runs, "replay_error": self.replay_error}

    def act(self, action: str, arg: object = None) -> dict:
        """Apply a key or button. Unknown actions are ignored. Returns the new snapshot."""
        with self._lock:
            now = self._clock()
            self.note = None
            last = len(self.steps) - 1
            if self.practice and self.phase != "run" and action in ("reveal", "next", "back", "goto"):
                self._act_qa(action, arg, now)
            elif action == "rerun" and self.variant is not None:
                self.reroll()  # in --random, R is "another variant"
            elif action in ("next", "back", "goto"):
                if self.practice and action == "next" and self.step >= last:
                    self._enter_qa(now)
                    return self.snapshot()
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
                if self.practice and self.phase != "run":  # R after the questions: the whole rehearsal again
                    self._reset_practice()
                    self.step, self.demo_started = 0, None
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
