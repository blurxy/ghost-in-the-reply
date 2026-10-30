"""Hospital reply templates for Warding Circle mode (GUARD=1).

Under GUARD=1 the model only classifies the message; this module writes every word the patient would read.
The model's JSON is checked in code, field by field, because the decoder's schema is not a security boundary:
a compromised or replaced model can return anything. Anything outside the contract holds the draft for a human.

Templates never claim that an action happened or that a clinician reviewed anything, and never format a
model-written string into the reply. The only model-derived value that reaches a draft is a date-time, and only
after it has parsed as ISO-8601 and fallen inside the allowed window; it is re-printed from the parsed value.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

INTENTS = ("reschedule", "refill_request", "billing", "other")
FIELDS = frozenset({"intent", "proposed_time", "needs_clinician"})
WINDOW = timedelta(days=90)
MAX_TIME_CHARS = 40

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "proposed_time": {"type": ["string", "null"]},
        "needs_clinician": {"type": "boolean"},
    },
    "required": ["intent", "proposed_time", "needs_clinician"],
    "additionalProperties": False,
}

# Held reasons are fixed strings. Model-written content never goes into them; it goes to the run log's event detail.
HELD_NOT_JSON = "the reply was not a JSON object"
HELD_FIELDS = "the reply did not have exactly the expected fields"
HELD_INTENT = "intent is not one of the allowed intents"
HELD_CLINICIAN = "needs_clinician is not true or false"
HELD_TIME = "proposed_time is not an ISO-8601 date-time"
HELD_WINDOW = "proposed_time is outside the allowed window"

_OPENERS = {
    "reschedule": "Thank you for your message about changing your appointment.",
    "refill_request": "Thank you for your message about a prescription refill.",
    "billing": "Thank you for your message about your bill.",
    "other": "Thank you for your message.",
}
_NEXT_STEPS = {
    "reschedule": "A member of our scheduling team will contact you to find a time that works.",
    "refill_request": "Our care team will look at your request and contact you. If you will run out of a medicine "
                      "you need before you hear from us, please call us.",
    "billing": "Our billing office will contact you to go over your statement.",
    "other": "A member of our team will read your message and get back to you.",
}
_CLINICIAN = "Your message will go to your care team so a clinician can answer it."
_SIGN_OFF = "Hollowveil Memorial Hospital, Patient Services"


def parse_intent(raw: str, received_at: datetime) -> tuple[dict | None, str | None]:
    """Validate the model's JSON. Returns (intent, None) when it is usable, or (None, held_reason) when it is not."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None, HELD_NOT_JSON
    if not isinstance(data, dict):
        return None, HELD_NOT_JSON
    if set(data) != FIELDS:
        return None, HELD_FIELDS
    intent = data["intent"]
    if not isinstance(intent, str) or intent not in INTENTS:
        return None, HELD_INTENT
    needs_clinician = data["needs_clinician"]
    if type(needs_clinician) is not bool:  # the string "false" is truthy; 0 and 1 are not answers either
        return None, HELD_CLINICIAN
    when = None
    proposed = data["proposed_time"]
    if proposed is not None:
        if not isinstance(proposed, str) or len(proposed) > MAX_TIME_CHARS:
            return None, HELD_TIME
        try:
            when = datetime.fromisoformat(proposed.strip())
        except ValueError:
            return None, HELD_TIME
        if when.tzinfo is None:
            when = when.replace(tzinfo=received_at.tzinfo)
        if not received_at <= when <= received_at + WINDOW:
            return None, HELD_WINDOW
    return {"intent": intent, "proposed_time": when.isoformat() if when else None,
            "needs_clinician": needs_clinician}, None


def _when(iso: str) -> str:
    when = datetime.fromisoformat(iso)
    day = f"{when:%A, %B} {when.day}"
    if when.hour == 0 and when.minute == 0:
        return day
    return f"{day} at {when.hour % 12 or 12}:{when:%M} {'AM' if when.hour < 12 else 'PM'}"


def render_reply(intent: dict, sender: dict, hospital: dict) -> str:
    """Write the reply from fixed text, the sender's own chart and the hospital's own contacts."""
    first_name = (str(sender.get("name") or "").split() or ["there"])[0]
    key = intent["intent"]
    lines = [f"Hello {first_name},", "", _OPENERS[key]]
    if key == "reschedule" and intent.get("proposed_time"):
        lines.append(f"We have noted that you asked about {_when(intent['proposed_time'])}.")
    lines.append(_NEXT_STEPS[key])
    if intent.get("needs_clinician") is True:
        lines.append(_CLINICIAN)
    lines += ["", f"If you need us sooner, call {hospital['phone']} or sign in to the patient portal at "
                  f"{hospital['portal_url']}.", "", _SIGN_OFF]
    return "\n".join(lines)
