"""Tests for the dashboard/ package: live fallback, state, view models, HTTP server, copy and preflight.

No model, no Ollama, no sleeping: LiveRunner is driven with fake run functions and a fake clock, the server is a
127.0.0.1 server on port 0 started by the test, and the run logs are the real saved replays in runs/demo/.
"""

from __future__ import annotations

import contextlib
import copy
import json
import re
import threading
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from dashboard import __main__ as dash_main
from dashboard import preflight, steps, viewmodel
from dashboard.live import TIMEOUTS, Choice, LiveRunner, Outcome, resolve
from dashboard.server import PAGE, make_server
from dashboard.state import DashboardState

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "runs" / "demo"
REPLAY_FILES = {0: "msg-004-guard0.json", 1: "msg-004-guard1.json"}
REPLAYS = {g: json.loads((DEMO / name).read_text(encoding="utf-8")) for g, name in REPLAY_FILES.items()}
LEVEL = {0: "possessed", 1: "whispering"}
LIVE_BADGE = re.compile(r"LIVE · Gemma 4 12B(?: · \d+\.\d s)?")
REPLAY_PREFIX = "REPLAY of a real Gemma 4 run"


class Clock:
    """A fake clock: a callable returning a mutable float."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def good_run(guard: int, **changes) -> dict:
    """A 'live' run: a copy of the real replay with its own run_id (so a live run is distinguishable)."""
    run = copy.deepcopy(REPLAYS[guard])
    run["run_id"] = f"live-g{guard}"
    run.update(changes)
    return run


def settle(runner: LiveRunner) -> None:
    runner._thread.join(2)
    assert not runner._thread.is_alive(), "the runner thread did not finish"


@pytest.fixture
def gate():
    event = threading.Event()
    yield event
    event.set()  # never leave a fake run_fn blocked


def fast_runner(clock: Clock | None = None, **kw) -> LiveRunner:
    """A runner whose runs both finish at once with the right levels."""
    return LiveRunner(lambda g: good_run(g), clock=clock or Clock(), **kw)


def assert_badges_honest(snapshot_or_views: dict) -> None:
    """The one rule: only a live result says LIVE; a replay never does and always says REPLAY of a real run."""
    for run in snapshot_or_views["runs"].values():
        text = run["badge"]["text"]
        assert run["badge"]["source"] == run["source"] if "source" in run else True
        if run["badge"]["source"] == "live":
            assert LIVE_BADGE.fullmatch(text), text
        elif run["badge"]["source"] == "wait":  # a live run still going: neither live nor a replay, and it says so
            assert text == "Waiting for the live run…" and "LIVE" not in text, text
        else:
            assert "LIVE" not in text, text
            assert text.startswith(REPLAY_PREFIX), text


# ======================================================================================== A. live.py: resolve

RIGHT = {g: Outcome("done", run={"status": "drafted", "verdict": {"level": LEVEL[g]}}) for g in (0, 1)}


@pytest.mark.parametrize("guard", [0, 1])
def test_resolve_replay_mode_is_always_replay(guard):
    for outcome in (None, Outcome("off"), Outcome("pending"), RIGHT[guard], Outcome("error", error="x")):
        choice = resolve("replay", guard, outcome)
        assert choice == Choice("replay")


@pytest.mark.parametrize("outcome", [None, Outcome("off")], ids=["none", "off"])
@pytest.mark.parametrize("guard", [0, 1])
def test_resolve_live_not_running_falls_back_with_a_reason(guard, outcome):
    choice = resolve("live", guard, outcome)
    assert choice.source == "replay" and choice.reason and choice.warning is None


@pytest.mark.parametrize("guard", [0, 1])
def test_resolve_pending_waits_and_never_shows_a_replay_early(guard):
    choice = resolve("live", guard, Outcome("pending", timeout=40.0 if guard == 0 else 25.0))
    assert choice.source == "wait" and "waiting" in choice.reason
    assert ("40 s" if guard == 0 else "25 s") in choice.reason


@pytest.mark.parametrize("guard,seconds", [(0, "40 s"), (1, "25 s")])
def test_resolve_timeout_names_the_limit(guard, seconds):
    choice = resolve("live", guard, Outcome("timeout", elapsed=99.0))
    assert choice.source == "replay"
    assert seconds in choice.reason


@pytest.mark.parametrize("guard", [0, 1])
def test_resolve_error_carries_the_error_text(guard):
    choice = resolve("live", guard, Outcome("error", error="ConnectionError: refused"))
    assert choice.source == "replay" and "ConnectionError: refused" in choice.reason


@pytest.mark.parametrize("guard", [0, 1])
def test_resolve_done_run_with_error_status_falls_back(guard):
    outcome = Outcome("done", run={"status": "error", "error": "model went away", "verdict": {"level": LEVEL[guard]}})
    choice = resolve("live", guard, outcome)
    assert choice.source == "replay" and "model went away" in choice.reason


@pytest.mark.parametrize("guard", [0, 1])
def test_resolve_done_with_no_run_dict_falls_back(guard):
    assert resolve("live", guard, Outcome("done", run=None)).source == "replay"


@pytest.mark.parametrize("guard", [0, 1])
def test_resolve_right_level_is_live_without_warning(guard):
    choice = resolve("live", guard, RIGHT[guard])
    assert choice == Choice("live", reason=None, warning=None)


@pytest.mark.parametrize("guard,wrong", [(0, "whispering"), (0, "still"), (1, "possessed"), (1, "still")])
def test_resolve_wrong_level_stays_live_with_warning(guard, wrong):
    outcome = Outcome("done", run={"status": "drafted", "verdict": {"level": wrong}})
    choice = resolve("live", guard, outcome)
    assert choice.source == "live"
    assert choice.warning and LEVEL[guard] in choice.warning and wrong in choice.warning


def test_resolve_wrong_level_with_no_verdict_warns():
    choice = resolve("live", 0, Outcome("done", run={"status": "drafted"}))
    assert choice.source == "live" and "possessed" in choice.warning


# ====================================================================================== A. live.py: LiveRunner

def test_runner_outcome_is_off_before_start():
    runner = fast_runner()
    assert runner.outcome(0).state == "off" and runner.outcome(1).state == "off"


def test_runner_done_carries_run_and_elapsed():
    clock = Clock()

    def run_fn(guard):
        clock.t += 3.5
        return good_run(guard)

    runner = LiveRunner(run_fn, clock=clock)
    runner.start()
    settle(runner)
    for guard in (0, 1):
        out = runner.outcome(guard)
        assert out.state == "done" and out.run["run_id"] == f"live-g{guard}"
        assert out.elapsed == pytest.approx(3.5)


def test_runner_runs_guard_0_then_guard_1():
    order = []
    runner = LiveRunner(lambda g: order.append(g) or good_run(g), clock=Clock())
    runner.start()
    settle(runner)
    assert order == [0, 1]


@pytest.mark.parametrize("exc", [ValueError("nope"), RuntimeError("boom"), ConnectionError("down")])
def test_runner_exception_becomes_error_naming_the_class(exc):
    def run_fn(guard):
        raise exc

    runner = LiveRunner(run_fn, clock=Clock())
    runner.start()
    settle(runner)
    for guard in (0, 1):  # a crash in GUARD=0 must not stop GUARD=1 from being tried
        out = runner.outcome(guard)
        assert out.state == "error" and type(exc).__name__ in out.error and str(exc) in out.error


@pytest.mark.parametrize("guard,advance,expected", [
    (0, 39.0, "done"), (0, 41.0, "timeout"), (1, 24.0, "done"), (1, 26.0, "timeout"),
])
def test_runner_slow_run_times_out_at_the_per_guard_limit(guard, advance, expected):
    clock = Clock()

    def run_fn(g):
        if g == guard:
            clock.t += advance
        return good_run(g)

    runner = LiveRunner(run_fn, clock=clock)
    runner.start()
    settle(runner)
    assert runner.outcome(guard).state == expected
    assert runner.outcome(1 - guard).state == "done"


def test_timeouts_constants_match_the_spec():
    assert TIMEOUTS == {0: 40.0, 1: 25.0}


def test_runner_still_running_past_timeout_reports_timeout(gate):
    clock = Clock()
    started = threading.Event()

    def run_fn(guard):
        started.set()
        gate.wait(2)
        return good_run(guard)

    runner = LiveRunner(run_fn, clock=clock)
    runner.start()
    assert started.wait(2)
    clock.t = 39.0
    assert runner.outcome(0).state == "pending"
    clock.t = 41.0
    assert runner.outcome(0).state == "timeout"
    gate.set()
    settle(runner)


def test_guard1_waiting_behind_stalled_guard0_times_out_after_guard0_limit(gate):
    clock = Clock()
    started = threading.Event()

    def run_fn(guard):
        started.set()
        gate.wait(2)
        return good_run(guard)

    runner = LiveRunner(run_fn, clock=clock)
    runner.start()
    assert started.wait(2)
    clock.t = 10.0
    assert runner.outcome(1).state == "pending"
    clock.t = 39.0
    assert runner.outcome(1).state == "pending"
    clock.t = 41.0
    assert runner.outcome(1).state == "timeout"
    assert runner.outcome(0).state == "timeout"
    gate.set()
    settle(runner)


def test_save_is_called_with_each_finished_run():
    saved = []
    runner = fast_runner(save=saved.append)
    runner.start()
    settle(runner)
    assert [r["run_id"] for r in saved] == ["live-g0", "live-g1"]


def test_failing_save_is_swallowed_and_the_run_still_counts():
    calls = []

    def save(run):
        calls.append(run["run_id"])
        raise OSError("disk full")

    runner = fast_runner(save=save)
    runner.start()
    settle(runner)
    assert calls == ["live-g0", "live-g1"]
    assert runner.outcome(0).state == "done" and runner.outcome(1).state == "done"


def test_save_is_not_called_for_a_crashed_run():
    saved = []

    def run_fn(guard):
        raise RuntimeError("x")

    runner = LiveRunner(run_fn, clock=Clock(), save=saved.append)
    runner.start()
    settle(runner)
    assert saved == []


def test_restart_ignores_the_old_threads_late_results(gate):
    calls = []
    lock = threading.Lock()
    first_started = threading.Event()

    def run_fn(guard):
        with lock:
            calls.append(guard)
            number = len(calls)
        if number == 1:  # the very first call is the one that hangs
            first_started.set()
            gate.wait(2)
            return {"tag": "old", "status": "drafted"}
        return {"tag": "new", "status": "drafted"}

    runner = LiveRunner(run_fn, clock=Clock())
    runner.start()
    assert first_started.wait(2)
    old_thread, old_generation = runner._thread, runner._generation
    runner.restart()
    assert runner._generation == old_generation + 1 and runner._thread is not old_thread
    runner._thread.join(2)
    assert runner.outcome(0).run["tag"] == "new" and runner.outcome(1).run["tag"] == "new"
    gate.set()  # the old thread now finishes, late
    old_thread.join(2)
    assert not old_thread.is_alive()
    assert runner.outcome(0).run["tag"] == "new" and runner.outcome(1).run["tag"] == "new"


def test_start_is_a_noop_while_the_thread_is_alive(gate):
    started = threading.Event()
    count = []

    def run_fn(guard):
        count.append(guard)
        started.set()
        gate.wait(2)
        return good_run(guard)

    runner = LiveRunner(run_fn, clock=Clock())
    runner.start()
    assert started.wait(2)
    thread, generation = runner._thread, runner._generation
    runner.start()
    runner.start()
    assert runner._thread is thread and runner._generation == generation
    gate.set()
    settle(runner)
    assert count == [0, 1]  # no extra runs were started


def test_start_after_the_runs_finished_keeps_the_results():
    # going back to step 0, or switching replay -> live, must show the live runs already done
    count = []
    runner = LiveRunner(lambda g: count.append(g) or good_run(g), clock=Clock())
    runner.start()
    settle(runner)
    first = runner.outcome(0).run
    runner.start()
    settle(runner)
    assert count == [0, 1]
    assert runner.outcome(0).state == "done" and runner.outcome(0).run is first


def test_restart_is_the_only_way_to_run_again():
    count = []
    runner = LiveRunner(lambda g: count.append(g) or good_run(g), clock=Clock())
    runner.start()
    settle(runner)
    runner.restart()
    settle(runner)
    assert count == [0, 1, 0, 1]


# ===================================================================================== B. state.py (replay-only)

def replay_state(**kw) -> DashboardState:
    return DashboardState(DEMO, clock=kw.pop("clock", Clock(1000.0)), **kw)


def test_replay_only_initial_state():
    state = replay_state(live=None)
    snap = state.snapshot()
    assert snap["step"] == 0 and snap["epoch"] == 1 and snap["mode"] == "replay"
    assert snap["replay_only"] is True and snap["live_available"] is False
    assert snap["n"] == 7 and len(snap["steps"]) == 7
    assert snap["replay_error"] is None and snap["demo_started"] is None
    for guard in ("0", "1"):
        run = snap["runs"][guard]
        assert run["source"] == "replay"
        assert run["badge"]["source"] == "replay" and run["badge"]["text"].startswith(REPLAY_PREFIX)
        assert run["run_id"] == REPLAYS[int(guard)]["run_id"]
    assert_badges_honest(snap)


def test_mode_live_without_a_runner_is_replay():
    assert DashboardState(DEMO, live=None, mode="live").mode == "replay"


def test_replay_mode_with_a_runner_does_not_start_it():
    runner = fast_runner()
    state = DashboardState(DEMO, live=runner, mode="replay")
    assert runner._thread is None and state.mode == "replay"
    assert state.snapshot()["live_available"] is True and state.snapshot()["replay_only"] is False


def test_navigation_moves_and_clamps_at_both_ends():
    state = replay_state()
    state.act("back")
    assert state.step == 0 and state.epoch == 1  # clamped at 0, no epoch bump
    for expected in range(1, 7):
        snap = state.act("next")
        assert snap["step"] == expected
    epoch = state.epoch
    assert state.act("next")["step"] == 6 and state.epoch == epoch  # clamped at the last step
    assert state.act("back")["step"] == 5


@pytest.mark.parametrize("arg,expected", [(3, 3), (0, 0), (6, 6), (99, 6), (-5, 0)])
def test_goto_clamps_into_range(arg, expected):
    state = replay_state()
    state.act("goto", 2)
    assert state.act("goto", arg)["step"] == expected


@pytest.mark.parametrize("arg", ["3", 3.0, True, False, None, [3], {"a": 1}], ids=repr)
def test_goto_ignores_non_int_args(arg):
    state = replay_state()
    state.act("goto", 2)
    epoch = state.epoch
    snap = state.act("goto", arg)
    assert snap["step"] == 2 and snap["epoch"] == epoch


@pytest.mark.parametrize("action", ["bogus", "", "NEXT", None, "next ", 5])
def test_unknown_actions_change_nothing(action):
    state = replay_state()
    state.act("goto", 2)
    before = state.snapshot()
    after = state.act(action)
    assert (after["step"], after["epoch"], after["mode"], after["demo_started"]) == (
        before["step"], before["epoch"], before["mode"], before["demo_started"])


def test_epoch_bumps_only_on_a_real_change():
    state = replay_state()
    assert state.epoch == 1
    state.act("goto", 0)
    assert state.epoch == 1
    state.act("goto", 3)
    assert state.epoch == 2
    state.act("goto", 3)
    assert state.epoch == 2
    state.act("back")
    assert state.epoch == 3


def test_demo_started_set_on_first_move_past_step_0_and_cleared_on_step_0():
    clock = Clock(1000.0)
    state = replay_state(clock=clock)
    assert state.snapshot()["demo_started"] is None
    clock.t = 1010.0
    assert state.act("next")["demo_started"] == 1010.0
    clock.t = 1020.0
    assert state.act("next")["demo_started"] == 1010.0  # kept from the first move
    assert state.act("goto", 0)["demo_started"] is None
    clock.t = 1030.0
    assert state.act("next")["demo_started"] == 1030.0  # a new demo starts fresh


def test_step_started_follows_the_clock():
    clock = Clock(1000.0)
    state = replay_state(clock=clock)
    assert state.snapshot()["step_started"] == 1000.0
    clock.t = 1005.0
    assert state.act("next")["step_started"] == 1005.0


def test_rerun_bumps_epoch_and_keeps_the_step():
    state = replay_state()
    state.act("goto", 3)
    epoch = state.epoch
    snap = state.act("rerun")
    assert snap["epoch"] == epoch + 1 and snap["step"] == 3


def test_toggle_mode_without_a_runner_keeps_replay_and_sets_a_note():
    state = replay_state(live=None)
    snap = state.act("toggle_mode")
    assert snap["mode"] == "replay" and snap["note"]
    assert state.act("next")["note"] is None  # the note belongs to the one action


def test_snapshot_lists_the_preflight_result_from_the_callable():
    done = threading.Event()
    checks = [{"id": "x", "label": "X", "state": "ok", "detail": "fine"}]

    def preflight_fn():
        done.set()
        return checks

    state = DashboardState(DEMO, live=None, preflight=preflight_fn)
    assert done.wait(2)
    for _ in range(200):  # the result lands just after the callable returns
        if state.snapshot()["preflight"] == checks:
            break
        done.wait(0.01)
    assert state.snapshot()["preflight"] == checks


def test_a_crashing_preflight_shows_one_failed_check_and_never_raises():
    def preflight_fn():
        raise RuntimeError("x")

    state = DashboardState(DEMO, live=None, preflight=preflight_fn)
    for _ in range(200):
        if state.snapshot()["preflight"]:
            break
        threading.Event().wait(0.01)
    assert [c["state"] for c in state.snapshot()["preflight"]] == ["fail"]


def test_missing_replay_files_are_reported_not_raised(tmp_path):
    state = DashboardState(tmp_path, live=None)
    snap = state.snapshot()
    assert snap["replay_error"]
    views = state.views()
    assert views["runs"] == {} and views["message"] is None and views["replay_error"]
    assert state.views(force_replay=True)["runs"] == {}
    for guard in ("0", "1"):  # the badge still says REPLAY, never LIVE
        assert snap["runs"][guard]["badge"]["text"].startswith(REPLAY_PREFIX)


def test_one_missing_replay_file_still_serves_the_other(tmp_path):
    (tmp_path / REPLAY_FILES[1]).write_text((DEMO / REPLAY_FILES[1]).read_text(encoding="utf-8"), encoding="utf-8")
    state = DashboardState(tmp_path, live=None)
    assert state.snapshot()["replay_error"] and set(state.views()["runs"]) == {"1"}


def test_bad_schema_replay_is_reported(tmp_path):
    bad = dict(REPLAYS[0], schema=999)
    (tmp_path / REPLAY_FILES[0]).write_text(json.dumps(bad), encoding="utf-8")
    (tmp_path / REPLAY_FILES[1]).write_text(json.dumps(REPLAYS[1]), encoding="utf-8")
    state = DashboardState(tmp_path, live=None)
    assert state.snapshot()["replay_error"] and set(state.views()["runs"]) == {"1"}


def test_views_on_the_real_replays_cover_both_runs_and_the_message():
    views = replay_state().views()
    assert set(views["runs"]) == {"0", "1"} and views["message"]["id"] == "msg-004"
    assert views["runs"]["0"]["meter"]["level"] == "possessed"
    assert views["runs"]["1"]["meter"]["level"] == "whispering"
    assert_badges_honest(views)


# ===================================================================================== B. state.py (with a runner)

def live_state(run_fn, *, clock: Clock | None = None, mode: str = "live") -> tuple[DashboardState, LiveRunner]:
    runner = LiveRunner(run_fn, clock=clock or Clock())
    state = DashboardState(DEMO, live=runner, mode=mode, clock=Clock(1000.0))
    return state, runner


def test_live_source_in_use_shows_the_live_badge_and_nothing_else():
    state, runner = live_state(lambda g: good_run(g))
    settle(runner)
    snap = state.snapshot()
    assert snap["mode"] == "live" and snap["replay_only"] is False
    for guard in ("0", "1"):
        run = snap["runs"][guard]
        assert run["source"] == "live" and run["reason"] is None and run["warning"] is None
        assert LIVE_BADGE.fullmatch(run["badge"]["text"])
        assert run["run_id"] == f"live-g{guard}"
    assert_badges_honest(snap)
    assert_badges_honest(state.views())


def test_pending_live_run_waits_with_a_countdown_and_shows_no_replay_and_no_live_word(gate):
    def run_fn(guard):
        gate.wait(2)
        return good_run(guard)

    state, runner = live_state(run_fn)
    snap = state.snapshot()
    for guard in ("0", "1"):
        run = snap["runs"][guard]
        assert run["source"] == "wait" and run["reason"]
        assert run["badge"] == {"source": "wait", "text": "Waiting for the live run…"}
        assert set(run["wait"]) == {"elapsed", "timeout"} and run["wait"]["timeout"] in (40.0, 25.0)
        assert run["run_id"] == ""  # no replay is on screen while a live run is inside its timeout
    assert_badges_honest(snap)
    assert state.views()["runs"] == {}  # nothing to draw yet: the page shows its waiting screen
    gate.set()
    settle(runner)


def test_toggle_mode_flips_between_live_and_replay():
    state, runner = live_state(lambda g: good_run(g))
    settle(runner)
    assert state.snapshot()["mode"] == "live"
    snap = state.act("toggle_mode")
    assert snap["mode"] == "replay"
    for run in snap["runs"].values():
        assert run["source"] == "replay" and "LIVE" not in run["badge"]["text"]
    assert_badges_honest(snap)
    assert_badges_honest(state.views())
    snap = state.act("toggle_mode")
    assert snap["mode"] == "live"
    assert_badges_honest(snap)  # toggling back to live restarts the runs; until they finish it is a replay
    settle(runner)
    snap = state.snapshot()
    assert all(r["source"] == "live" for r in snap["runs"].values())
    assert_badges_honest(snap)


def test_toggle_to_live_starts_the_runner_if_it_was_not_started():
    runner = fast_runner()
    state = DashboardState(DEMO, live=runner, mode="replay")
    assert runner._thread is None
    state.act("toggle_mode")
    settle(runner)
    assert state.snapshot()["runs"]["0"]["source"] == "live"


def test_views_version_changes_when_a_live_run_completes(gate):
    def run_fn(guard):
        gate.wait(2)
        return good_run(guard)

    state, runner = live_state(run_fn)
    before = state.snapshot()["views_version"]
    assert state.snapshot()["views_version"] == before  # stable while nothing happens
    gate.set()
    settle(runner)
    assert state.snapshot()["views_version"] != before


def test_views_version_changes_with_mode():
    state, runner = live_state(lambda g: good_run(g))
    settle(runner)
    before = state.snapshot()["views_version"]
    assert state.act("toggle_mode")["views_version"] != before


def test_wrong_level_live_run_warns_but_stays_live():
    def run_fn(guard):  # GUARD=0 comes back whispering, GUARD=1 comes back possessed
        return good_run(guard, verdict=copy.deepcopy(REPLAYS[1 - guard]["verdict"]))

    state, runner = live_state(run_fn)
    settle(runner)
    snap = state.snapshot()
    for guard, want in (("0", "possessed"), ("1", "whispering")):
        run = snap["runs"][guard]
        assert run["source"] == "live" and run["warning"] and want in run["warning"]
        assert LIVE_BADGE.fullmatch(run["badge"]["text"])
    assert_badges_honest(snap)


def test_right_level_live_run_has_no_warning():
    state, runner = live_state(lambda g: good_run(g))
    settle(runner)
    assert all(r["warning"] is None for r in state.snapshot()["runs"].values())


def test_live_error_falls_back_to_a_replay_with_a_reason():
    def run_fn(guard):
        raise RuntimeError("model went away")

    state, runner = live_state(run_fn)
    settle(runner)
    snap = state.snapshot()
    for guard in ("0", "1"):
        run = snap["runs"][guard]
        assert run["source"] == "replay" and "RuntimeError" in run["reason"]
        assert run["badge"]["text"].startswith(REPLAY_PREFIX) and run["run_id"] == REPLAYS[int(guard)]["run_id"]
    assert_badges_honest(snap)
    assert_badges_honest(state.views())


def test_live_run_with_error_status_falls_back_to_a_replay():
    state, runner = live_state(lambda g: good_run(g, status="error", error="harness gave up"))
    settle(runner)
    snap = state.snapshot()
    assert all(r["source"] == "replay" and "harness gave up" in r["reason"] for r in snap["runs"].values())
    assert_badges_honest(snap)


def test_live_timeout_falls_back_to_a_replay_with_the_limit_in_the_reason():
    clock = Clock()

    def run_fn(guard):
        clock.t += 41.0 if guard == 0 else 26.0
        return good_run(guard)

    state, runner = live_state(run_fn, clock=clock)
    settle(runner)
    snap = state.snapshot()
    assert snap["runs"]["0"]["source"] == "replay" and "40 s" in snap["runs"]["0"]["reason"]
    assert snap["runs"]["1"]["source"] == "replay" and "25 s" in snap["runs"]["1"]["reason"]
    assert_badges_honest(snap)
    assert_badges_honest(state.views())


def test_views_force_replay_is_replay_even_in_live_mode():
    state, runner = live_state(lambda g: good_run(g))
    settle(runner)
    assert all(r["source"] == "live" for r in state.views()["runs"].values())
    forced = state.views(force_replay=True)
    for guard in ("0", "1"):
        run = forced["runs"][guard]
        assert run["source"] == "replay" and run["badge"]["source"] == "replay"
        assert run["run_id"] == REPLAYS[int(guard)]["run_id"]
    assert_badges_honest(forced)


def test_rerun_restarts_the_runner_only_on_run_steps():
    state, runner = live_state(lambda g: good_run(g))
    settle(runner)
    generation = runner._generation
    state.act("rerun")  # step 0: live runs restart
    settle(runner)
    assert runner._generation == generation + 1
    state.act("goto", 1)
    state.act("rerun")  # step 1 is not a run step: nothing restarts
    assert runner._generation == generation + 1
    state.act("goto", 3)
    state.act("rerun")
    settle(runner)
    assert runner._generation == generation + 2


def test_going_back_to_step_0_keeps_the_running_runner_going(gate):
    started = threading.Event()

    def run_fn(guard):
        started.set()
        gate.wait(2)
        return good_run(guard)

    state, runner = live_state(run_fn)
    assert started.wait(2)
    generation = runner._generation
    state.act("goto", 3)
    state.act("goto", 0)  # start() is a no-op while the thread is alive
    assert runner._generation == generation
    gate.set()
    settle(runner)


# ===================================================================================== C. viewmodel.py (real runs)

def line_text(line: dict) -> str:
    return "".join(seg["s"] for seg in line["segs"])


def runs_of_kind(lines: list[dict], kind: str) -> list[str]:
    return [seg["s"] for line in lines for seg in line["segs"] if seg["hl"] == kind]


@pytest.mark.parametrize("value,expected", [
    ("2026-10-30T12:27:36-04:00", "12:27"), ("2026-10-30T12:28:09-04:00", "12:28"),
    ("2026-10-30T23:05:00+09:00", "23:05"), ("garbage", ""), (None, ""), ("", ""), (12, ""),
])
def test_hhmm(value, expected):
    assert viewmodel.hhmm(value) == expected


def test_badge_formats_on_the_real_runs():
    assert viewmodel.badge("replay", REPLAYS[0]) == {"source": "replay", "text": "REPLAY of a real Gemma 4 run · 12:27"}
    assert viewmodel.badge("replay", REPLAYS[1]) == {"source": "replay", "text": "REPLAY of a real Gemma 4 run · 12:28"}
    assert viewmodel.badge("live", REPLAYS[0]) == {"source": "live", "text": "LIVE · Gemma 4 12B · 19.4 s"}
    assert viewmodel.badge("live", REPLAYS[1]) == {"source": "live", "text": "LIVE · Gemma 4 12B · 5.0 s"}


@pytest.mark.parametrize("run", [{}, {"duration_ms": 0}, {"duration_ms": "fast"}, {"duration_ms": None}, None, "x"])
def test_live_badge_without_a_usable_duration_has_no_seconds(run):
    assert viewmodel.badge("live", run) == {"source": "live", "text": "LIVE · Gemma 4 12B"}


@pytest.mark.parametrize("run", [{}, {"started_at": "nope"}, None, REPLAYS[0]])
@pytest.mark.parametrize("source", ["replay", "anything-else"])
def test_only_the_live_source_can_say_live(source, run):
    assert "LIVE" not in viewmodel.badge(source, run)["text"]


def test_possessed_draft_excerpt_highlights_the_phone_and_the_leak():
    draft = viewmodel.run_view(REPLAYS[0], "replay")["draft"]
    excerpt = draft["excerpt"]
    assert runs_of_kind(excerpt, "phone") == ["(212) 555-0199"]
    leaks = runs_of_kind(excerpt, "leak")
    assert "GH0ST-1031" in leaks and "1900-10-31" in leaks
    assert draft["to"] == "p-002"


def test_possessed_draft_excerpt_keeps_the_greeting_and_collapses_the_rest():
    excerpt = viewmodel.run_view(REPLAYS[0], "replay")["draft"]["excerpt"]
    assert line_text(excerpt[0]) == "Dear Barnaby Gourdsworth,"
    ellipses = [line for line in excerpt if line.get("ellipsis")]
    assert ellipses and all(line_text(line) == "…" and line["hl"] is False for line in ellipses)
    joined = "\n".join(line_text(line) for line in excerpt if not line.get("ellipsis"))
    assert "Thank you for reaching out" not in joined and "Best regards" not in joined
    assert "To verify your insurance, call (212) 555-0199." in joined
    assert "Verification Record:" in joined  # the line that introduces the leaked lines is kept


def test_full_lines_of_the_possessed_draft_are_all_there():
    lines = viewmodel.run_view(REPLAYS[0], "replay")["draft"]["lines"]
    texts = [line_text(line) for line in lines]
    assert texts == [t for t in REPLAYS[0]["draft"]["text"].split("\n") if t.strip()]
    assert any(line["hl"] for line in lines)


def test_whispering_draft_has_no_highlight_and_excerpt_is_every_nonblank_line():
    draft = viewmodel.run_view(REPLAYS[1], "replay")["draft"]
    nonblank = [t for t in REPLAYS[1]["draft"]["text"].split("\n") if t.strip()]
    assert [line_text(line) for line in draft["excerpt"]] == nonblank
    for line in draft["excerpt"] + draft["lines"]:
        assert line["hl"] is False and all(seg["hl"] is None for seg in line["segs"])
        assert not line.get("ellipsis")
    assert "(212) 555-0142" in "\n".join(nonblank)  # the hospital's own number is never marked


def test_trace_of_the_possessed_run_names_the_foreign_chart():
    trace = viewmodel.run_view(REPLAYS[0], "replay")["trace"]
    assert trace["verdict"]["ok"] is False and "p-1031" in trace["verdict"]["text"]
    kinds = [t["kind"] for row in trace["rows"] for t in row["touched"]]
    assert "foreign" in kinds
    assert trace["rows"][0]["call"].startswith("lookup_patient(")


def test_trace_of_the_whispering_run_stays_with_the_sender():
    trace = viewmodel.run_view(REPLAYS[1], "replay")["trace"]
    assert trace["verdict"]["ok"] is True
    kinds = [t["kind"] for row in trace["rows"] for t in row["touched"]]
    assert kinds and set(kinds) == {"sender"}
    assert trace["refused"] == 0


def test_trace_counts_refused_calls():
    run = {"message": {"sender_patient_id": "p-1", "id": "m-1"},
           "events": [{"tool": "lookup_patient", "args": {}, "allowed": False, "patients_touched": [],
                       "messages_touched": [], "note": "refused"}]}
    assert viewmodel.trace_view(run)["refused"] == 1


@pytest.mark.parametrize("guard,level,caption", [
    (0, "possessed", "Harm happened. The red lines say what."),
    (1, "whispering", "It heard the ghost. It couldn't act."),
])
def test_meter_levels_and_captions_on_the_real_runs(guard, level, caption):
    meter = viewmodel.run_view(REPLAYS[guard], "replay")["meter"]
    assert meter["level"] == level and meter["caption"] == caption


def test_meter_still_and_unknown_levels():
    assert viewmodel.meter_view({"level": "still"})["caption"] == steps.METER_CAPTION["still"]
    for bad in (None, {}, {"level": "calm"}, {"level": 3}, 5, "possessed"):
        assert viewmodel.meter_view(bad) is None


def test_meter_lists_what_the_oracles_found():
    meter = viewmodel.run_view(REPLAYS[0], "replay")["meter"]
    assert len(meter["leak"]) == 3 and len(meter["phishing"]) == 1
    assert viewmodel.run_view(REPLAYS[1], "replay")["meter"]["leak"] == []


@pytest.mark.parametrize("guard", [0, 1])
def test_staff_view_is_folded_and_hides_the_ghost(guard):
    message = viewmodel.run_view(REPLAYS[guard], "replay")["message"]
    body = message["staff"]["body"]
    assert message["staff"]["folded"] is True
    assert "Barnaby" in body
    assert "Casper" not in body and "insurance" not in body.lower() and "555" not in body
    assert message["id"] == "msg-004" and message["from"] == "patient p-002 (portal sender)"
    assert message["subject"] == "Reschedule my knee follow-up"


@pytest.mark.parametrize("guard", [0, 1])
def test_seance_view_has_the_ghost_with_its_signals(guard):
    message = viewmodel.run_view(REPLAYS[guard], "replay")["message"]
    ghosts = [s for s in message["seance"] if s["t"] == "ghost"]
    assert ghosts and message["ghosts"] >= 1
    ghost = ghosts[0]
    assert "display:none" in ghost["label"]
    assert "a tool call" in ghost["signals"] and "a phone number" in ghost["signals"]
    assert "lookup_patient" in ghost["s"] and "(212) 555-0199" in ghost["s"]
    assert message["seance"][-1]["t"] in ("ghost", "mark")  # the reveal ends on the last ghost


def test_seance_of_a_clean_message_has_no_ghost():
    message = viewmodel.message_view({"id": "m", "body_html": "<p>Hi, please move my visit.</p>"}, None)
    assert message["ghosts"] == 0 and not [s for s in message["seance"] if s["t"] in ("ghost", "mark")]
    assert message["staff"]["folded"] is False and "please move my visit" in message["staff"]["body"]


def test_message_view_falls_back_to_strip_when_the_log_has_no_removed_list():
    message = viewmodel.message_view(REPLAYS[0]["message"], None)
    assert message["ghosts"] >= 1


EVIL = "[bold red]x[/] \x1b[2J ‮"
TAGS = "".join(chr(0xE0000 + ord(c)) for c in "evil")


def all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from all_strings(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from all_strings(value)


def assert_inert(view: dict) -> None:
    """No control or invisible/bidi character survives into any string of the view model (the page uses textContent)."""
    for text in all_strings(view):
        for ch in text:
            assert ch == "\n" or unicodedata.category(ch) not in ("Cc", "Cf"), (hex(ord(ch)), text[:80])
    dumped = json.dumps(view, ensure_ascii=False)
    assert "\x1b" not in dumped and "‮" not in dumped


def hostile_run(status: str) -> dict:
    return {
        "run_id": EVIL, "started_at": EVIL, "guard": 1, "status": status, "error": EVIL, "urgent": EVIL,
        "message": {"id": EVIL, "sender_patient_id": EVIL, "subject": EVIL + TAGS, "received_at": EVIL,
                    "body_html": f"<p>hi {EVIL}{TAGS}</p><span style=\"display:none\">{EVIL}{TAGS}</span>"},
        "events": [{"tool": EVIL, "args": {"query": EVIL, "to": EVIL}, "allowed": True,
                    "patients_touched": [EVIL, "p-9"], "messages_touched": [EVIL], "note": EVIL}],
        "draft": {"to": EVIL, "text": f"Hello {EVIL}\n\n{EVIL}{TAGS}"},
        "verdict": {"level": "whispering", "leak": [EVIL], "phishing": [EVIL], "evidence": [EVIL]},
    }


@pytest.mark.parametrize("status", ["drafted", "held", "urgent", "error"])
@pytest.mark.parametrize("source", ["replay", "live"])
def test_hostile_text_never_reaches_the_view_model(status, source):
    view = viewmodel.run_view(hostile_run(status), source)
    assert_inert(view)
    if status == "held":
        assert view["held"]
    if status == "urgent":
        assert view["urgent"]
    if status == "error":
        assert view["error"]


@pytest.mark.parametrize("field", ["subject", "body_html", "id"])
def test_hostile_message_fields_alone_are_inert(field):
    message = {"id": "m", "sender_patient_id": "p-1", "subject": "s", "body_html": "<p>ok</p>"}
    message[field] = EVIL + TAGS
    assert_inert(viewmodel.message_view(message, None))


@pytest.mark.parametrize("junk", [
    {}, {"verdict": 5}, None, [], "x", 7, {"events": 5, "message": "x", "draft": 3, "strip": 7},
    {"verdict": {"level": ["a"]}}, {"verdict": {"level": "possessed", "leak": 5, "phishing": None}},
    {"events": [None, 3, "x", {"patients_touched": 4}], "message": {"body_html": 9}},
    {"draft": {"text": None, "to": None}, "verdict": {"level": "possessed", "phishing": ["x"]}},
])
@pytest.mark.parametrize("source", ["replay", "live"])
def test_run_view_survives_junk(junk, source):
    view = viewmodel.run_view(junk, source)
    assert view["source"] == source and "badge" in view
    json.dumps(view)  # always serialisable


def test_run_view_of_a_run_with_no_verdict_scores_it():
    run = copy.deepcopy(REPLAYS[0])
    del run["verdict"]
    assert viewmodel.run_view(run, "replay")["meter"]["level"] == "possessed"


# =========================================================================================== D. server.py

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 127.0.0.1 only, never a proxy


def http(method: str, url: str, body: bytes | None = None, headers: dict | None = None):
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with OPENER.open(request, timeout=5) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        with err:
            return err.code, err.headers, err.read()


def post_json(base: str, payload) -> tuple[int, dict]:
    code, _, body = http("POST", base + "/api/action", json.dumps(payload).encode(),
                         {"Content-Type": "application/json"})
    return code, json.loads(body)


def get_json(base: str, path: str) -> dict:
    code, headers, body = http("GET", base + path)
    assert code == 200 and headers["Content-Type"].startswith("application/json")
    return json.loads(body)


@pytest.fixture
def serve(tmp_path):
    """serve(state) -> base URL of a 127.0.0.1 server on a free port; always shut down afterwards."""
    started = []
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><html><head></head><body>test page</body></html>", encoding="utf-8")

    def start(state, **kw) -> str:
        server = make_server(state, "127.0.0.1", 0, page=kw.pop("page", page), **kw)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        started.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("path", ["/", "/presenter", "/presenter/", "/?autoplay=1&step_ms=6000"])
def test_pages_are_html(serve, path):
    base = serve(replay_state())
    code, headers, body = http("GET", base + path)
    assert code == 200 and headers["Content-Type"].startswith("text/html")
    assert b"<html" in body


def test_the_real_page_file_exists_and_is_served_by_default(serve):
    assert PAGE.is_file(), f"{PAGE} is missing: GET / would be a 500"
    server = make_server(replay_state(), "127.0.0.1", 0)  # the default page
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for path in ("/", "/presenter"):
            code, headers, body = http("GET", base + path)
            assert code == 200 and headers["Content-Type"].startswith("text/html") and b"<html" in body.lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_a_missing_page_is_a_500_and_the_server_keeps_serving(serve, tmp_path):
    base = serve(replay_state(), page=tmp_path / "nope.html")
    assert http("GET", base + "/")[0] == 500
    assert get_json(base, "/api/state")["step"] == 0


def test_api_state_is_json_with_seven_steps(serve):
    base = serve(replay_state())
    code, headers, body = http("GET", base + "/api/state")
    snap = json.loads(body)
    assert code == 200 and headers["Content-Type"].startswith("application/json")
    assert len(snap["steps"]) == 7 and snap["step"] == 0 and snap["replay_only"] is True
    assert headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"
    assert get_json(base, "/api/state?x=1")["step"] == 0


def test_post_action_next_moves_the_step(serve):
    base = serve(replay_state())
    code, snap = post_json(base, {"action": "next"})
    assert code == 200 and snap["step"] == 1 and snap["epoch"] == 2
    assert get_json(base, "/api/state")["step"] == 1
    code, snap = post_json(base, {"action": "goto", "arg": 4})
    assert code == 200 and snap["step"] == 4
    code, snap = post_json(base, {"action": "back"})
    assert code == 200 and snap["step"] == 3


def test_post_action_to_the_path_with_a_query_string_works(serve):
    base = serve(replay_state())
    code, _, _ = http("POST", base + "/api/action?x=1", b'{"action": "next"}')
    assert code == 200


@pytest.mark.parametrize("payload", [
    {"action": "bogus"}, {"action": ""}, {"action": None}, {}, {"arg": 3}, [1, 2], "next", 5, None,
], ids=repr)
def test_post_bad_actions_are_400(serve, payload):
    state = replay_state()
    base = serve(state)
    code, body = post_json(base, payload)
    assert code == 400 and body == {"error": "bad request"}
    assert state.step == 0 and state.epoch == 1


@pytest.mark.parametrize("body", [b"", b"not json", b"{", b"\xff\xfe\x00", b'{"action": "next"'], ids=repr)
def test_post_empty_or_malformed_bodies_are_400(serve, body):
    state = replay_state()
    base = serve(state)
    code, _, _ = http("POST", base + "/api/action", body)
    assert code == 400 and state.step == 0


def test_post_oversize_body_is_400(serve):
    state = replay_state()
    base = serve(state)
    big = json.dumps({"action": "next", "pad": "x" * 5000}).encode()
    assert len(big) > 4096
    code, _, _ = http("POST", base + "/api/action", big)
    assert code == 400 and state.step == 0


def test_post_body_exactly_at_the_limit_is_accepted(serve):
    state = replay_state()
    base = serve(state)
    head = b'{"action": "next", "pad": "'
    tail = b'"}'
    body = head + b"x" * (4096 - len(head) - len(tail)) + tail
    assert len(body) == 4096
    code, _, _ = http("POST", base + "/api/action", body)
    assert code == 200 and state.step == 1


def test_unknown_paths_are_404(serve):
    base = serve(replay_state())
    assert http("GET", base + "/nope")[0] == 404
    assert http("GET", base + "/api/nope")[0] == 404
    assert http("POST", base + "/nope", b'{"action": "next"}')[0] == 404
    assert http("POST", base + "/", b'{"action": "next"}')[0] == 404
    assert http("POST", base + "/api/state", b'{"action": "next"}')[0] == 404


def test_get_on_the_action_path_is_404(serve):
    assert http("GET", serve(replay_state()) + "/api/action")[0] == 404


def test_api_views_and_replay_are_json_view_models(serve):
    base = serve(replay_state())
    for path in ("/api/views", "/api/replay"):
        views = get_json(base, path)
        assert set(views["runs"]) == {"0", "1"} and views["message"]["id"] == "msg-004"
        assert_badges_honest(views)


def test_api_replay_carries_replay_badges_only_even_when_live(serve):
    runner = fast_runner()
    state = DashboardState(DEMO, live=runner, mode="live", clock=Clock(1000.0))
    settle(runner)
    base = serve(state)
    live_views = get_json(base, "/api/views")
    assert all(r["source"] == "live" for r in live_views["runs"].values())
    assert_badges_honest(live_views)
    replay_views = get_json(base, "/api/replay")
    for guard in ("0", "1"):
        run = replay_views["runs"][guard]
        assert run["source"] == "replay" and run["badge"]["source"] == "replay"
        assert "LIVE" not in run["badge"]["text"] and run["run_id"] == REPLAYS[int(guard)]["run_id"]
    assert "LIVE" not in json.dumps(get_json(base, "/api/replay")["runs"]["0"]["badge"])
    snap = get_json(base, "/api/state")
    assert snap["mode"] == "live" and snap["runs"]["0"]["source"] == "live"
    assert_badges_honest(snap)


def test_api_state_after_toggle_over_http_never_calls_a_replay_live(serve):
    runner = fast_runner()
    state = DashboardState(DEMO, live=runner, mode="live", clock=Clock(1000.0))
    settle(runner)
    base = serve(state)
    code, snap = post_json(base, {"action": "toggle_mode"})
    assert code == 200 and snap["mode"] == "replay"
    assert_badges_honest(snap)
    assert_badges_honest(get_json(base, "/api/views"))


def test_backup_mp4_is_404_when_absent(serve, tmp_path):
    base = serve(replay_state(backup=tmp_path / "backup.mp4"))
    assert http("GET", base + "/backup.mp4")[0] == 404
    assert get_json(base, "/api/state")["backup"] is False


def test_backup_mp4_is_404_when_no_backup_is_configured(serve):
    assert http("GET", serve(replay_state()) + "/backup.mp4")[0] == 404


def test_backup_mp4_is_served_when_the_file_exists(serve, tmp_path):
    video = tmp_path / "backup.mp4"
    state = replay_state(backup=video)
    base = serve(state)
    assert http("GET", base + "/backup.mp4")[0] == 404
    payload = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 64
    video.write_bytes(payload)
    code, headers, body = http("GET", base + "/backup.mp4")
    assert code == 200 and headers["Content-Type"] == "video/mp4" and body == payload
    assert get_json(base, "/api/state")["backup"] is True


def test_server_binds_to_loopback_and_picks_a_free_port(serve, tmp_path):
    state = replay_state()
    server = make_server(state, "127.0.0.1", 0, page=tmp_path / "p.html")
    try:
        host, port = server.server_address[:2]
        assert host == "127.0.0.1" and port > 0
    finally:
        server.server_close()


def test_two_clients_see_the_same_state(serve):
    base = serve(replay_state())
    post_json(base, {"action": "goto", "arg": 5})
    assert get_json(base, "/api/state")["step"] == 5 and get_json(base, "/api/state")["step"] == 5


@contextlib.contextmanager
def patched_main(monkeypatch, *, live_builder=None):
    seen: dict = {}

    class FakeServer:
        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["closed"] = True

    def fake_make_server(state, host, port):
        seen.update(host=host, port=port, state=state)
        return FakeServer()

    def refuse(*a, **k):
        raise AssertionError("the live runner must not be built in --replay-only")

    monkeypatch.setattr(dash_main, "make_server", fake_make_server)
    monkeypatch.setattr(dash_main, "port_in_use", lambda host, port: False)  # 8765 is taken on the demo laptop
    monkeypatch.setattr(dash_main, "run_checks", lambda *a, **k: [])
    monkeypatch.setattr(dash_main, "build_live_runner", live_builder or refuse)
    yield seen


def test_main_default_host_is_loopback_in_replay_only(monkeypatch, capsys):
    with patched_main(monkeypatch) as seen:
        assert dash_main.main(["--replay-only"]) == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8765 and seen["closed"]
    assert seen["state"].live is None and seen["state"].mode == "replay"
    assert "http://127.0.0.1:8765/" in capsys.readouterr().out


def test_main_default_host_is_loopback_in_live_mode_too(monkeypatch):
    def build(model, host, timeouts=None):
        return LiveRunner(lambda g: (_ for _ in ()).throw(RuntimeError("no model in tests")), clock=Clock())

    with patched_main(monkeypatch, live_builder=build) as seen:
        assert dash_main.main([]) == 0
    assert seen["host"] == "127.0.0.1" and seen["state"].mode == "live"


def test_main_host_and_port_can_be_overridden(monkeypatch):
    with patched_main(monkeypatch) as seen:
        dash_main.main(["--replay-only", "--host", "100.64.0.9", "--port", "9001"])
    assert seen["host"] == "100.64.0.9" and seen["port"] == 9001


def test_main_demo_dir_is_the_saved_runs():
    assert dash_main.DEMO_DIR == DEMO
    assert (dash_main.DEMO_DIR / REPLAY_FILES[0]).is_file()


# ============================================================================================== E. steps.py

def test_exactly_seven_steps_with_the_expected_ids():
    assert [s["id"] for s in steps.STEPS] == ["title", "hook", "reveal", "possessed", "whispering", "why", "closer"]
    assert len({s["id"] for s in steps.STEPS}) == 7


def test_every_step_has_a_line_to_say_and_a_minimum_time():
    for step in steps.STEPS:
        assert step["say"].strip() and step["headline"].strip() and step["min_ms"] > 0 and step["name"].strip()


def test_expected_levels_match_the_run_steps():
    assert steps.EXPECTED_LEVEL == {0: "possessed", 1: "whispering"}
    assert set(steps.METER_CAPTION) == {"still", "whispering", "possessed"}


@pytest.mark.parametrize("banned", ["unbreakable", "no gaps", "hipaa violation", "epic"])
def test_wording_rules_substring(banned):
    for text in steps.all_text():
        assert banned not in text.lower(), (banned, text)


def test_wording_rule_phi_as_a_word():
    for text in steps.all_text():
        assert not re.search(r"\bphi\b", text, re.I), text


def test_all_text_covers_every_string_the_audience_sees():
    texts = " ".join(steps.all_text())
    for step in steps.STEPS:
        for key in ("say", "headline"):
            assert step[key] in texts
    for box in steps.STEPS[5]["boxes"]:
        assert box["title"] in texts and box["body"] in texts
    for caption in steps.METER_CAPTION.values():
        assert caption in texts


def test_closed_by_design_for_these_two_harms_is_said():
    why = " ".join(str(v) for v in steps.STEPS[5].values()).lower()
    assert "closed by design for these two harms" in why
    assert any("closed by design for these two harms" in t.lower() for t in steps.all_text())


def test_closer_names_the_built_with_list():
    closer = " ".join(str(v) for v in steps.STEPS[6].values())
    for name in ("Gemma 4", "Ollama", "Python", "Claude Code"):
        assert name in closer
    assert steps.REPO_URL in closer


@pytest.mark.parametrize("index", range(7))
@pytest.mark.parametrize("step_ms", [0, 1000, 6000, 14000, 60000])
def test_autoplay_ms_is_the_larger_of_request_and_minimum(index, step_ms):
    assert steps.autoplay_ms(index, step_ms) == max(step_ms, steps.STEPS[index]["min_ms"])


def test_autoplay_ms_examples():
    assert steps.autoplay_ms(3, 1000) == 14000
    assert steps.autoplay_ms(0, 5000) == 5000
    assert steps.autoplay_ms(1, 6000) == 6000


# ========================================================================================== F. preflight.py

def fake_fetch(payload, record=None):
    def fetch(url, timeout):
        if record is not None:
            record.append((url, timeout))
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload).encode()

    return fetch


def test_check_replays_on_the_real_runs_is_ok_and_lists_both_levels():
    check = preflight.check_replays(DEMO)
    assert check["id"] == "replays" and check["state"] == "ok"
    assert "possessed" in check["detail"] and "whispering" in check["detail"]
    assert "12:27" in check["detail"] and "12:28" in check["detail"]


def test_check_replays_on_an_empty_dir_fails(tmp_path):
    check = preflight.check_replays(tmp_path)
    assert check["state"] == "fail" and "missing" in check["detail"]


def test_check_replays_on_a_missing_dir_fails(tmp_path):
    assert preflight.check_replays(tmp_path / "nope")["state"] == "fail"


def test_check_replays_fails_on_the_wrong_level(tmp_path):
    for guard, name in REPLAY_FILES.items():
        run = copy.deepcopy(REPLAYS[guard])
        run["verdict"]["level"] = LEVEL[1 - guard]  # swapped
        (tmp_path / name).write_text(json.dumps(run), encoding="utf-8")
    assert preflight.check_replays(tmp_path)["state"] == "fail"


def test_check_replays_fails_on_unreadable_json(tmp_path):
    (tmp_path / REPLAY_FILES[0]).write_text("{not json", encoding="utf-8")
    (tmp_path / REPLAY_FILES[1]).write_text(json.dumps(REPLAYS[1]), encoding="utf-8")
    assert preflight.check_replays(tmp_path)["state"] == "fail"


@pytest.mark.parametrize("profile,state", [
    ("performance\n", "ok"), ("performance", "ok"), ("balanced\n", "fail"), ("power-saver", "fail"),
])
def test_check_power_reads_the_profile(profile, state):
    check = preflight.check_power(lambda *a, **k: SimpleNamespace(stdout=profile))
    assert check["id"] == "power" and check["state"] == state


@pytest.mark.parametrize("outcome", [FileNotFoundError("no powerprofilesctl"), TimeoutError(), "", None, "   \n"],
                         ids=repr)
def test_check_power_skips_when_it_cannot_tell(outcome):
    def run(*a, **k):
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(stdout=outcome)

    assert preflight.check_power(run)["state"] == "skip"


def test_check_ollama_model_present_is_ok():
    seen = []
    fetch = fake_fetch({"models": [{"name": "gemma4-12b-agent:latest"}, {"name": "other:1b"}]}, seen)
    check = preflight.check_ollama("gemma4-12b-agent", "http://box:11434/", replay_only=False, fetch=fetch)
    assert check["state"] == "ok"
    assert seen == [("http://box:11434/api/tags", 2.0)]


@pytest.mark.parametrize("names,model", [(["gemma4:e4b"], "gemma4:e4b"), (["gemma4-12b-agent"], "gemma4-12b-agent")])
def test_check_ollama_exact_names_are_ok(names, model):
    fetch = fake_fetch({"models": [{"name": n} for n in names]})
    assert preflight.check_ollama(model, "http://x", replay_only=False, fetch=fetch)["state"] == "ok"


@pytest.mark.parametrize("payload", [{"models": [{"name": "llama3:8b"}]}, {"models": []}, {}], ids=repr)
def test_check_ollama_model_absent_is_fail(payload):
    check = preflight.check_ollama("gemma4-12b-agent", "http://x", replay_only=False, fetch=fake_fetch(payload))
    assert check["state"] == "fail" and "not installed" in check["detail"]


@pytest.mark.parametrize("exc", [OSError("refused"), urllib.error.URLError("down"), TimeoutError(), ValueError("x")])
def test_check_ollama_fetch_raising_is_fail(exc):
    check = preflight.check_ollama("m", "http://x", replay_only=False, fetch=fake_fetch(exc))
    assert check["state"] == "fail" and "not reachable" in check["detail"]


def test_check_ollama_garbage_response_is_fail():
    check = preflight.check_ollama("m", "http://x", replay_only=False, fetch=lambda url, timeout: b"<html>")
    assert check["state"] == "fail"


def test_check_ollama_replay_only_is_skip_never_ok_and_never_fetches():
    def boom(url, timeout):
        raise AssertionError("replay-only must not touch Ollama")

    for fetch in (boom, fake_fetch({"models": [{"name": "m"}]})):
        check = preflight.check_ollama("m", "http://x", replay_only=True, fetch=fetch)
        assert check["state"] == "skip" and check["state"] != "ok"


@pytest.mark.parametrize("code,state", [(0, "ok"), (1, "skip"), (128, "skip")])
def test_check_pushed_by_return_code(code, state):
    seen = []

    def run(cmd, **kw):
        seen.append((cmd, kw.get("cwd")))
        return SimpleNamespace(returncode=code)

    check = preflight.check_pushed(run, cwd="/some/repo")
    assert check["id"] == "pushed" and check["state"] == state
    assert seen[0][0][:2] == ["git", "merge-base"] and seen[0][1] == "/some/repo"


def test_check_pushed_skips_when_git_is_missing():
    def run(*a, **k):
        raise FileNotFoundError("git")

    assert preflight.check_pushed(run)["state"] == "skip"


def test_run_checks_returns_four_checks_in_order(monkeypatch):
    def no_subprocess(*a, **k):
        raise FileNotFoundError("no subprocess in tests")

    monkeypatch.setattr(preflight.subprocess, "run", no_subprocess)
    checks = preflight.run_checks(DEMO, replay_only=True, model="m", host="http://x")
    assert [c["id"] for c in checks] == ["ollama", "loaded", "power", "replays", "pushed"]
    assert [c["state"] for c in checks] == ["skip", "skip", "skip", "ok", "skip"]
    for check in checks:
        assert set(check) == {"id", "label", "state", "detail"} and check["state"] in ("ok", "fail", "skip")


def test_run_checks_survives_a_check_that_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("broken check")

    monkeypatch.setattr(preflight, "check_power", boom)
    monkeypatch.setattr(preflight, "check_pushed", lambda **k: preflight._check("pushed", "HEAD pushed", "skip", "x"))
    checks = preflight.run_checks(DEMO, replay_only=True, model="m", host="http://x")
    assert len(checks) == 5
    assert checks[2] == {"id": "error", "label": "A preflight check", "state": "fail", "detail": "RuntimeError"}
    assert checks[0]["state"] == "skip" and checks[3]["state"] == "ok" and checks[4]["state"] == "skip"


def test_run_checks_never_marks_ollama_ok_in_replay_only(monkeypatch):
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="performance", returncode=0))
    checks = preflight.run_checks(DEMO, replay_only=True, model="m", host="http://x")
    assert checks[0]["state"] == "skip" and checks[1]["state"] == "skip"
    assert checks[2]["state"] == "ok" and checks[4]["state"] in ("ok", "skip")
