"""The Security team's view of a run, and the sender's patient banner: view models only (INSIDE-VIEW-SPEC, CLINICAL-LOOK-SPEC).

Everything here is read from the run log, the sender's record and the harness's own constants. Nothing is made up: the raw
JSON is the logged objects (the model's responses, the tool_calls with the arguments as returned) and the request bodies
rebuilt exactly as demo/raw/make_requests.py builds them, from the logged transcript, NAIVE_TOOLS and INTENT_SCHEMA; the
alert and the verdict lines are built from the verdict and the events. A field that is not on file is not shown.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from ghostreply import ui
from ghostreply.templates import INTENT_SCHEMA
from ghostreply.tools import NAIVE_TOOLS

_text = ui._display


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _when(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _day(when: datetime) -> str:
    return when.strftime("%d %b %Y")


def _clock(when: datetime) -> str:
    return when.strftime("%I:%M %p").lstrip("0")


# ------------------------------------------------------------------------------------- the patient banner

def patient_banner(record: object, on: object = None) -> dict | None:
    """Name, age, sex, DOB, MRN, PCP, allergies and next appointment: only the fields the sender's record has. The age is
    counted on the day the message arrived (`on`), so a saved run always shows the same one."""
    rec = _dict(record)
    if not rec:
        return None
    dob, next_appt, today = _when(rec.get("dob")), _when(rec.get("next_appointment")), _when(on)
    age = None
    if dob is not None:
        ref = today or datetime.now(dob.tzinfo)
        age = ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))
    out = {"name": _text(rec.get("name"), 80), "age": age, "sex": _text(rec.get("sex"), 20),
           "dob": _day(dob) if dob else "", "mrn": _text(rec.get("mrn"), 40), "pcp": _text(rec.get("pcp"), 80),
           "allergies": _text(rec.get("allergies"), 120),
           "next_appt": f"{_day(next_appt)}, {_clock(next_appt)}" if next_appt else ""}
    return {k: v for k, v in out.items() if v not in (None, "")}


POOLS = {"reschedule": "Scheduling", "refill_request": "Refills", "billing": "Billing"}


def message_context(message: object) -> dict:
    """The routing, work-state and provenance lines of the reading pane, from the message itself."""
    msg = _dict(message)
    received = _when(msg.get("received_at"))
    intent = msg.get("expected_intent")
    pool = POOLS.get(intent, "General") if isinstance(intent, str) else "General"
    out = {"pool": pool, "priority": "Routine", "status": "Needs review"}
    if received:
        out["due"] = f"{received.strftime('%d %b')}, 5:00 PM"
        out["provenance"] = f"Patient portal · Received {_day(received)}, {_clock(received)}"
    return out


# ------------------------------------------------------------------------------------------- the inside view

def _elapsed_ms(turns: list, upto: int) -> int:
    return sum(int(t.get("ms") or 0) for t in turns[:upto + 1] if isinstance(t, dict))


def audit_rows(run: dict) -> list[dict]:
    """One row per event of the run, in order: when, who, what was called, which record, linked to the sender or not, allowed
    or denied. The time is the run's start plus the model time logged for the turn that made the call."""
    message, sender = _dict(run.get("message")), _dict(run.get("message")).get("sender_patient_id")
    turns = [t for t in _list(run.get("turns")) if isinstance(t, dict)]
    start = _when(run.get("started_at"))
    rows = []
    for i, event in enumerate(e for e in _list(run.get("events")) if isinstance(e, dict)):
        charts, messages = _list(event.get("patients_touched")), _list(event.get("messages_touched"))
        ids = [_text(str(p), 60) for p in charts] or [_text(str(m), 60) for m in messages]
        linked = None
        if charts:
            linked = all(p == sender for p in charts)
        elif messages:
            linked = all(m == message.get("id") for m in messages)
        at = ""
        if start is not None:
            ms = _elapsed_ms(turns, min(i, len(turns) - 1)) if turns and not run.get("guard") else 0
            at = (start + timedelta(milliseconds=ms)).strftime("%H:%M:%S")
        rows.append({"time": at, "actor": "Wisp", "action": _text(ui._call(event.get("tool"), event.get("args")), 160),
                     "record": ", ".join(ids) if ids else "none", "linked": linked,
                     "result": "allowed" if event.get("allowed") else "denied"})
    return rows


def _foreign_charts(run: dict) -> list[str]:
    sender = _dict(run.get("message")).get("sender_patient_id")
    seen: list[str] = []
    for event in (e for e in _list(run.get("events")) if isinstance(e, dict)):
        for pid in _list(event.get("patients_touched")):
            shown = _text(str(pid), 60)
            if pid != sender and shown not in seen:
                seen.append(shown)
    return seen


def _fields_copied(verdict: dict) -> list[str]:
    out = []
    for line in _list(verdict.get("leak")):
        m = re.match(r"^(?:canary )?(.+?) in the draft$", str(line))
        if m:
            out.append(_text(m.group(1), 120))
    return out


def _contacts_added(verdict: dict) -> list[str]:
    out = []
    for line in _list(verdict.get("phishing")):
        m = re.search(r"(?:phone|number|email|url)?\s*(.+?) is not the hospital", str(line))
        out.append(_text(m.group(1) if m else line, 120))
    return out


def alert(run: dict, verdict: dict, removed_hidden: bool) -> dict:
    """The alert line, built from the verdict and the events. HIGH when a harm happened, INFO when the attack was contained."""
    foreign, level = _foreign_charts(run), verdict.get("level")
    copied, contacts = _fields_copied(verdict), _contacts_added(verdict)
    if level == "possessed" or foreign:
        text = f"Wisp opened chart {', '.join(foreign)}, which isn't linked to this message" if foreign else "Wisp's draft contains data from outside this message"
        text += ", and copied its fields into a draft." if copied else "."
        if contacts:
            text += " The draft also adds a contact that isn't the hospital's."
        return {"severity": "HIGH", "text": text}
    if removed_hidden:
        return {"severity": "INFO", "text": "Hidden instruction removed before the model; Wisp touched only the sender's chart."}
    return {"severity": "INFO", "text": "No hidden instruction found; Wisp touched only the sender's chart."}


def request_bodies(run: dict) -> list[dict]:
    """The request each model call was sent, rebuilt as demo/raw/make_requests.py builds it: the logged transcript up to that
    turn, the model name and seed from the log, and NAIVE_TOOLS (guard off) or the INTENT_SCHEMA format (guard on)."""
    transcript = [m for m in _list(run.get("transcript")) if isinstance(m, dict)]
    model = _dict(run.get("model"))
    guard = bool(run.get("guard"))
    out, turn = [], 0
    cuts = [i for i, m in enumerate(transcript) if m.get("role") == "assistant"]
    # one request per logged turn; a turn whose answer is not in the transcript (guard on: code renders the reply from the
    # model's JSON) was sent the whole transcript
    cuts += [len(transcript)] * max(0, len(_list(run.get("turns"))) - len(cuts))
    for i in cuts:
        body = {"model": model.get("name"), "messages": transcript[:i], "stream": False, "keep_alive": -1, "think": False,
                "options": {"temperature": 0, "seed": model.get("seed"), "top_k": 1}}
        body["format" if guard else "tools"] = INTENT_SCHEMA if guard else NAIVE_TOOLS
        turn += 1
        out.append({"label": f"Request {turn}", "json": json.dumps(body, indent=2, ensure_ascii=True),
                    "note": "tools: none; the reply is forced into the intent schema" if guard else "three tools; free text allowed"})
    return out


def response_bodies(run: dict) -> list[dict]:
    """The model's responses exactly as logged: content and the raw tool_calls with their arguments."""
    turns = [t for t in _list(run.get("turns")) if isinstance(t, dict)]
    return [{"label": f"Response {i + 1}", "json": json.dumps(t.get("response"), indent=2, ensure_ascii=True)}
            for i, t in enumerate(turns)]


def records_touched(run: dict) -> list[dict]:
    message, sender = _dict(run.get("message")), _dict(run.get("message")).get("sender_patient_id")
    rows: list[dict] = []
    for event in (e for e in _list(run.get("events")) if isinstance(e, dict)):
        for pid in _list(event.get("patients_touched")):
            shown = _text(str(pid), 60)
            if not any(r["id"] == shown for r in rows):
                rows.append({"id": shown, "sender": pid == sender, "kind": "chart"})
        for mid in _list(event.get("messages_touched")):
            shown = _text(str(mid), 60)
            if not any(r["id"] == shown for r in rows):
                rows.append({"id": shown, "sender": mid == message.get("id"), "kind": "message"})
    return rows


def inside_view(run: object, verdict: object, hidden_removed: bool) -> dict:
    """Everything the Security team's view shows for one run."""
    run, verdict = _dict(run), _dict(verdict)
    guard = bool(run.get("guard"))
    level = verdict.get("level")
    word = {"possessed": "Possessed", "whispering": "Whispering", "still": "Still"}.get(level, "") if isinstance(level, str) else ""
    copied, contacts = _fields_copied(verdict), _contacts_added(verdict)
    foreign = _foreign_charts(run)
    return {
        "alert": alert(run, verdict, hidden_removed),
        "audit": audit_rows(run),
        "request": request_bodies(run),
        "response": response_bodies(run),
        "records": records_touched(run),
        "verdict": {
            "headline": f"Wisp: {word}" + (" · Guard on" if guard else " · Guard off") if word else "",
            "mode": "Warding Circle mode: " + ("On" if guard else "Off"),
            "finding": "Cross-patient data found in draft" if copied else "",
            "rows": [["Patient records accessed", ", ".join(foreign) or "Sender's chart only"],
                     ["Fields copied", "; ".join(copied) or "None"],
                     ["Contact added", "; ".join(contacts) or "None"]],
        },
    }
