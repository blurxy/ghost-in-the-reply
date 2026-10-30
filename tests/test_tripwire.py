"""Block 9: the crisis tripwire. Crisis language means URGENT for a human and no draft. No Ollama, ever."""

import copy

import pytest

from ghostreply import harness, tools
from ghostreply.strip import strip
from tests.test_guard import FakeModel, HOSPITAL, INBOX, PATIENTS, reply_json, tool_turn

POSITIVES = [
    "I keep thinking about suicide",
    "I want to die",
    "I don't want to be here anymore",
    "I don’t want to be here anymore",
    "I might kill myself",
    "thinking about ending my life",
    "I have been hurting myself",
    "self-harm",
    "I took an overdose",
    "I have chest pain",
    "I can't breathe",
    "I'm having trouble breathing",
    "I do not feel safe right now",
    "I WANT TO DIE",
    "I DON'T WANT TO BE HERE ANYMORE",
    "I   want \t to\n\n die",
    "I  do   not  feel   safe   right now",
]


@pytest.mark.parametrize("text", POSITIVES)
def test_crisis_signal_positive(text):
    assert tools.crisis_signal(text) is not None


NEGATIVES = [
    "I'm dying to know my results",
    "this bill is killing me",
    "Could we move my appointment?",
]


@pytest.mark.parametrize("text", NEGATIVES)
def test_crisis_signal_negative(text):
    assert tools.crisis_signal(text) is None


@pytest.mark.parametrize("msg_id", ["msg-001", "msg-002", "msg-003"])
def test_crisis_signal_ignores_visible_text_of_normal_messages(msg_id):
    message = INBOX[msg_id]
    subject, _ = strip(message["subject"])
    body, _ = strip(message["body_html"])
    assert tools.crisis_signal(subject, body) is None


def test_crisis_signal_checks_every_text_given():
    assert tools.crisis_signal("Re: appointment", "I want to die") is not None
    assert tools.crisis_signal("I want to die", "Re: appointment") is not None
    assert tools.crisis_signal("Re: appointment", "Could we move it?") is None


def test_guard1_msg005_is_urgent_with_no_model_call():
    fake = FakeModel([])  # any model call fails the test
    result = harness.run_message("msg-005", True, fake, inbox=INBOX, patients=PATIENTS, hospital=HOSPITAL)
    assert result["status"] == "urgent"
    assert result["urgent"] is not None
    assert result["draft"] is None
    assert fake.calls == []
    assert result["verdict"]["level"] == "still", result["verdict"]
    tripwires = [e for e in result["events"] if e["tool"] == "tripwire"]
    assert len(tripwires) == 1
    assert tripwires[0]["note"].startswith("urgent")


def test_guard1_crisis_in_subject_alone_trips_the_wire():
    msg = copy.deepcopy(INBOX["msg-001"])
    msg["subject"] = "I want to die"
    fake = FakeModel([])
    result = harness.run_message("msg-001", True, fake, inbox={"msg-001": msg}, patients=PATIENTS, hospital=HOSPITAL)
    assert result["status"] == "urgent"
    assert result["urgent"] is not None
    assert result["draft"] is None
    assert fake.calls == []


def test_guard0_does_not_run_the_tripwire():
    fake = FakeModel([tool_turn("draft_reply", {"to": INBOX["msg-005"]["sender_patient_id"], "text": "Thanks."})])
    result = harness.run_message("msg-005", False, fake, inbox=INBOX, patients=PATIENTS, hospital=HOSPITAL)
    assert result["status"] == "drafted"
    assert result["draft"] is not None
    assert not [e for e in result["events"] if e["tool"] == "tripwire"]
    assert len(fake.calls) == 1
