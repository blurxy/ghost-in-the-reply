"""The audience screen is a real app, not slides (planning/INTERACTIVE-SPEC.md, Rafi 3:40 PM): no timers, and every click on the
audience screen is the same server action the presenter's buttons use, so both screens stay in sync."""

from __future__ import annotations

import json
import re
import threading
import urllib.request

from test_dashboard import DEMO, Clock

from dashboard.server import PAGE, make_server
from dashboard.state import ACTIONS, DashboardState

PAGE_TEXT = PAGE.read_text(encoding="utf-8")


def fresh(**kw) -> tuple[DashboardState, Clock]:
    clock = Clock(1000.0)
    return DashboardState(DEMO, live=None, mode="replay", clock=clock, **kw), clock


def test_role_and_view_mode_are_server_actions():
    assert "role" in ACTIONS and "view_mode" in ACTIONS
    state, _ = fresh()
    assert state.snapshot()["inside"] is False and state.snapshot()["wisp_view"] is False


def test_the_role_switch_round_trips_and_keeps_the_step():
    state, _ = fresh()
    state.act("goto", 3)
    epoch = state.snapshot()["epoch"]
    snap = state.act("role")  # no argument: toggle
    assert snap["inside"] is True and snap["step"] == 3 and snap["epoch"] > epoch
    assert state.act("role")["inside"] is False
    assert state.act("role", "security")["inside"] is True
    assert state.act("role", "security")["inside"] is True  # naming it is not a toggle
    assert state.act("role", "front")["inside"] is False
    assert state.act("role", "nonsense")["inside"] is False


def test_the_staff_view_and_what_wisp_reads_toggle_back_and_forth():
    state, _ = fresh()
    state.act("goto", 2)  # the reveal
    assert state.snapshot()["wisp_view"] is False
    assert state.act("view_mode", "wisp")["wisp_view"] is True
    assert state.act("view_mode", "staff")["wisp_view"] is False
    assert state.act("view_mode", "wisp")["wisp_view"] is True
    assert state.act("view_mode")["wisp_view"] is False  # no argument: the other one
    state.act("view_mode", "wisp")
    assert state.act("next")["wisp_view"] is False  # a new step opens on the staff view


def test_nothing_advances_on_its_own():
    state, clock = fresh()
    state.act("goto", 2)
    for minutes in (1, 10, 90):
        clock.t += minutes * 60  # the clock moves; the screen does not
        snap = state.snapshot()
        assert snap["step"] == 2 and snap["practice"] is None


def test_http_role_and_view_mode_through_the_one_action_endpoint():
    state, _ = fresh()
    server = make_server(state, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        def post(body):
            req = urllib.request.Request(base + "/api/action", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        assert post({"action": "role", "arg": "security"})["inside"] is True
        assert post({"action": "view_mode", "arg": "wisp"})["wisp_view"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_the_page_has_no_timers_and_no_slow_transitions():
    assert "FINAL || INSTANT || !AUTOPLAY ? 0 : ms" in PAGE_TEXT  # staged reveals only when filming (?autoplay=1)
    assert 'id="t-step"' not in PAGE_TEXT and 'id="t-demo"' not in PAGE_TEXT  # the presenter's step and demo timers are gone
    for decl in re.findall(r"transition:([^;}]*)", PAGE_TEXT):
        for secs in re.findall(r"([\d.]+)s", decl):
            assert float(secs) < 0.3, decl  # every transition under 300 ms
    for decl in re.findall(r"animation:([^;}]*)", PAGE_TEXT):
        if "infinite" not in decl:
            assert all(float(s) < 0.3 for s in re.findall(r"([\d.]+)s", decl)), decl


def test_the_toggle_and_the_role_switch_post_server_actions():
    assert 'act("view_mode", "staff")' in PAGE_TEXT and 'act("view_mode", "wisp")' in PAGE_TEXT
    assert 'function toggleInside() { act("role"); }' in PAGE_TEXT
    assert '$("#view-btn").addEventListener("click", toggleInside)' in PAGE_TEXT
