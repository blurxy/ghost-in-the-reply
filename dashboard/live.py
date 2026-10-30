"""Live runs and the fallback to the saved real runs. The part of the dashboard that matters most.

Rules (DASHBOARD-SPEC): the run steps are LIVE or REPLAY. Both live runs start in the background when step 0 opens,
GUARD=0 then GUARD=1. A live result is used only if it finished in time without an error; a run still going after
its timeout (40 s for GUARD=0, 25 s for GUARD=1), a run that failed and a crash all fall back to the replay. A replay
is never labelled live. A live run whose level is not the expected one (GUARD=0 not possessed, GUARD=1 not
whispering) stays on screen but raises a warning for the presenter, who can switch to the replay with L.

Nothing here knows about Ollama: the runner takes a function, so the tests drive it with fakes and a fake clock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from .steps import EXPECTED_LEVEL

TIMEOUTS = {0: 40.0, 1: 25.0}  # seconds; measured on the demo laptop with a warm model: about 19 s and 5 s
PREPARE_TIMEOUT = 60.0  # seconds the model may take to load before the replay takes over


@dataclass
class Outcome:
    """Where a live run stands. state: off (never started) | pending | done | timeout | error."""

    state: str
    run: dict | None = None
    elapsed: float = 0.0
    error: str | None = None
    timeout: float = 0.0  # this run's limit in seconds, so the screen can count down


@dataclass
class Choice:
    """Which result the screen shows. source is "live", "replay" or "wait" (a live run still inside its timeout:
    the screen says so and counts down; it never shows a replay as if it were the live result)."""

    source: str
    reason: str | None = None
    warning: str | None = None


def resolve(mode: str, guard: int, outcome: Outcome | None) -> Choice:
    """Pick live, wait or replay for one run step. Pure: same inputs, same answer."""
    if mode != "live":
        return Choice("replay")
    if outcome is None or outcome.state == "off":
        return Choice("replay", reason="live runs are not running")
    limit = outcome.timeout or TIMEOUTS[guard]
    if outcome.state == "pending":
        return Choice("wait", reason=f"waiting for the live run, up to {limit:.0f} s")
    if outcome.state == "timeout":
        return Choice("replay", reason=f"the live run took longer than {limit:.0f} s")
    run = outcome.run if isinstance(outcome.run, dict) else {}
    if outcome.state == "error" or not run or run.get("status") == "error":
        detail = outcome.error or run.get("error") or "the live run failed"
        return Choice("replay", reason=str(detail)[:160])
    verdict = run.get("verdict")
    level = verdict.get("level") if isinstance(verdict, dict) else None
    expected = EXPECTED_LEVEL[guard]
    if level != expected:
        return Choice("live", warning=f"the live run scored {level or 'nothing'}, expected {expected}. Press L for the replay.")
    return Choice("live")


class LiveRunner:
    """Runs GUARD=0 then GUARD=1 on a background thread and reports each one's Outcome.

    run_fn(guard) returns a run log (it may raise). save(run) is called with every finished live run.
    The clock is injectable so a test can fast-forward past a timeout without sleeping.
    """

    def __init__(self, run_fn: Callable[[int], dict], *, timeouts: dict[int, float] | None = None,
                 clock: Callable[[], float] = time.monotonic, save: Callable[[dict], object] | None = None,
                 prepare: Callable[[], object] | None = None, prepare_timeout: float = PREPARE_TIMEOUT) -> None:
        """`prepare` (the model warm-up) runs once before GUARD=0 starts, and is not counted against GUARD=0's
        limit: the spec's 40 s and 25 s are for a warm model. It has a limit of its own, so a model that never
        finishes loading still falls back to the replay."""
        self._run_fn = run_fn
        self._timeouts = dict(timeouts or TIMEOUTS)
        self._clock = clock
        self._save = save
        self._prepare = prepare
        self._prepare_timeout = prepare_timeout
        self._preparing_since: float | None = None
        self._lock = threading.Lock()
        self._generation = 0
        self._slots: dict[int, dict] = {}
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start both runs the first time only. Finished results are kept, so going back to step 0 or switching
        replay -> live shows the live runs already done instead of throwing them away. restart() is explicit."""
        with self._lock:
            if self._slots:
                return
        self.restart()

    def restart(self) -> None:
        """Throw away the old results and run both again. A stale thread's late results are ignored."""
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._preparing_since = None
            self._slots = {0: {"state": "pending", "t0": None}, 1: {"state": "pending", "t0": None}}
            self._thread = threading.Thread(target=self._work, args=(generation,), daemon=True)
            self._thread.start()

    def _work(self, generation: int) -> None:
        if self._prepare is not None:
            with self._lock:
                if generation != self._generation:
                    return
                self._preparing_since = self._clock()
            try:
                self._prepare()
            except Exception:
                pass  # a failed warm-up only means a slower first run; the run's own limit still applies
            with self._lock:
                if generation != self._generation:
                    return
                self._preparing_since = None
        for guard in (0, 1):
            with self._lock:
                if generation != self._generation:
                    return
                self._slots[guard]["t0"] = self._clock()
            try:
                run, error = self._run_fn(guard), None
                if not isinstance(run, dict):  # a runner that returns anything but a run log has failed
                    run, error = None, f"the live run returned {type(run).__name__}, not a run log"
            except Exception as exc:  # any failure falls back to the replay; never end the demo
                run, error = None, f"{type(exc).__name__}: {exc}"
            if run is not None and self._save is not None:
                try:
                    self._save(run)
                except Exception:
                    pass  # a disk problem must not hide a good live result
            with self._lock:
                if generation != self._generation:
                    return
                self._slots[guard].update(run=run, error=error, t1=self._clock())

    def outcome(self, guard: int) -> Outcome:
        with self._lock:
            slot = self._slots.get(guard)
            if slot is None:
                return Outcome("off")
            now = self._clock()
            timeout = self._timeouts[guard]
            if "t1" in slot:
                elapsed = slot["t1"] - slot["t0"]
                if slot.get("error"):
                    return Outcome("error", elapsed=elapsed, error=slot["error"], timeout=timeout)
                if elapsed > timeout:
                    return Outcome("timeout", elapsed=elapsed, timeout=timeout)
                return Outcome("done", run=slot.get("run"), elapsed=elapsed, timeout=timeout)
            if self._preparing_since is not None and slot["t0"] is None:  # the model is still loading
                loading = now - self._preparing_since
                limit = self._prepare_timeout
                return Outcome("timeout" if loading > limit else "pending", elapsed=loading, timeout=limit)
            if slot["t0"] is None:  # waiting behind the other run: it counts from when the other one stalls out
                other = self._slots.get(0 if guard else 1) or {}
                stalled = other.get("t0") is not None and "t1" not in other and now - other["t0"] > self._timeouts[0]
                return Outcome("timeout" if stalled else "pending", timeout=timeout)
            elapsed = now - slot["t0"]
            return Outcome("timeout" if elapsed > timeout else "pending", elapsed=elapsed, timeout=timeout)
