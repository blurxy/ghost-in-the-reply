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

TIMEOUTS = {0: 40.0, 1: 25.0}  # seconds; measured on the demo laptop: about 19 s and 5 s


@dataclass
class Outcome:
    """Where a live run stands. state: off (never started) | pending | done | timeout | error."""

    state: str
    run: dict | None = None
    elapsed: float = 0.0
    error: str | None = None


@dataclass
class Choice:
    """Which result the screen shows. source is "live" or "replay"; reason says why replay (for the presenter)."""

    source: str
    reason: str | None = None
    warning: str | None = None


def resolve(mode: str, guard: int, outcome: Outcome | None) -> Choice:
    """Pick live or replay for one run step. Pure: same inputs, same answer."""
    if mode != "live":
        return Choice("replay")
    if outcome is None or outcome.state == "off":
        return Choice("replay", reason="live runs are not running")
    if outcome.state == "pending":
        return Choice("replay", reason="the live run is still going")
    if outcome.state == "timeout":
        return Choice("replay", reason=f"the live run took longer than {TIMEOUTS[guard]:.0f} s")
    if outcome.state == "error" or not isinstance(outcome.run, dict) or outcome.run.get("status") == "error":
        detail = outcome.error or (outcome.run or {}).get("error") or "the live run failed"
        return Choice("replay", reason=str(detail)[:160])
    level = (outcome.run.get("verdict") or {}).get("level")
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
                 clock: Callable[[], float] = time.monotonic, save: Callable[[dict], object] | None = None) -> None:
        self._run_fn = run_fn
        self._timeouts = dict(timeouts or TIMEOUTS)
        self._clock = clock
        self._save = save
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
            self._slots = {0: {"state": "pending", "t0": None}, 1: {"state": "pending", "t0": None}}
            self._thread = threading.Thread(target=self._work, args=(generation,), daemon=True)
            self._thread.start()

    def _work(self, generation: int) -> None:
        for guard in (0, 1):
            with self._lock:
                if generation != self._generation:
                    return
                self._slots[guard]["t0"] = self._clock()
            try:
                run, error = self._run_fn(guard), None
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
                    return Outcome("error", elapsed=elapsed, error=slot["error"])
                if elapsed > timeout:
                    return Outcome("timeout", elapsed=elapsed)
                return Outcome("done", run=slot.get("run"), elapsed=elapsed)
            if slot["t0"] is None:  # waiting behind the other run: it counts from when the other one stalls out
                other = self._slots.get(0 if guard else 1) or {}
                stalled = other.get("t0") is not None and "t1" not in other and now - other["t0"] > self._timeouts[0]
                return Outcome("timeout") if stalled else Outcome("pending")
            elapsed = now - slot["t0"]
            return Outcome("timeout" if elapsed > timeout else "pending", elapsed=elapsed)
