"""Dashboard milestone 2: the waiting state, the warm-up and loaded checks, the port guard, the timeout flags.

No Ollama, no model: fake runners, a fake clock, fake HTTP, and a real socket only to hold a port on 127.0.0.1.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
from test_dashboard import DEMO, REPLAYS, Clock, good_run, line_text, live_state, patched_main, settle

from dashboard import __main__ as dash_main
from dashboard import preflight, steps, viewmodel
from ghostreply import oracles
from dashboard.live import LiveRunner, Outcome, resolve
from dashboard.server import make_server
from dashboard.state import DashboardState


@pytest.fixture
def hold():
    event = threading.Event()
    yield event
    event.set()


def slow(hold: threading.Event):
    """A run_fn that holds until the test releases it, then returns a real-looking run log."""

    def run_fn(guard):
        hold.wait(3)
        return good_run(guard)

    return run_fn


def started(runner: LiveRunner) -> None:
    """Wait until GUARD=0 has begun, so it has read the fake clock at its start before a test moves the clock."""
    for _ in range(500):
        slot = runner._slots.get(0) or {}
        if slot.get("t0") is not None:
            return
        time.sleep(0.01)
    raise AssertionError("the runner never started")


def held_port() -> tuple[socket.socket, int]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    return sock, sock.getsockname()[1]


# ------------------------------------------------------------------------------- the waiting state

def test_a_live_run_inside_its_timeout_waits_then_goes_live_when_it_finishes(hold):
    clock = Clock(0.0)

    def run_fn(guard):
        hold.wait(3)
        return good_run(guard)

    state, runner = live_state(run_fn, clock=clock)
    started(runner)
    clock.t = 10.0
    run0 = state.snapshot()["runs"]["0"]
    assert run0["source"] == "wait" and run0["wait"] == {"elapsed": 10.0, "timeout": 40.0}
    assert state.snapshot()["runs"]["1"]["source"] == "wait"  # GUARD=1 is queued behind GUARD=0
    hold.set()
    settle(runner)
    done = state.snapshot()["runs"]
    assert done["0"]["source"] == "live" and done["1"]["source"] == "live"
    assert "wait" not in done["0"]


def test_a_live_run_past_its_timeout_falls_back_to_the_replay_and_says_why(hold):
    clock = Clock(0.0)
    state, runner = live_state(slow(hold), clock=clock)
    started(runner)
    clock.t = 41.0  # GUARD=0 has taken longer than 40 s
    run0 = state.snapshot()["runs"]["0"]
    assert run0["source"] == "replay" and "40 s" in run0["reason"]
    assert run0["badge"]["text"].startswith("REPLAY of a real Gemma 4 run") and "LIVE" not in run0["badge"]["text"]
    assert run0["run_id"] == REPLAYS[0]["run_id"]
    assert state.views()["runs"]["0"]["source"] == "replay"
    hold.set()
    settle(runner)
    assert state.snapshot()["runs"]["0"]["source"] == "replay"  # a late finish never promotes it back to live


def test_guard1_waiting_behind_a_stalled_guard0_times_out_too(hold):
    clock = Clock(0.0)
    state, runner = live_state(slow(hold), clock=clock)
    started(runner)
    clock.t = 41.0
    assert state.snapshot()["runs"]["1"]["source"] == "replay"
    hold.set()
    settle(runner)


def test_the_presenter_can_switch_a_waiting_run_to_the_replay(hold):
    state, runner = live_state(slow(hold))
    assert state.snapshot()["runs"]["0"]["source"] == "wait"
    state.act("toggle_mode")  # key L, or the "Use replay" button
    snap = state.snapshot()
    assert snap["mode"] == "replay" and snap["runs"]["0"]["source"] == "replay"
    hold.set()
    settle(runner)


def test_an_instant_error_falls_back_at_once_with_the_error_as_the_reason():
    def explode(guard):
        raise RuntimeError("model 'nope' not found")

    state, runner = live_state(explode)
    settle(runner)
    for guard in ("0", "1"):
        run = state.snapshot()["runs"][guard]
        assert run["source"] == "replay" and "not found" in run["reason"]
        assert run["badge"]["source"] == "replay" and "LIVE" not in run["badge"]["text"]


def test_a_wrong_level_live_run_warns_but_stays_live_until_the_presenter_switches():
    state, runner = live_state(lambda g: good_run(g, verdict={"level": "still", "leak": [], "phishing": [], "evidence": []}))
    settle(runner)
    run0 = state.snapshot()["runs"]["0"]
    assert run0["source"] == "live" and run0["warning"] and "possessed" in run0["warning"]
    state.act("toggle_mode")
    assert state.snapshot()["runs"]["0"]["source"] == "replay"


def test_the_warm_up_is_not_counted_against_guard0s_limit():
    clock = Clock(0.0)

    def slow_load():
        clock.t += 30.0  # a cold model takes 30 s to load into the GPU

    def run_fn(guard):
        clock.t += 5.0
        return good_run(guard)

    runner = LiveRunner(run_fn, clock=clock, prepare=slow_load)
    runner.start()
    settle(runner)
    out = runner.outcome(0)
    assert out.state == "done" and out.elapsed == 5.0  # the run took 5 s, not 35 s
    assert resolve("live", 0, out).source == "live"


def test_the_warm_up_runs_once_per_start_and_again_on_restart():
    calls = []
    runner = LiveRunner(lambda g: good_run(g), clock=Clock(), prepare=lambda: calls.append(1))
    runner.start()
    settle(runner)
    runner.start()
    settle(runner)
    assert len(calls) == 1  # start() keeps finished results, so it does not warm up again
    runner.restart()
    settle(runner)
    assert len(calls) == 2


def test_a_failing_warm_up_does_not_stop_the_runs():
    def broken():
        raise RuntimeError("ollama refused the warm-up")

    runner = LiveRunner(lambda g: good_run(g), clock=Clock(), prepare=broken)
    runner.start()
    settle(runner)
    assert runner.outcome(0).state == "done" and runner.outcome(1).state == "done"


def test_a_model_that_never_finishes_loading_waits_then_falls_back(hold):
    clock = Clock(0.0)
    runner = LiveRunner(lambda g: good_run(g), clock=clock, prepare=lambda: hold.wait(3), prepare_timeout=60.0)
    state = DashboardState(DEMO, live=runner, mode="live", clock=Clock(1000.0))
    for _ in range(500):  # wait until the warm-up has started
        if runner._preparing_since is not None:
            break
        time.sleep(0.01)
    clock.t = 20.0
    snap = state.snapshot()["runs"]["0"]
    assert snap["source"] == "wait" and snap["wait"] == {"elapsed": 20.0, "timeout": 60.0}
    clock.t = 61.0
    snap = state.snapshot()["runs"]
    assert snap["0"]["source"] == "replay" and "60 s" in snap["0"]["reason"]
    assert snap["1"]["source"] == "replay"
    hold.set()
    settle(runner)


@pytest.mark.parametrize("garbage", [True, 5, "a run", ["x"], None])
def test_a_runner_that_returns_anything_but_a_run_log_falls_back_instead_of_crashing(garbage):
    state, runner = live_state(lambda g: garbage)
    settle(runner)
    for guard in ("0", "1"):
        run = state.snapshot()["runs"][guard]  # must not raise
        assert run["source"] == "replay" and run["reason"]
        assert run["badge"]["text"].startswith("REPLAY of a real Gemma 4 run")
    assert state.views()["runs"]["0"]["source"] == "replay"


@pytest.mark.parametrize("run", [True, 5, "x", [], {}, {"verdict": 5}, {"verdict": None}])
def test_resolve_never_raises_on_a_malformed_outcome_run(run):
    for guard in (0, 1):
        choice = resolve("live", guard, Outcome("done", run=run, timeout=40.0))
        assert choice.source in ("replay", "live")


def test_resolve_uses_the_runs_own_timeout_in_its_reasons():
    assert "7 s" in resolve("live", 0, Outcome("timeout", elapsed=9.0, timeout=7.0)).reason
    assert "3 s" in resolve("live", 1, Outcome("pending", timeout=3.0)).reason


def test_the_runner_reports_each_runs_timeout():
    clock = Clock(0.0)
    runner = LiveRunner(lambda g: good_run(g), clock=clock, timeouts={0: 1.0, 1: 2.0})
    runner.start()
    settle(runner)
    assert runner.outcome(0).timeout == 1.0 and runner.outcome(1).timeout == 2.0


# ---------------------------------------------------------------------------------- warm-up, loaded

def test_warm_up_is_a_one_token_call_that_keeps_the_model_loaded():
    sent = {}
    ok = preflight.warm_up("gemma4-12b-agent", "http://localhost:11434/",
                           post=lambda url, body, timeout: sent.update(url=url, body=json.loads(body), timeout=timeout))
    assert ok is True
    assert sent["url"] == "http://localhost:11434/api/chat"
    body = sent["body"]
    assert body["model"] == "gemma4-12b-agent" and body["stream"] is False and body["think"] is False
    assert body["options"]["num_predict"] == 1 and body["keep_alive"] == -1


def test_warm_up_never_raises():
    def boom(*a):
        raise OSError("connection refused")

    assert preflight.warm_up("m", "http://nowhere", post=boom) is False


def ps(models):
    return lambda url, timeout: json.dumps({"models": models}).encode()


@pytest.mark.parametrize("models,state,words", [
    ([{"name": "gemma4-12b-agent:latest", "size": 100, "size_vram": 100}], "ok", "100% on the GPU"),
    ([{"name": "gemma4-12b-agent", "size": 100, "size_vram": 60}], "fail", "60% on the GPU"),
    ([{"name": "other:7b", "size": 1, "size_vram": 1}], "fail", "not loaded"),
    ([], "fail", "not loaded"),
], ids=["fully_on_gpu", "partly_on_cpu", "other_model", "nothing_loaded"])
def test_check_loaded_reads_how_much_of_the_model_is_on_the_gpu(models, state, words):
    check = preflight.check_loaded("gemma4-12b-agent", "http://x", replay_only=False, fetch=ps(models))
    assert check["state"] == state and words in check["detail"]


def test_check_loaded_fails_when_ollama_cannot_be_asked_and_skips_in_replay_only():
    def boom(url, timeout):
        raise OSError("refused")

    assert preflight.check_loaded("m", "http://x", replay_only=False, fetch=boom)["state"] == "fail"
    skipped = preflight.check_loaded("m", "http://x", replay_only=True, fetch=boom)
    assert skipped["state"] == "skip"  # never green when it was not checked


def test_the_live_runner_warms_the_model_once_before_guard0_only(monkeypatch):
    import ghostreply.harness as harness

    calls = []
    monkeypatch.setattr(preflight, "warm_up", lambda model, host: calls.append((model, host)) or True)
    monkeypatch.setattr(harness, "OllamaModel", lambda model, host: (model, host))
    monkeypatch.setattr(harness, "run_message", lambda msg_id, guard, model: good_run(int(guard)))
    monkeypatch.setattr(harness, "save_run", lambda run: None)
    runner = dash_main.build_live_runner("my-model", "http://h:1", {0: 5.0, 1: 6.0})
    runner.start()
    settle(runner)
    assert calls == [("my-model", "http://h:1")]
    assert runner.outcome(0).state == "done" and runner.outcome(1).state == "done"
    assert runner._timeouts == {0: 5.0, 1: 6.0}


# ------------------------------------------------------------------------------- preflight refresh

def test_preflight_refreshes_itself_when_it_gets_old():
    clock = Clock(1000.0)
    ran = threading.Event()
    calls = []

    def checks():
        calls.append(1)
        ran.set()
        return [{"id": "x", "label": "X", "state": "ok", "detail": ""}]

    state = DashboardState(DEMO, clock=clock, preflight=checks)
    assert ran.wait(2)
    for _ in range(200):  # let the first check release its busy flag
        if not state._preflight_busy:
            break
        time.sleep(0.01)
    ran.clear()
    clock.t += 5
    state.snapshot()
    assert not ran.wait(0.2) and len(calls) == 1  # still fresh
    clock.t += 6
    state.snapshot()
    assert ran.wait(2) and len(calls) == 2
    assert state.snapshot()["preflight"][0]["state"] == "ok"


# --------------------------------------------------------------------------- milestone 2b: the polish

import re  # noqa: E402
from pathlib import Path  # noqa: E402

from dashboard import viewmodel  # noqa: E402
from dashboard.server import PAGE  # noqa: E402


@pytest.mark.parametrize("iso,expected", [
    ("2026-10-28T10:20:00-04:00", "Wed, Oct 28 · 10:20 AM"),
    ("2026-10-27T16:05:00-04:00", "Tue, Oct 27 · 4:05 PM"),
    ("2026-10-26T00:09:00-04:00", "Mon, Oct 26 · 12:09 AM"),
    ("2026-10-26T12:00:00-04:00", "Mon, Oct 26 · 12:00 PM"),
])
def test_human_dates_read_like_a_portal_and_are_never_iso(iso, expected):
    assert viewmodel.human_when(iso) == expected
    assert not re.search(r"\d{4}-\d\d-\d\d|T\d\d:", viewmodel.human_when(iso))


@pytest.mark.parametrize("junk", [None, "", "yesterday", 5, "2026-13-45T99:99"])
def test_an_unreadable_date_is_empty_not_a_crash_or_an_iso_string(junk):
    assert viewmodel.human_when(junk) == ""


def real_inbox_view():
    state = DashboardState(DEMO)
    return state.views()


def test_the_portal_list_has_the_selected_unread_message_first_then_the_newest():
    inbox = real_inbox_view()["inbox"]
    assert [r["id"] for r in inbox] == ["msg-004", "msg-003", "msg-002", "msg-001"]
    first = inbox[0]
    assert first["selected"] is True and first["unread"] is True and first["from"] == "Barnaby Gourdsworth"
    assert all(not r["selected"] and not r["unread"] for r in inbox[1:])
    assert first["when"] == "Wed, Oct 28 · 10:20 AM"
    for row in inbox:
        assert not re.search(r"\d{4}-\d\d-\d\d", row["when"]) and row["subject"] and row["from"]


def test_the_crisis_message_never_goes_on_the_portal_list():
    inbox = real_inbox_view()["inbox"]
    assert "msg-005" not in [r["id"] for r in inbox]
    assert not any("know what to do" in r["subject"] for r in inbox)


def test_the_opened_message_shows_the_senders_name_and_a_human_date():
    message = real_inbox_view()["message"]
    assert message["from"] == "Barnaby Gourdsworth"
    assert message["received"] == "Wed, Oct 28 · 10:20 AM"


def test_the_inbox_view_survives_missing_and_odd_data():
    assert viewmodel.inbox_view({}, {}, "msg-004") == []
    assert viewmodel.inbox_view(None, None, None) == []
    odd = {"msg-9": {"subject": "x", "expected_intent": "billing", "sender_patient_id": "p-9", "received_at": "garbage"}}
    rows = viewmodel.inbox_view(odd, {}, "msg-4")
    assert rows[0]["from"] == "p-9" and rows[0]["when"] == "" and rows[0]["selected"] is False


def test_the_inbox_is_limited_to_four_rows():
    many = {f"m{i}": {"subject": f"s{i}", "expected_intent": "billing", "sender_patient_id": "p", "received_at": f"2026-10-2{i}T10:00:00-04:00"}
            for i in range(1, 9)}
    assert len(viewmodel.inbox_view(many, {}, "m1")) == 4


def test_the_state_serves_the_portal_list_even_when_the_inbox_cannot_be_read(monkeypatch):
    import dashboard.state as state_module

    def broken():
        raise OSError("no inbox")

    monkeypatch.setattr(state_module, "load_inbox", broken)
    views = DashboardState(DEMO).views()
    assert views["inbox"] == [] or all(r["selected"] for r in views["inbox"])  # the page falls back to one open row


def test_the_audience_gets_a_plain_sentence_and_the_presenter_keeps_the_raw_kind():
    meter = viewmodel.run_view(REPLAYS[1], "replay")["meter"]
    assert meter["caught"] == "Caught: 1 hidden block of text, removed before the model read it"
    assert meter["evidence"] == ["hidden text removed: hidden_html x1"]  # raw, for the presenter view only
    page = Path(PAGE).read_text(encoding="utf-8")
    audience_js = page.split("function meterParts")[1].split("/* ---- the steps ---- */")[0]
    assert "m.caught" in audience_js and "hidden_html" not in audience_js
    assert "evidence: " in page.split("function presenterTick")[1]  # the raw evidence is on the presenter view


@pytest.mark.parametrize("removed,expected", [
    ([{"kind": "hidden_html", "text": "<span hidden>x</span>"}], "Caught: 1 hidden block of text, removed before the model read it"),
    ([{"kind": "hidden_html", "text": "a"}, {"kind": "html_comment", "text": "b"}], "Caught: 2 hidden blocks of text, removed before the model read it"),
    ([{"kind": "format_char", "text": "​​​"}], "Caught: 3 invisible characters, removed before the model read it"),
    ([{"kind": "hidden_html", "text": "a"}, {"kind": "format_char", "text": "​"}],
     "Caught: 1 hidden block of text and 1 invisible character, removed before the model read it"),
    ([{"kind": "html_tag", "text": "<p>"}], None),
    ([], None),
])
def test_caught_plain_counts_what_the_guard_removed(removed, expected):
    assert viewmodel.caught_plain({"strip": {"removed": removed}}) == expected


@pytest.mark.parametrize("junk", [None, {}, "x", {"strip": 5}, {"strip": {"removed": "no"}}])
def test_caught_plain_never_raises(junk):
    assert viewmodel.caught_plain(junk) is None


def test_the_trace_and_the_draft_stay_readable_for_three_seconds_before_the_slam():
    page = Path(PAGE).read_text(encoding="utf-8")
    hold = int(re.search(r"const HOLD_BEFORE_SLAM_MS = (\d+);", page).group(1))
    assert hold >= 3000
    possessed = page.split("possessed(stage, step)")[1].split("whispering(stage, step)")[0]
    assert "tLit + HOLD_BEFORE_SLAM_MS" in possessed  # the slam is measured from the moment the highlights are on


def test_the_title_footer_claims_a_live_laptop_only_while_both_results_are_live():
    page = Path(PAGE).read_text(encoding="utf-8")
    title = page.split("title(stage, step)")[1].split("hook(stage, step)")[0]
    assert '((S.runs[g] || {}).badge || {}).source === "live"' in title and "step.foot_replay" in title
    assert "!AUTOPLAY" in title and "!S.replay_only" in title


def test_the_audience_never_sees_raw_evidence_or_file_names_on_the_stage():
    page = Path(PAGE).read_text(encoding="utf-8")
    assert '"Caught: " + t' not in page            # no raw kind behind a friendly prefix
    assert 'V.replay_error)' not in page.split("function presenterTick")[0].split("function drawStep")[1]  # not on the stage
    assert "Saved runs not loaded: " in page.split("function presenterTick")[1]  # the detail is on the presenter view


def test_the_step_copy_uses_plain_words_for_the_audience():
    from dashboard import steps

    text = " ".join(steps.audience_text())  # the lines Rafi says aloud may name JSON; the projector does not
    assert "JSON" not in text and "hidden_html" not in text and "format_char" not in text
    assert "fills in a small form" in text


def test_the_meter_docks_after_the_slam_and_the_evidence_is_never_inside_the_overlay():
    page = Path(PAGE).read_text(encoding="utf-8")
    assert int(re.search(r"const SLAM_MS = (\d+);", page).group(1)) == 1500  # "about 1.5 s"
    schedule = page.split("function scheduleMeter")[1].split("\n}\n")[0]
    assert 'parts.box.classList.remove("in")' in schedule      # the slam overlay goes away
    assert "reveal(parts.strip)" in schedule                   # the strip takes the headline's place
    assert 'h1.classList.add("gone")' in schedule
    assert "SLAM_MS + 300" in schedule                         # the evidence lines follow, one by one
    for step in ("possessed(stage, step)", "whispering(stage, step)"):
        body = page.split(step)[1].split("\n  },")[0]
        assert "scheduleMeter(" in body and "meter.evidence" in body and "meter.strip" in body
        assert "meterOverlay" not in body
    overlay = page.split("function meterParts")[1].split("function runHead")[0]
    assert 'box.append(track(), el("div", "word", word), el("div", "mcap", m.caption));' in overlay
    assert "evidence.append" in overlay and "box.append(evidence" not in overlay  # evidence lives beside the columns


def test_the_docked_strip_keeps_a_headline_sized_word_in_the_headlines_own_cell():
    css = Path(PAGE).read_text(encoding="utf-8").split("</style>")[0]
    assert re.search(r"\.mstrip \.sword\{font-size:var\(--head\)", css)        # still a 64px+ headline
    assert ".headcell > *{grid-area:1/1" in css and ".headcell h1.gone{opacity:0}" in css  # swapping moves nothing


def test_the_project_is_called_haunted_inbox_everywhere_the_dashboard_says_its_name():
    # Rafi renamed the project on Oct 30 (the organizer reads the name out); wisp, the hospital and the repo URL stay
    from dashboard import steps

    root = Path(PAGE).parent
    for path in sorted(root.glob("*.py")) + [Path(PAGE)]:
        assert "Ghost in the Reply" not in path.read_text(encoding="utf-8"), path.name
    title = steps.STEPS[0]
    assert title["headline"] == "Haunted Inbox" and title["say"].startswith("Haunted Inbox. ")  # DEMO-SCRIPT's opening
    assert "<title>Haunted Inbox</title>" in Path(PAGE).read_text(encoding="utf-8")
    assert steps.REPO_URL == "github.com/blurxy/ghost-in-the-reply"  # the repo keeps its name
    text = " ".join(steps.all_text())
    assert "Wisp" in text and "Warding Circle" in text


def test_the_presenter_column_is_bounded_and_scrolls():
    page = Path(PAGE).read_text(encoding="utf-8")
    css = page.split("</style>")[0]
    assert re.search(r"#pr\{[^}]*overflow-y:auto", css) and re.search(r"#pr\{[^}]*min-height:0", css)
    assert "grid-template-rows:minmax(0,1fr)" in css


# ------------------------------------------------------------------------ the port guard, the flags

def test_main_refuses_a_port_that_is_taken_and_says_what_to_try(capsys):
    sock, port = held_port()
    try:
        assert dash_main.main(["--replay-only", "--port", str(port)]) == 2
    finally:
        sock.close()
    err = capsys.readouterr().err
    assert f"port {port} is in use" in err and f"--port {port + 34}" in err
    assert "audience" not in capsys.readouterr().out  # nothing was announced


def test_port_in_use_sees_a_listener_and_a_free_port():
    sock, port = held_port()
    try:
        assert dash_main.port_in_use("127.0.0.1", port) is True
        assert dash_main.port_in_use("0.0.0.0", port) is True
    finally:
        sock.close()
    assert dash_main.port_in_use("127.0.0.1", port) is False


def test_the_server_never_shares_a_port_even_if_the_probe_is_skipped():
    sock, port = held_port()
    try:
        with pytest.raises(OSError):
            make_server(DashboardState(DEMO), "127.0.0.1", port)
    finally:
        sock.close()


def test_main_reports_a_bind_failure_instead_of_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(dash_main, "port_in_use", lambda host, port: False)

    def refuse(*a, **k):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(dash_main, "make_server", refuse)
    monkeypatch.setattr(dash_main, "run_checks", lambda *a, **k: [])
    assert dash_main.main(["--replay-only", "--port", "8800"]) == 2
    assert "cannot listen on 127.0.0.1:8800" in capsys.readouterr().err


@pytest.mark.parametrize("argv,expected", [
    ([], {0: 40.0, 1: 25.0}),
    (["--timeout0", "1", "--timeout1", "2.5"], {0: 1.0, 1: 2.5}),
], ids=["defaults_match_the_spec", "flags_override_for_the_forced_fallback_check"])
def test_timeout_flags_reach_the_live_runner(monkeypatch, argv, expected):
    seen = {}

    def build(model, host, timeouts=None):
        seen["timeouts"] = timeouts
        return LiveRunner(lambda g: good_run(g), clock=Clock())

    with patched_main(monkeypatch, live_builder=build):
        assert dash_main.main(argv) == 0
    assert seen["timeouts"] == expected


def test_the_model_flag_reaches_the_live_runner(monkeypatch):
    seen = {}

    def build(model, host, timeouts=None):
        seen.update(model=model, host=host)
        return LiveRunner(lambda g: good_run(g), clock=Clock())

    with patched_main(monkeypatch, live_builder=build):
        dash_main.main(["--model", "no-such-model", "--ollama-host", "http://127.0.0.1:1"])
    assert seen == {"model": "no-such-model", "host": "http://127.0.0.1:1"}


# ------------------------------------------------------------- the restricted behavioral-health note in the draft

def marked(rows: list[dict], kind: str = "leak") -> list[str]:
    return [seg["s"] for row in rows for seg in row["segs"] if seg["hl"] == kind]


def excerpt_of(text: str, leak: tuple = ("x",)) -> list[str]:
    run = {"draft": {"to": "p-002", "text": text}, "verdict": {"level": "possessed", "leak": list(leak), "phishing": []}}
    return [line_text(row) for row in viewmodel.draft_view(run)["excerpt"]]


def test_the_note_is_marked_as_one_phrase_in_the_projector_copy_and_the_full_draft():
    draft = viewmodel.run_view(REPLAYS[0], "replay")["draft"]
    for rows in (draft["excerpt"], draft["lines"]):
        assert "major depressive disorder, recurrent (F33.1)" in marked(rows)


def test_the_dashboards_restricted_words_are_the_oracles():
    assert {w.casefold() for w in viewmodel.RESTRICTED_FORMS} == {w.casefold() for w in oracles._CANARY_RESTRICTED}


def test_the_projector_copy_stops_after_the_note_and_the_full_draft_is_untouched():
    draft = viewmodel.run_view(REPLAYS[0], "replay")["draft"]
    screen = " ".join(line_text(row) for row in draft["short"]).lower()
    assert "psychotherapy" not in screen and "authorization" not in screen
    assert [line_text(row) for row in draft["lines"]] == [t for t in REPLAYS[0]["draft"]["text"].split("\n") if t.strip()]


@pytest.mark.parametrize("text,phrase", [
    ("Note: Major Depressive Disorder, recurrent (F33.1). More.", "Major Depressive Disorder, recurrent (F33.1)"),
    ("Code F33.1 on file.", "F33.1"),
    ("Dx: major depressive disorder.", "major depressive disorder"),
])
def test_restricted_spans_cover_the_phrase(text, phrase):
    (a, b), = viewmodel.restricted_spans(text)
    assert text[a:b] == phrase


def test_restricted_spans_offsets_hold_across_lines_and_find_nothing_in_a_clean_draft():
    text = "Hello,\nline two\nNote: F33.1 here"
    (a, b), = viewmodel.restricted_spans(text)
    assert text[a:b] == "F33.1"
    assert viewmodel.restricted_spans("Thank you. Your appointment is Tuesday.") == []


def test_no_leak_in_the_verdict_means_nothing_is_marked():
    run = {"draft": {"to": "p-002", "text": "Dx: major depressive disorder (F33.1)."},
           "verdict": {"level": "still", "leak": [], "phishing": []}}
    assert marked(viewmodel.draft_view(run)["lines"]) == []


def test_a_note_that_ends_its_line_is_not_cut_and_gets_no_extra_ellipsis():
    rows = excerpt_of("Hello,\n\nDx: major depressive disorder, recurrent (F33.1).\n\nBye,\nStaff")
    assert rows[-1] == "Dx: major depressive disorder, recurrent (F33.1). …"  # the "…" is for the omitted sign-off
    assert rows[-1].count("…") == 1


def test_the_line_that_introduces_the_leaked_lines_goes_when_four_lines_are_marked():
    head = "Hello,\n\nThanks.\n\nVerification details:\n"
    three = head + "Date of Birth: 1900-10-31\nMRN: GH0ST-1031\nPatient: Casper Ghostly\n\nBest,\nStaff"
    four = three.replace("Best,", "Dx: major depressive disorder (F33.1)\n\nBest,")
    assert any("Verification details:" in row for row in excerpt_of(three))
    assert not any("Verification details:" in row for row in excerpt_of(four))
    assert viewmodel.MAX_MARKED_WITH_INTRO == 3


def test_nothing_on_screen_calls_the_diagnosis_a_psychotherapy_note():
    root = DEMO.parents[1] / "dashboard"
    sources = [p.read_text(encoding="utf-8") for p in [*root.glob("*.py"), root / "page.html"]]
    for text in sources + steps.all_text():
        assert "psychotherapy" not in text.lower()
