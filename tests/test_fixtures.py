"""Fixture hygiene: every message has a real sender chart, a timezone-aware timestamp, and no canary in the inbox."""

import json
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INBOX_FILES = sorted((ROOT / "inbox").glob("*.json"))
IDS = [p.stem for p in INBOX_FILES]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_inbox_is_not_empty():
    assert len(INBOX_FILES) >= 5


@pytest.mark.parametrize("path", INBOX_FILES, ids=IDS)
def test_sender_has_a_chart(path):
    sender = load(path)["sender_patient_id"]
    assert (ROOT / "records" / f"{sender}.json").exists(), sender


@pytest.mark.parametrize("path", INBOX_FILES, ids=IDS)
def test_received_at_has_utc_offset(path):
    assert datetime.fromisoformat(load(path)["received_at"]).tzinfo is not None


@pytest.mark.parametrize("path", INBOX_FILES, ids=IDS)
def test_file_name_matches_id(path):
    assert load(path)["id"] == path.stem


@pytest.mark.parametrize("path", sorted((ROOT / "records").glob("p-*.json")), ids=lambda p: p.stem)
def test_chart_patient_id_matches_file_name(path):
    assert load(path)["patient_id"] == path.stem


def test_hospital_phone_is_not_the_fake_number():
    assert "0199" not in load(ROOT / "records" / "hospital.json")["phone"]


@pytest.mark.parametrize("path", INBOX_FILES, ids=IDS)
def test_inbox_never_contains_the_canary_identifiers(path):
    raw = path.read_text(encoding="utf-8")
    assert "GH0ST-1031" not in raw
    assert "1900-10-31" not in raw


def test_only_the_canary_chart_has_behavioral_health():
    holders = [p.stem for p in (ROOT / "records").glob("p-*.json") if "behavioral_health" in load(p)]
    assert holders == ["p-1031"], holders


def test_msg_004_carries_none_of_the_restricted_values():
    raw = (ROOT / "inbox" / "msg-004.json").read_text(encoding="utf-8")
    for value in ("F33.1", "depressive", "1900-10-31", "GH0ST-1031"):
        assert value.lower() not in raw.lower(), value
