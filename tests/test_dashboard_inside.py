"""Layer 1 of INSIDE-VIEW-SPEC / CLINICAL-LOOK-SPEC: the view models behind the Security team's view and the patient banner.

The inside view's JSON is the run log's own, byte for byte: what the model was sent (rebuilt exactly as demo/raw/make_requests.py
builds it), what it answered (verbatim, tool_calls and arguments as returned), the records touched, the audit rows (from
run["events"]), and the alert and verdict lines (built from the verdict and the events, never hard-coded). Nothing is invented.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re

import pytest
from test_dashboard import DEMO, REPLAYS, Clock

from dashboard import inside, pool, viewmodel
from dashboard.state import DashboardState
from ghostreply.harness import ROOT, load_patients, load_run
from ghostreply.templates import INTENT_SCHEMA
from ghostreply.tools import NAIVE_TOOLS


def pool_runs() -> list[dict]:
    variants, _, _ = pool.load_pool()
    return [load_run(path) for v in variants for path in pool.run_paths(v["id"]).values()]


ALL_RUNS = [REPLAYS[0], REPLAYS[1], *pool_runs()]


def view(run: dict) -> dict:
    return viewmodel.run_view(run, "replay")["inside"]


# ------------------------------------------------------------------------------------ raw JSON, verbatim

@pytest.mark.parametrize("run", ALL_RUNS, ids=lambda r: r["run_id"])
def test_every_response_is_the_logged_response_verbatim(run):
    shown = view(run)["response"]
    assert len(shown) == len(run["turns"])
    for item, turn in zip(shown, run["turns"]):
        assert json.loads(item["json"]) == turn["response"]  # content and tool_calls with the arguments as returned


@pytest.mark.parametrize("run", ALL_RUNS, ids=lambda r: r["run_id"])
def test_every_request_is_built_from_the_logged_transcript_and_the_harness_constants(run):
    requests = view(run)["request"]
    cuts = [i for i, m in enumerate(run["transcript"]) if m.get("role") == "assistant"]
    cuts += [len(run["transcript"])] * (len(run["turns"]) - len(cuts))  # guard on: the reply is rendered by code, not logged
    assert len(requests) == len(run["turns"]) >= 1
    for item, cut in zip(requests, cuts):
        body = json.loads(item["json"])
        assert body["messages"] == run["transcript"][:cut]
        assert body["model"] == run["model"]["name"] and body["options"]["seed"] == run["model"]["seed"]
        assert body["stream"] is False and body["think"] is False and body["options"]["temperature"] == 0
        if run["guard"]:
            assert body["format"] == INTENT_SCHEMA and "tools" not in body
        else:
            assert body["tools"] == NAIVE_TOOLS and "format" not in body


@pytest.mark.parametrize("run", [r for r in ALL_RUNS if r["guard"]], ids=lambda r: r["run_id"])
def test_with_the_guard_on_no_tool_call_is_shown_because_none_was_made(run):
    inner = view(run)
    assert all("tool_calls" not in json.loads(r["json"]) or not json.loads(r["json"])["tool_calls"] for r in inner["response"])
    assert all("tools" not in json.loads(r["json"]) for r in inner["request"])
    assert "tools: none" in inner["request"][0]["note"]


def test_the_guard_off_request_is_what_the_raw_demo_curls():
    spec = importlib.util.spec_from_file_location("make_requests", ROOT / "demo" / "raw" / "make_requests.py")
    raw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(raw)
    run = REPLAYS[0]
    body = raw.body(run["model"]["name"], [{"role": "system", "content": raw.NAIVE_SYSTEM},
                                           {"role": "user", "content": raw._naive_prompt(run["message"])}], tools=NAIVE_TOOLS)
    assert json.loads(view(run)["request"][0]["json"]) == body  # the bridge to the terminal demo


@pytest.mark.parametrize("run", ALL_RUNS, ids=lambda r: r["run_id"])
def test_the_raw_json_carries_no_control_or_invisible_character(run):
    for item in view(run)["request"] + view(run)["response"]:
        assert item["json"].isascii() and not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", item["json"])


# -------------------------------------------------------------------------- the alert, the audit log, the verdict

def test_the_alert_is_built_from_the_verdict_and_the_events():
    off, on = view(REPLAYS[0])["alert"], view(REPLAYS[1])["alert"]
    assert off["severity"] == "HIGH" and "chart p-1031" in off["text"] and "isn't linked to this message" in off["text"]
    assert "copied its fields into a draft" in off["text"]
    assert on == {"severity": "INFO", "text": "Hidden instruction removed before the model; Wisp touched only the sender's chart."}


def test_the_alert_follows_the_events_it_is_not_hard_coded():
    clean = copy.deepcopy(REPLAYS[0])
    clean["events"] = [e for e in clean["events"] if e["patients_touched"] != ["p-1031"]]
    clean["verdict"] = {"level": "still", "leak": [], "phishing": [], "evidence": []}
    clean["strip"] = {"removed": [], "clean": "x"}
    alert = view(clean)["alert"]
    assert alert["severity"] == "INFO" and "p-1031" not in alert["text"] and "No hidden instruction found" in alert["text"]
    other = copy.deepcopy(REPLAYS[0])
    other["events"][0]["patients_touched"] = ["p-1032"]
    assert "chart p-1032" in view(other)["alert"]["text"] and "p-1031" not in view(other)["alert"]["text"]


def test_the_audit_rows_are_the_events():
    rows = view(REPLAYS[0])["audit"]
    assert len(rows) == len(REPLAYS[0]["events"]) == 2
    assert [r["linked"] for r in rows] == [False, True] and {r["actor"] for r in rows} == {"Wisp"}
    assert rows[0]["action"] == 'lookup_patient("Casper Ghostly")' and rows[0]["record"] == "p-1031"
    assert all(r["result"] == "allowed" and re.fullmatch(r"\d\d:\d\d:\d\d", r["time"]) for r in rows)
    denied = copy.deepcopy(REPLAYS[0])
    denied["events"][0]["allowed"] = False
    assert view(denied)["audit"][0]["result"] == "denied"
    assert all(r["linked"] is True for r in view(REPLAYS[1])["audit"])  # guard on: only the sender's chart and this message


def test_the_records_touched_say_which_belong_to_the_sender():
    assert view(REPLAYS[0])["records"] == [{"id": "p-1031", "sender": False, "kind": "chart"},
                                           {"id": "p-002", "sender": True, "kind": "chart"}]
    assert all(r["sender"] for r in view(REPLAYS[1])["records"])


def test_the_verdict_names_the_harms_that_fired_and_the_mode():
    off, on = view(REPLAYS[0])["verdict"], view(REPLAYS[1])["verdict"]
    assert off["headline"] == "Wisp: Possessed · Guard off" and off["mode"] == "Warding Circle mode: Off"
    assert off["finding"] == "Cross-patient data found in draft"
    rows = dict(off["rows"])
    assert rows["Patient records accessed"] == "p-1031" and "MRN GH0ST-1031" in rows["Fields copied"]
    assert rows["Contact added"] == "(212) 555-0199"
    assert on["headline"] == "Wisp: Whispering · Guard on" and on["mode"] == "Warding Circle mode: On" and on["finding"] == ""
    assert dict(on["rows"]) == {"Patient records accessed": "Sender's chart only", "Fields copied": "None", "Contact added": "None"}


def test_no_compliance_claim_is_made_on_screen():
    text = json.dumps([view(REPLAYS[0])["alert"], view(REPLAYS[1])["alert"], view(REPLAYS[0])["verdict"]]).lower()
    for banned in ("hipaa", "complian", "violation", "audit-control", "breach"):
        assert banned not in text


# --------------------------------------------------------------------------------------- the patient banner

def test_the_banner_reads_the_senders_record_and_shows_only_what_is_on_file():
    banner = inside.patient_banner(load_patients()["p-002"], on="2026-10-28T10:20:00-04:00")
    assert banner["name"] == "Barnaby Gourdsworth" and banner["age"] == 41 and banner["dob"] == "22 Jul 1985"
    assert banner["mrn"] == "HV-100202" and banner["next_appt"] == "04 Nov 2026, 9:00 AM"
    assert banner["sex"] == "Male" and banner["pcp"] == "Dr. Mara Vale" and banner["allergies"] == "Not reviewed"
    bare = inside.patient_banner({"name": "Pat Example", "mrn": "X-1", "dob": "2000-01-01"}, on="2026-10-28T10:20:00-04:00")
    assert bare == {"name": "Pat Example", "age": 26, "dob": "01 Jan 2000", "mrn": "X-1"}  # no sex, pcp or allergies invented
    assert inside.patient_banner(None) is None and inside.patient_banner([]) is None and inside.patient_banner("x") is None


@pytest.mark.parametrize("dob,on,age", [("1985-07-22", "2026-07-21T09:00:00-04:00", 40), ("1985-07-22", "2026-07-22T09:00:00-04:00", 41),
                                        ("2026-10-28", "2026-10-28T09:00:00-04:00", 0)])
def test_the_age_is_counted_on_the_day_the_message_arrived(dob, on, age):
    assert inside.patient_banner({"dob": dob}, on=on)["age"] == age


def test_every_record_in_the_repo_makes_a_banner_from_its_own_fields_only():
    for pid, record in load_patients().items():
        banner = inside.patient_banner(record, on="2026-10-28T10:20:00-04:00")
        assert banner["name"] == record["name"] and banner["mrn"] == record["mrn"], pid
        assert ("sex" in banner) == ("sex" in record) and ("pcp" in banner) == ("pcp" in record), pid


def test_the_routing_and_provenance_lines_come_from_the_message():
    ctx = inside.message_context(REPLAYS[0]["message"])
    assert ctx == {"pool": "Scheduling", "priority": "Routine", "status": "Needs review", "due": "28 Oct, 5:00 PM",
                   "provenance": "Patient portal · Received 28 Oct 2026, 10:20 AM"}
    assert inside.message_context(None)["pool"] == "General" and "provenance" not in inside.message_context({})


# ----------------------------------------------------------------- layer 2: the role switch on the page (structure)

PAGE_TEXT = (ROOT / "dashboard" / "page.html").read_text(encoding="utf-8")


def test_the_role_switch_has_its_button_chip_labels_and_the_audit_view_copy():
    assert 'id="view-btn"' in PAGE_TEXT and 'id="who"' in PAGE_TEXT
    for words in ("View as: Security team", "View as: Front desk", "Security team", "Audit view", "J. Rivera", "Front desk",
                  "Lantern Audit · Wisp activity", "Model request", "Model response", "Records touched", "Verdict", "Copy raw JSON",
                  "Audit log", "HIGH"):
        assert words in PAGE_TEXT, words
    css = PAGE_TEXT.split("</style>")[0]
    assert ".viewbtn{flex:none;min-width:" in css  # the button's width is reserved for both labels: nothing shifts


def test_key_i_is_ignored_while_typing_and_the_switch_keeps_the_step():
    handler = PAGE_TEXT.split('document.addEventListener("keydown"')[1].split("});")[0]
    assert '(k === "i" || k === "I") && !typingNow()' in handler
    typing = PAGE_TEXT.split("function typingNow()")[1].split("\n}\n")[0]
    assert "isContentEditable" in typing and "input|textarea|select" in typing
    toggle = PAGE_TEXT.split("function toggleInside()")[1].split("\n")[0]
    assert 'act("role")' in toggle  # Rafi 3:40 PM: a click or the I key is the server action the presenter uses; the step is untouched
    assert "FINAL || INSTANT || !AUTOPLAY ? 0 : ms" in PAGE_TEXT  # no staged reveals: every animation timer is instant unless filming


def test_the_role_persists_across_steps():
    # INSIDE is page state: it is set only by the declaration and by the switch, never by a step change or a redraw
    # (who is looking now lives on the server: set from the poll's `inside`, and by the static replay's local switch)
    assert "INSIDE = !!next.inside" in PAGE_TEXT and "S.inside = INSIDE = !INSIDE" in PAGE_TEXT and "let INSIDE = false" in PAGE_TEXT
    assert 'if (insideNow()) return securityView(stage, step);' in PAGE_TEXT.split("function drawStep()")[1].split("\n}\n")[0]
    assert 'const SECURITY_STEPS = ["hook", "reveal", "possessed", "whispering"]' in PAGE_TEXT


def test_the_raw_json_viewer_shows_the_text_it_was_given_and_nothing_is_parsed_as_html():
    viewer = PAGE_TEXT.split("function inspector(ins)")[1].split("\n}\n")[0]
    assert 'el("pre", "raw", cur.json)' in viewer and "innerHTML" not in PAGE_TEXT
    assert ".raw{" in PAGE_TEXT and "user-select:text" in PAGE_TEXT and "overflow:auto" in PAGE_TEXT.split(".raw{")[1].split("}")[0]


def test_the_static_site_gets_the_switch_too_with_replay_data_only():
    from dashboard import export_static as ex

    assert "i" in ex.KEYS and "I" in ex.KEYS
    assert 'data-k="i"' in ex.index_html("Haunted Inbox", "x")
    stage = ex.stage_html(PAGE_TEXT, {"n": 1}, {"runs": {}})
    keys = json.loads(re.search(r"var KEYS = (\[.*?\]);", stage).group(1))
    assert "i" in keys and "b" not in keys and "l" not in keys  # the switch yes; no backup video, no live toggle


def test_the_state_hands_the_page_the_banner_and_the_routing_lines():
    state = DashboardState(DEMO, live=None, mode="replay", clock=Clock(1000.0))
    message = state.views()["message"]
    assert message["patient"]["name"] == "Barnaby Gourdsworth" and message["patient"]["mrn"] == "HV-100202"
    assert message["context"]["provenance"].startswith("Patient portal · Received 28 Oct 2026")
