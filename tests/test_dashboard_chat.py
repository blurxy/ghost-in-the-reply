"""/chat: Barnaby chats with Wisp (planning/PATIENT-CHAT-SPEC.md). No Ollama: the lead's real presets and saved real runs, with a
scripted run_chat standing in for the model.

Covers the lines Rafi set: preset NAMES only, the 2,000-character cap before the model, one run at a time, replay fallback only
for the two presets (a typed message says "timed out"), a badge on every Wisp reply that never calls a replay live, and every
user string reaching the page through textContent.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

import pytest

from dashboard import chatportal as cp
from dashboard.chatportal import Backend, ChatSession
from dashboard.server import CHAT_PAGE, make_server
from dashboard.state import DashboardState
from test_dashboard import DEMO

from ghostreply.harness import load_run

REAL = cp.resolve_backend()
PAGE_TEXT = CHAT_PAGE.read_text(encoding="utf-8")


def saved(preset: str, guard: int) -> dict:
    return load_run(REAL.preset_paths(preset)[guard])


def scripted(delay: float = 0.0, fail: bool = False, calls: list | None = None, gate: threading.Event | None = None) -> Backend:
    """The real presets and paths, with run_chat answering from the saved real runs (what a validated live run looks like)."""
    def run_chat(guard, model, *, preset=None, text=None, deadline_s=None):
        if calls is not None:
            calls.append({"guard": guard, "preset": preset, "text": text})
        if gate is not None:
            gate.wait(5)
        time.sleep(delay)
        if fail:
            raise OSError("ollama is down")
        return saved(preset or "visible", int(guard)) if preset or text is None else saved("visible", int(guard))
    return Backend(run_chat, presets=REAL.presets, cap=REAL.cap, preset_paths=REAL.preset_paths, refusals=REAL.refusals)


def session(backend=None, **kw) -> ChatSession:
    kw.setdefault("save", False)
    kw.setdefault("timeouts", {0: 5, 1: 5})
    return ChatSession(kw.pop("model_factory", lambda: object()), backend=backend or scripted(), **kw)


def wait_done(chat: ChatSession, seconds: float = 5.0) -> dict:
    end = time.time() + seconds
    while time.time() < end:
        snap = chat.snapshot()
        if snap["turn"] and snap["turn"]["state"] != "typing" and not snap["busy"]:
            return snap
        time.sleep(0.02)
    raise AssertionError("the chat turn never finished")


def test_the_real_backend_is_found_and_has_both_presets():
    assert REAL is not None and set(REAL.presets) == {"visible", "hidden"} and REAL.cap == 2000


def test_a_preset_runs_live_and_the_reply_is_wisps_draft_with_a_live_badge():
    chat = session()
    code, _ = chat.send({"preset": "visible"})
    assert code == 202
    snap = wait_done(chat)
    turn = snap["turn"]
    assert turn["state"] == "done" and turn["badge"]["source"] == "live" and turn["badge"]["text"].startswith("LIVE")
    assert "\n".join(turn["reply"]).strip() == saved("visible", 0)["draft"]["text"].strip()
    assert turn["patient"]["text"] == REAL.presets["visible"]["text"]


def test_typing_is_shown_while_the_run_goes_and_one_run_at_a_time():
    gate = threading.Event()
    chat = session(scripted(gate=gate))
    assert chat.send({"preset": "visible"})[0] == 202
    snap = chat.snapshot()
    assert snap["busy"] and snap["turn"]["state"] == "typing" and snap["turn"]["reply"] is None
    code, body = chat.send({"preset": "hidden"})  # a second click while Wisp is answering
    assert code == 409 and body["error"] == cp.BUSY
    assert chat.act("reset")[0] == 409  # nor can the thread be wiped under a run
    gate.set()
    wait_done(chat)
    assert chat.send({"preset": "hidden"})[0] == 202
    wait_done(chat)


def test_the_pasted_preset_shows_only_what_a_person_sees_and_the_model_gets_the_preset():
    calls: list = []
    chat = session(scripted(calls=calls))
    chat.send({"preset": "hidden"})
    snap = wait_done(chat)
    shown = snap["turn"]["patient"]
    assert shown["pasted"] is True and shown["text"].strip()
    assert "display:none" not in shown["text"] and "<" not in shown["text"]
    assert calls == [{"guard": False, "preset": "hidden", "text": None}]  # the backend resolves the raw HTML itself, by name


def test_the_guard_applies_to_the_next_send_and_the_toggle_is_server_side():
    calls: list = []
    chat = session(scripted(calls=calls))
    assert chat.snapshot()["guard"] == 0
    assert chat.act("guard")[1]["guard"] == 1
    chat.send({"preset": "visible"})
    wait_done(chat)
    assert calls[-1]["guard"] is True
    turn = chat.snapshot()["turn"]
    assert turn["guard"] == 1 and turn["badge"]["source"] == "live"
    assert chat.act("guard")[1]["guard"] == 0


def test_who_is_looking_is_server_side_and_the_security_view_comes_from_the_run_log():
    chat = session()
    chat.send({"preset": "visible"})
    wait_done(chat)
    assert "security" not in chat.snapshot()
    assert chat.act("view")[1]["role"] == "security"
    snap = chat.snapshot()
    sec = snap["security"]
    assert sec["inside"]["alert"]["severity"] == "HIGH" and "p-1031" in sec["inside"]["alert"]["text"]
    assert sec["inside"]["audit"] and sec["inside"]["request"] and sec["inside"]["response"]
    assert sec["banner"]["name"] == "Barnaby Gourdsworth" and sec["banner"]["mrn"] == "HV-100202"
    assert sec["inside"]["verdict"]["headline"] == "Wisp: Possessed · Guard off"
    assert json.loads(sec["inside"]["response"][0]["json"]) == saved("visible", 0)["turns"][0]["response"]  # raw, unedited
    assert chat.snapshot(full=False).get("security") is None and chat.snapshot(full=False)["turn"] is None
    assert chat.act("view")[1]["role"] == "patient"


def test_unknown_actions_are_refused_and_change_nothing():
    chat = session()
    for bad in ("rm -rf", "", None, 7, ["view"], "VIEW", "view; ls"):
        code, body = chat.act(bad)
        assert code == 400 and body["allowed"] == list(cp.ACTIONS)
    snap = chat.snapshot()
    assert snap["role"] == "patient" and snap["guard"] == 0 and snap["version"] == 1


@pytest.mark.parametrize("body", [
    {}, {"preset": "visible", "text": "hi"}, {"preset": "nope"}, {"preset": 3}, {"preset": ["visible"]}, {"text": ""}, {"text": "   "},
    {"text": 5}, "visible", ["visible"], None, {"text": "x" * 2001},
])
def test_bad_sends_are_refused_before_the_model(body):
    calls: list = []
    chat = session(scripted(calls=calls))
    code, payload = chat.send(body)
    assert code == 400 and payload["error"] and calls == [] and not chat.busy
    assert chat.snapshot()["turn"] is None


def test_the_cap_is_2000_characters_and_the_limit_is_in_the_message():
    chat = session()
    code, body = chat.send({"text": "y" * 2001})
    assert code == 400 and "2001" in body["error"] and "2000" in body["error"]


def test_a_typed_message_that_is_exactly_a_preset_is_that_preset_and_may_replay():
    calls: list = []
    chat = session(scripted(calls=calls))
    chat.send({"text": REAL.presets["visible"]["text"]})
    wait_done(chat)
    assert calls[0]["preset"] == "visible" and calls[0]["text"] is None
    edited = REAL.presets["visible"]["text"] + " Thanks."
    chat.send({"text": edited})
    wait_done(chat)
    assert calls[1]["preset"] is None and calls[1]["text"] == edited  # edited: free text, no fallback


def test_a_preset_that_times_out_falls_back_to_its_saved_real_run_and_says_replay():
    chat = session(scripted(delay=0.6), timeouts={0: 0.1, 1: 0.1}, grace=0.05)
    chat.send({"preset": "hidden"})
    snap = wait_done(chat, seconds=8)
    turn = snap["turn"]
    assert turn["state"] == "done" and turn["badge"]["source"] == "replay" and "LIVE" not in turn["badge"]["text"]
    assert turn["badge"]["text"].startswith("REPLAY") and "longer than" in turn["note"]
    assert "\n".join(turn["reply"]).strip() == saved("hidden", 0)["draft"]["text"].strip()


def test_a_timed_out_run_keeps_the_lock_until_it_really_ends():
    gate = threading.Event()
    chat = session(scripted(gate=gate), timeouts={0: 0.1, 1: 0.1}, grace=0.05)
    chat.send({"preset": "visible"})
    end = time.time() + 5
    while time.time() < end and chat.snapshot()["turn"]["state"] == "typing":
        time.sleep(0.02)
    assert chat.snapshot()["turn"]["badge"]["source"] == "replay" and chat.busy  # shown, but the model thread is still going
    assert chat.send({"preset": "hidden"})[0] == 409
    gate.set()
    end = time.time() + 5
    while time.time() < end and chat.busy:
        time.sleep(0.02)
    assert not chat.busy and chat.send({"preset": "hidden"})[0] == 202
    wait_done(chat)


def test_a_typed_message_has_no_replay_it_says_timed_out():
    chat = session(scripted(delay=0.6), timeouts={0: 0.1, 1: 0.1}, grace=0.05)
    chat.send({"text": "Can I move my appointment to Tuesday?"})
    snap = wait_done(chat, seconds=8)
    turn = snap["turn"]
    assert turn["state"] == "timed out" and turn["reply"] is None
    assert turn["badge"] == {"source": "none", "text": "TIMED OUT"} and cp.NO_REPLAY in turn["note"]


def test_a_model_failure_replays_a_preset_and_never_a_typed_message():
    chat = session(scripted(fail=True))
    chat.send({"preset": "visible"})
    turn = wait_done(chat)["turn"]
    assert turn["badge"]["source"] == "replay" and "OSError" in turn["note"]
    chat.send({"text": "Hello Wisp"})
    turn = wait_done(chat)["turn"]
    assert turn["state"] == "error" and turn["reply"] is None and turn["badge"]["source"] == "none"


def test_replay_only_replays_the_presets_and_refuses_typing():
    chat = ChatSession(None, backend=scripted(), save=False)
    assert chat.snapshot()["can_type"] is False
    code, body = chat.send({"text": "hello"})
    assert code == 503 and body["error"] == cp.NEEDS_MODEL
    chat.send({"preset": "visible"})
    turn = wait_done(chat)["turn"]
    assert turn["badge"]["source"] == "replay" and "replay-only" in turn["note"]


def test_a_live_run_that_scores_otherwise_warns_the_presenter_but_is_still_shown_live():
    odd = saved("visible", 1)  # a guard-on run, answered to a guard-off request
    backend = Backend(lambda guard, model, **kw: odd, presets=REAL.presets, cap=REAL.cap, preset_paths=REAL.preset_paths)
    chat = session(backend)
    chat.send({"preset": "visible"})
    turn = wait_done(chat)["turn"]
    assert turn["badge"]["source"] == "live" and "expected possessed" in turn["warn"]


def test_reset_clears_the_thread_and_the_switches():
    chat = session()
    chat.act("guard"); chat.act("view")
    chat.send({"preset": "visible"})
    wait_done(chat)
    code, snap = chat.act("reset")
    assert code == 200 and snap["role"] == "patient" and snap["guard"] == 0 and snap["turn_state"] == "idle"


def test_a_reset_during_a_late_answer_does_not_bring_the_old_exchange_back():
    chat = session(scripted(delay=0.3))
    chat.send({"preset": "visible"})
    chat.turn = None  # as if reset had run while the model was still answering
    time.sleep(0.6)
    assert chat.snapshot()["turn"] is None


class NoBackend(ChatSession):
    backend = property(lambda self: None)


def test_no_backend_is_a_503_and_the_page_says_why():
    chat = NoBackend(None)
    assert chat.send({"preset": "visible"})[0] == 503
    snap = chat.snapshot()
    assert snap["available"] is False and snap["reason"] == cp.NO_BACKEND


# ------------------------------------------------------------------------------------------------------------- HTTP

@pytest.fixture()
def served():
    state = DashboardState(DEMO, mode="replay")
    state.chat = session()
    server = make_server(state, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def call(base: str, path: str, body=None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_chat_page_state_send_and_act(served):
    state, base = served
    code, page = call(base, "/chat")
    assert code == 200 and b"Hollowveil Lantern" in page
    code, body = call(base, "/api/chat/send", {"preset": "visible"})
    assert code == 202 and json.loads(body)["busy"] is True
    wait_done(state.chat)
    code, body = call(base, "/api/chat/state")
    assert code == 200 and json.loads(body)["turn"]["state"] == "done"
    code, body = call(base, "/api/chat/state?full=0")
    assert json.loads(body)["turn"] is None
    code, body = call(base, "/api/chat/act", {"action": "guard"})
    assert code == 200 and json.loads(body)["guard"] == 1
    assert call(base, "/api/chat/act", {"action": "ls"})[0] == 400


def test_http_refuses_bad_bodies_and_an_oversized_one_never_reaches_the_model(served):
    state, base = served
    assert call(base, "/api/chat/send", raw=b"not json")[0] == 400
    assert call(base, "/api/chat/send", raw=b"")[0] == 400
    assert call(base, "/api/chat/send", raw=b"[1]")[0] == 400
    assert call(base, "/api/chat/send", {"text": "z" * 2001})[0] == 400
    assert call(base, "/api/chat/send", raw=json.dumps({"text": "q" * 70000}).encode())[0] == 400
    assert state.chat.snapshot()["turn"] is None


def test_http_without_a_chat_session_answers_cleanly():
    state = DashboardState(DEMO, mode="replay")
    server = make_server(state, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert call(base, "/api/chat/send", {"preset": "visible"})[0] == 503
        code, body = call(base, "/api/chat/state")
        assert code == 200 and json.loads(body)["available"] is False
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------------------------------------------------------- the page

def test_the_page_draws_every_string_through_textcontent():
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert banned not in PAGE_TEXT, banned


def test_the_page_has_no_form_ids_that_shadow_the_dom():
    ids = set(re.findall(r'\bid="([^"]+)"', PAGE_TEXT)) | set(re.findall(r'\.id = "([^"]+)"', PAGE_TEXT))
    assert not ids & {"hidden", "submit", "name", "action", "method", "length", "elements", "reset"}


def test_the_page_has_the_spec_copy_and_the_keys():
    for text in ("Hollowveil Lantern", "Patient portal", "Chat with Wisp · Hollowveil's virtual assistant", "Wisp is typing…",
                 "Warding Circle mode:", "Simulation · synthetic patients · nothing leaves this laptop", "timed out",
                 "Suggested messages", "Lantern Audit · Wisp activity", "Turn on (G)", "View as: Security team (I)"):
        assert text in PAGE_TEXT, text
    assert '"i" || e.key === "I"' in PAGE_TEXT and '"g" || e.key === "G"' in PAGE_TEXT
    assert "badge " in PAGE_TEXT and "t.badge" in PAGE_TEXT


def test_the_page_follows_the_wording_rules():
    low = PAGE_TEXT.lower()
    for banned in ("unbreakable", "no gaps", "hipaa violation", "psychotherapy note", "epic's"):
        assert banned not in low, banned
