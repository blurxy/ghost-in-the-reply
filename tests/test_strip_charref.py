"""strip() is total: a numeric character reference with thousands of digits must not be able to raise.

Python refuses to turn a string of more than 4300 digits into an int, so html.unescape raised ValueError on
"&#" + 5000 digits. A patient message could therefore crash strip() (and with it GUARD=1 and the dashboard).
Out of range is out of range: a browser shows U+FFFD, and so does strip() now. Review found it (red-team).
"""

from __future__ import annotations

import html
import random

import pytest

from ghostreply import ui
from ghostreply.strip import describe, rendered, safe_unescape, strip

HUGE = "9" * 5000

CASES = {
    "decimal": "a&#" + HUGE + ";b",
    "decimal_no_semicolon": "a&#" + HUGE + "b",
    "hex": "a&#x" + "f" * 5000 + ";b",
    "hex_upper_x": "a&#X" + "F" * 5000 + ";b",
    "in_a_style_attribute": '<b style="display&#' + HUGE + ';:none">x</b>',
    "in_a_hidden_element": "<span hidden>&#" + HUGE + ";ghost</span>after",
    "in_a_comment": "<!--&#" + HUGE + ";-->z",
    "in_an_attribute_value": '<a title="&#' + HUGE + ';">x</a>',
    "right_at_the_limit": "a&#" + "1" * 4300 + ";b",
    "one_past_the_limit": "a&#" + "1" * 4301 + ";b",
    "mixed_with_real_entities": "&amp;&#" + HUGE + ";&lt;b&gt;&#8203;x",
    "twice": "&#" + HUGE + ";&#" + HUGE + ";",
}


@pytest.mark.parametrize("name", CASES)
def test_a_huge_numeric_reference_never_raises_anywhere(name):
    text = CASES[name]
    clean, removed = strip(text)  # must not raise
    assert strip(clean) == (clean, [])  # and it stays idempotent
    rendered(text)
    for span in removed:
        describe(span)
    ui.seance_segments(text, removed)
    assert "&#" not in clean or name in ("in_a_comment",)


@pytest.mark.parametrize("name", ["decimal", "decimal_no_semicolon", "hex", "right_at_the_limit", "one_past_the_limit"])
def test_it_decodes_to_the_replacement_character_as_a_browser_does(name):
    assert strip(CASES[name])[0] == "a�b"


def test_the_staff_view_shows_the_replacement_character_too():
    assert rendered(CASES["decimal"]) == "a�b"


def test_safe_unescape_matches_html_unescape_on_ordinary_text():
    for text in ["", "plain", "&amp; &lt;b&gt; &#8203; &#x41; &copy; &notit; &foo", "AT&amp;T 5 &lt; 6", "&#0; &#1114112;"]:
        assert safe_unescape(text) == html.unescape(text)


def test_safe_unescape_survives_a_huge_reference_inside_a_longer_string():
    assert safe_unescape("x&#" + HUGE + ";&amp;y") == "x�&y"
    assert safe_unescape("&amp;&#x" + "a" * 5000 + ";") == "&�"
    assert safe_unescape("&notarealentity" + "9" * 5000) == html.unescape("&notarealentity" + "9" * 5000)


def test_a_huge_reference_in_a_style_attribute_still_lets_the_real_rule_hide_the_element():
    clean, removed = strip('<b style="color:red; display&#58;none; x:&#' + HUGE + ';">ghost</b>ok')
    assert clean == "ok" and [s["kind"] for s in removed] == ["hidden_html"]


def test_a_random_mix_with_huge_references_stays_total_and_idempotent():
    atoms = ["&#" + "7" * 4400 + ";", "&#x" + "c" * 4400 + ";", "&amp;", "&lt;", "<span hidden>", "</span>", "<p>", "text ",
             "&#8203;", "​", "<!--", "-->", "&", "<", ">", "e", "́"]
    rng = random.Random(4300)
    for _ in range(120):
        text = "".join(rng.choice(atoms) for _ in range(rng.randint(0, 14)))
        clean, removed = strip(text)
        assert strip(clean) == (clean, [])
        assert all(0 <= s["start"] <= s["end"] <= len(text) for s in removed)
        rendered(text)
