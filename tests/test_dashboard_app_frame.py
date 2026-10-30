"""The portal-app look on every screen (planning/INBOX-LOOK-SPEC.md): one staff app frame, the inbox's list, reading pane,
composer and drawer, the settings page and the signed-out card. A page.html restyle: no new logic, same view models.

The layout itself (nothing cut off at 1920x1080 or 1280x720, for the canonical run and all six pool variants) is measured
in a browser; these tests pin what a later edit must not lose.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from test_dashboard_m2 import real_inbox_view

from dashboard import viewmodel
from dashboard.server import PAGE

PAGE_TEXT = Path(PAGE).read_text(encoding="utf-8")
CSS = PAGE_TEXT.split("</style>")[0]
MARKUP = PAGE_TEXT.split("</style>")[1].split("<script>")[0]


def test_every_screen_sits_in_one_app_frame_with_a_top_bar_a_left_rail_and_the_honest_badge():
    for part in ('id="appbar"', 'id="rail"', 'id="stage"', 'id="overlay"', 'id="dock"'):
        assert part in MARKUP, part
    bar = MARKUP.split('<header id="appbar">')[1].split("</header>")[0]
    assert "Hollowveil Memorial" in bar and "Patient Messages" in bar and "Search messages" in bar
    assert "J. Rivera" in bar and "Front desk" in bar and "<svg" in bar  # the staff user is synthetic; the logo is inline SVG
    assert 'id="badge"' in bar and 'id="dots"' in bar  # the LIVE/REPLAY badge and the progress dots live in the top bar
    rail = MARKUP.split('<nav id="rail"')[1].split("</nav>")[0]
    for name in ("Inbox", "Flagged", "Drafts", "Sent", "Archived", "Scheduling", "Refills", "Billing", "Wisp AI drafts: On"):
        assert name in rail, name
    assert 'id="rail-n"' in rail  # the Inbox unread count


def test_each_screen_carries_the_words_the_spec_asks_for():
    for words in ("Staff view", "What Wisp reads", "✦ Draft by Wisp", "Wisp activity", "Wisp output", "to Patient Services",
                  "Settings › Wisp › Warding Circle mode", "Signed out", "Code: looked up the sender only", "Reply to "):
        assert words in PAGE_TEXT, words
    assert "class=\"seg\"" in PAGE_TEXT or 'el("div", "seg")' in PAGE_TEXT  # the segmented toggle
    for tool in ("Reply", "Forward", "Archive", "Flag"):
        assert f'["{tool}",' in PAGE_TEXT, tool  # the reading pane's toolbar


def test_it_works_offline_inline_svg_only_no_fonts_no_cdn():
    page = PAGE_TEXT.replace("http://www.w3.org/2000/svg", "")  # the SVG namespace is an identifier, not a request
    assert not re.search(r"<link\b|@import|@font-face|url\(|https?://|<img\b|\bsrc=|<script[^>]+src", page)
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", PAGE_TEXT)  # textContent only
    assert MARKUP.count("<svg") >= 7  # the logo, the search icon and the five folder icons


def test_body_text_is_24px_at_1920_by_1080_and_headlines_stay_large():
    body = re.search(r"--body:max\((\d+)px,([\d.]+)vw\)", CSS)
    assert body and 1920 * float(body.group(2)) / 100 >= 24  # "at least 24 px at 1920x1080"
    assert int(body.group(1)) >= 20  # and a floor that keeps a 1280x720 screen readable
    head = re.search(r"--head:max\((\d+)px", CSS)
    assert head and int(head.group(1)) >= 64


def test_the_badge_and_the_dots_are_never_dimmed_behind_a_splash_card():
    dim = [rule for rule in CSS.split("}") if "#frame.dim" in rule]
    assert dim and all("#top" not in rule and "#badge" not in rule for rule in dim)
    assert "body.presenter #overlay{display:none" in CSS  # the presenter view is never under the audience's card


def test_a_small_screen_gets_its_own_pane_widths_so_nothing_is_cut_off():
    small = CSS.split("@media (max-width:1500px)")[1:]
    joined = " ".join(small)
    assert ".panes.p3{grid-template-columns" in joined and ".panes.r2" in joined and ".drawer .evidence" in joined


def test_the_meter_still_docks_after_the_slam_into_the_top_bars_second_row():
    run_head = PAGE_TEXT.split("function runHead")[1].split("\n}\n")[0]
    assert '$("#dock").append(head)' in run_head
    assert "#dock:empty{display:none}" in CSS


# ------------------------------------------------------------------------------------------- the inbox rows

def test_the_real_inbox_rows_carry_a_preview_a_label_and_a_short_time():
    rows = real_inbox_view()["inbox"]
    assert len(rows) == 4 and rows[0]["selected"] and rows[0]["unread"]
    for row in rows:
        assert row["label"] in ("Scheduling", "Refills", "Billing")
        assert row["preview"] and len(row["preview"]) <= 91 and "<" not in row["preview"]
        assert re.fullmatch(r"\d{1,2}:\d\d [AP]M|Yesterday|Mon|Tue|Wed|Thu|Fri|Sat|Sun", row["short"]), row["short"]
    assert re.fullmatch(r"\d{1,2}:\d\d [AP]M", rows[0]["short"])  # the newest message shows its time


@pytest.mark.parametrize("value,expected", [
    ("2026-10-28T10:20:00-04:00", "10:20 AM"), ("2026-10-27T16:05:00-04:00", "Yesterday"),
    ("2026-10-26T09:12:00-04:00", "Mon"), ("2026-10-28T00:05:00-04:00", "12:05 AM"), ("nope", ""), (None, ""),
])
def test_short_when_is_a_time_yesterday_or_a_weekday(value, expected):
    assert viewmodel.short_when(value, datetime.fromisoformat("2026-10-28T10:20:00-04:00")) == expected


def test_short_when_without_a_reference_shows_the_time_and_the_preview_survives_junk():
    assert viewmodel.short_when("2026-10-27T16:05:00-04:00", None) == "4:05 PM"
    assert viewmodel._preview({}) == "" and viewmodel._preview({"body_html": None}) == ""
    long = viewmodel._preview({"body_html": "<p>" + "word " * 60 + "</p>"})
    assert long.endswith("…") and len(long) <= 91
