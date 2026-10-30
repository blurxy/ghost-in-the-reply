"""strip(): the one function that decides what Wisp is allowed to read.

    clean, removed = strip(body_html)

`clean` is plain text with no markup, no hidden elements and no invisible characters. `removed` lists every
piece that was dropped, as {"kind", "text", "start", "end"}. The offsets point into the ORIGINAL text; a
character that arrived as an entity (&#8203;) or in an earlier pass is covered by the span it came from.
`text` is exactly what was dropped, decoded when it arrived as an entity.

One pass, in this order:
    1. hidden elements with their text, and comments         (hidden_html, html_comment)
    2. every other tag; block tags leave a newline            (html_tag)
    3. entities are unescaped, so &#8203; and &lt; are caught
    4. drop Cf/Cc, the Unicode tag block U+E0000-E007F (by code-point range, because E0000 and
       E0002-E001F are category Cn), variation selectors and private-use characters
    5. NFKC, last, so offsets stay valid and strip(clean) == clean
The pass repeats until it changes nothing, at most MAX_PASSES times. If the text is still changing after
that, every remaining < > & is dropped (unstable_markup), so the result is always a fixed point.

Guarantees: pure (no I/O), total (never raises, no recursion), idempotent (strip(clean)[0] == clean for
every input), and no tag, comment, entity or invisible character survives that a parser would act on. A lone
< or & in ordinary text ("BP 120<140", "AT&T") is kept, and so is a < that no tag can start from, such as <
followed by a combining mark: a browser shows those as text too. Cost of the guarantee, accepted by Rafi:
visible text that merely looks like markup (&lt;b&gt;) is over-stripped.

"Hidden" means exactly this list, and the UI claims nothing beyond it: display:none, visibility:hidden,
the hidden attribute, a font-size of zero or under 1px (0.1em, 10%), an opacity of zero or under 1%, and the
elements script, style, template, head, title, noscript, iframe, noembed, noframes, datalist, dialog,
canvas, video, audio, object and the SVG-only desc, metadata, defs, symbol, clippath, mask, pattern,
marker. If any declaration hides, the element counts as hidden (fail closed). Not detected: colour-on-colour,
off-screen positioning, zero height or width, and rules that live in a <style> block (the block itself is
dropped and recorded, so the meter still says Whispering, but a span hidden only by its class reaches the
model). An unclosed hidden element or comment runs to the end of the text, and a tag cut off by the end of
the text is dropped, as a browser would. Optional end tags (an unclosed <p>) are not implied, so a hidden <p>
can hide more than a browser would. Known false positive: a ZWJ or variation selector inside a real emoji is
dropped and recorded.
"""

from __future__ import annotations

import html
import re
import unicodedata

MAX_PASSES = 5

KINDS = (
    "hidden_html", "html_comment", "html_tag", "format_char", "control_char",
    "variation_selector", "private_use", "unstable_markup",
)
HIDDEN_KINDS = frozenset(k for k in KINDS if k != "html_tag")

_WS = " \t\n\f\r"
_NAME_END = _WS + "/>"
_ATTR_END = _WS + "/>="
_ASCII_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
HIDDEN_TAGS = frozenset(
    "script style template head title noscript iframe noembed noframes datalist dialog canvas video audio object "
    "desc metadata defs symbol clippath mask pattern marker".split()
)
RAWTEXT_TAGS = frozenset("script style iframe noembed noframes xmp title textarea".split())
BLOCK_TAGS = frozenset(
    "address article aside blockquote br dd div dl dt fieldset figure footer form h1 h2 h3 h4 h5 h6 header hr "
    "li main nav ol p pre section table td th tr ul".split()
)

_COMMENT_END = re.compile(r"--!?>")
_RAWTEXT_END = {n: re.compile(r"</" + n + r"(?=[\t\n\f\r />])", re.I) for n in RAWTEXT_TAGS}
_CHARREF = re.compile(r"&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)")
_CSS_COMMENT = re.compile(r"/\*.*?(?:\*/|\Z)", re.S)
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n\f\r]?|(.))", re.S)
_CSS_NUMBER = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+))([a-z%]*)\Z")
_CSS_IMPORTANT = re.compile(r"!\s*important\s*\Z")
# A font-size below these counts as hidden (unitless is only valid for 0). Below 1 px nobody can read it.
_TINY_FONT = {"px": 1.0, "pt": 0.75, "em": 0.1, "rem": 0.1, "ex": 0.1, "ch": 0.1, "%": 10.0,
              "vw": 0.1, "vh": 0.1, "vmin": 0.1, "vmax": 0.1}

_MERGE_KINDS = frozenset(KINDS) - {"hidden_html", "html_comment"}


# --------------------------------------------------------------------------------------------- characters

def char_kind(c: str) -> str | None:
    """The kind strip() records for this character, or None if it is kept."""
    if c in "\t\n\r":
        return None
    cp = ord(c)
    if 0xE0000 <= cp <= 0xE007F:
        return "format_char"
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or cp in (0x180B, 0x180C, 0x180D, 0x180F):
        return "variation_selector"
    cat = unicodedata.category(c)
    if cat in ("Cc", "Cs"):
        return "control_char"
    if cat == "Cf":
        return "format_char"
    if cat == "Co":
        return "private_use"
    return None


def printable(text: str) -> str:
    """Text that is safe to put on a terminal: invisible and control characters are dropped."""
    out = []
    for c in text:
        if c in "  ":
            out.append("\n")
        elif char_kind(c) is None:
            out.append(c)
    return "".join(out)


# ------------------------------------------------------------------------------------------------ tokens

def _read_name(text: str, i: int) -> tuple[str, int]:
    n = len(text)
    j = i
    while j < n and text[j] not in _NAME_END:
        j += 1
    return text[i:j].lower(), j


def _scan_tag(text: str, i: int) -> tuple[int, dict[str, str], bool]:
    """Scan attributes from i. Returns (end, attrs, terminated); unterminated means the text ran out."""
    n = len(text)
    attrs: dict[str, str] = {}
    while i < n:
        c = text[i]
        if c in _WS or c == "/":
            i += 1
            continue
        if c == ">":
            return i + 1, attrs, True
        j = i + 1
        while j < n and text[j] not in _ATTR_END:
            j += 1
        name = text[i:j].lower()
        k = j
        while k < n and text[k] in _WS:
            k += 1
        if k < n and text[k] == "=":
            k += 1
            while k < n and text[k] in _WS:
                k += 1
            if k < n and text[k] in "\"'":
                close = text.find(text[k], k + 1)
                if close == -1:
                    return n, attrs, False
                value = text[k + 1:close]
                i = close + 1
            else:
                e = k
                while e < n and text[e] not in _WS and text[e] != ">":
                    e += 1
                value = text[k:e]
                i = e
        else:
            value = ""
            i = j
        attrs.setdefault(name, value)
    return n, attrs, False


def _tokenize(text: str) -> list[tuple]:
    """A small HTML5-style tokenizer with exact offsets. Never raises; every token advances."""
    n = len(text)
    toks: list[tuple] = []
    i = 0
    while i < n:
        lt = text.find("<", i)
        if lt == -1:
            toks.append(("data", i, n))
            break
        if lt > i:
            toks.append(("data", i, lt))
        i = lt
        c1 = text[i + 1] if i + 1 < n else ""
        if text.startswith("<!--", i):
            if text.startswith("<!-->", i):
                end, term = i + 5, True
            elif text.startswith("<!--->", i):
                end, term = i + 6, True
            else:
                m = _COMMENT_END.search(text, i + 4)
                end, term = (m.end(), True) if m else (n, False)
            toks.append(("comment", i, end, term))
            i = end
        elif c1 in ("!", "?") and c1:
            gt = text.find(">", i + 2)
            end = n if gt == -1 else gt + 1
            toks.append(("comment", i, end, gt != -1))
            i = end
        elif c1 == "/":
            c2 = text[i + 2] if i + 2 < n else ""
            if c2 in _ASCII_LETTERS and c2:
                name, j = _read_name(text, i + 2)
                end, _, term = _scan_tag(text, j)
                toks.append(("end", i, end, name, term))
                i = end
            elif c2 == ">":
                toks.append(("badtag", i, i + 3))
                i += 3
            elif c2 == "":
                toks.append(("data", i, n))
                i = n
            else:
                gt = text.find(">", i + 2)
                end = n if gt == -1 else gt + 1
                toks.append(("comment", i, end, gt != -1))
                i = end
        elif c1 in _ASCII_LETTERS and c1:
            name, j = _read_name(text, i + 1)
            end, attrs, term = _scan_tag(text, j)
            toks.append(("start", i, end, name, attrs, term))
            i = end
            if term and name in RAWTEXT_TAGS:
                m = _RAWTEXT_END[name].search(text, i)
                stop = m.start() if m else n
                if stop > i:
                    toks.append(("data", i, stop))
                i = stop
        else:
            toks.append(("data", i, i + 1))
            i += 1
    return toks


# ---------------------------------------------------------------------------------------------- hiding

def _css_declarations(style: str) -> list[tuple[str, str]]:
    style = _CSS_COMMENT.sub("", style)

    def unescape(m: re.Match) -> str:
        if m.group(1):
            cp = int(m.group(1), 16)
            return chr(cp) if 0 < cp <= 0x10FFFF else "�"
        return m.group(2) or ""

    style = _CSS_ESCAPE.sub(unescape, style)
    out = []
    for decl in style.split(";"):
        prop, sep, value = decl.partition(":")
        if sep:
            value = _CSS_IMPORTANT.sub("", value.strip().lower()).strip()
            out.append((prop.strip().lower(), value))
    return out


def _hidden_reason(name: str, attrs: dict[str, str]) -> str | None:
    """Why an element is hidden, from the fixed list in the module docstring; None if it is not."""
    if name in HIDDEN_TAGS:
        return f"<{name}> element"
    if "hidden" in attrs:
        return "hidden attribute"
    for prop, value in _css_declarations(html.unescape(attrs.get("style", ""))):
        if prop == "display" and value == "none":
            return "display:none"
        if prop == "visibility" and value in ("hidden", "collapse"):
            return "visibility:hidden"
        if prop == "font-size":
            m = _CSS_NUMBER.match(value)
            if m:
                number, unit = float(m.group(1)), m.group(2)
                limit = _TINY_FONT.get(unit)
                if number == 0:
                    return "font-size:0"
                if limit is not None and 0 < number < limit:  # a negative size is invalid CSS: the browser ignores it
                    return "tiny font-size"
        if prop == "opacity":
            m = _CSS_NUMBER.match(value)
            if m:
                number, unit = float(m.group(1)), m.group(2)
                if number == 0 and unit in ("", "%"):
                    return "opacity:0"
                if (unit == "" and number <= 0.01) or (unit == "%" and number <= 1):
                    return "near-zero opacity"
    return None


# ---------------------------------------------------------------------------------------- offset-carrying

Origin = list  # per character of the current text: (start, end) in the ORIGINAL text


def _span(origin: Origin, a: int, b: int) -> tuple[int, int]:
    chunk = origin[a:b]
    return min(o[0] for o in chunk), max(o[1] for o in chunk)


def _record(removed: list[dict], kind: str, text: str, origin_slice: Origin) -> None:
    start = min(o[0] for o in origin_slice)
    end = max(o[1] for o in origin_slice)
    removed.append({"kind": kind, "text": text, "start": start, "end": end})


def _html_pass(text: str, origin: Origin, removed: list[dict], breaks: bool = False) -> tuple[str, Origin]:
    """One pass over the tags. With breaks, every <br> is a line break, as in a browser (the staff view)."""
    out: list[str] = []
    out_o: Origin = []
    last = ""
    stack: list[str] = []
    counts: dict[str, int] = {}
    root = -1
    region_start = 0

    def push(name: str) -> None:
        stack.append(name)
        counts[name] = counts.get(name, 0) + 1

    def pop_to(j: int) -> None:
        for name in stack[j:]:
            counts[name] -= 1
        del stack[j:]

    def visible_tag(name: str, a: int, b: int) -> None:
        nonlocal last
        _record(removed, "html_tag", text[a:b], origin[a:b])
        if (breaks and name == "br") or (name in BLOCK_TAGS and last and last not in _WS):
            out.append("\n")
            out_o.append(_span(origin, a, b))
            last = "\n"

    for tok in _tokenize(text):
        kind, a, b = tok[0], tok[1], tok[2]
        if kind == "data":
            if root < 0:
                out.append(text[a:b])
                out_o.extend(origin[a:b])
                last = text[b - 1]
        elif kind == "comment":
            if root < 0:
                _record(removed, "html_comment", text[a:b], origin[a:b])
        elif kind == "badtag":
            if root < 0:
                _record(removed, "html_tag", text[a:b], origin[a:b])
        elif kind == "start":
            name, attrs, term = tok[3], tok[4], tok[5]
            if not term:
                if root < 0:
                    _record(removed, "hidden_html", text[a:b], origin[a:b])
                continue
            void = name in VOID_TAGS
            if root < 0:
                if not void and _hidden_reason(name, attrs):
                    root = len(stack)
                    region_start = a
                    push(name)
                else:
                    visible_tag(name, a, b)
                    if not void:
                        push(name)
            elif not void:
                push(name)
        else:  # "end"
            name, term = tok[3], tok[4]
            if not term:
                if root < 0:
                    _record(removed, "hidden_html", text[a:b], origin[a:b])
                continue
            j = len(stack) - 1
            if counts.get(name):
                while stack[j] != name:
                    j -= 1
            else:
                j = -1
            if j >= 0:
                pop_to(j)
            if root >= 0:
                if 0 <= j <= root:
                    _record(removed, "hidden_html", text[region_start:b], origin[region_start:b])
                    root = -1
            else:
                visible_tag(name, a, b)

    if root >= 0:
        _record(removed, "hidden_html", text[region_start:], origin[region_start:])
    return "".join(out), out_o


def _unescape(text: str, origin: Origin, removed: list[dict]) -> tuple[str, Origin]:
    out: list[str] = []
    out_o: Origin = []
    pos = 0
    for m in _CHARREF.finditer(text):
        a, b = m.span()
        decoded = html.unescape(m.group(0))
        if decoded == m.group(0):
            continue
        out.append(text[pos:a])
        out_o.extend(origin[pos:a])
        if decoded == "":
            _record(removed, "control_char", m.group(0), origin[a:b])
        else:
            out.append(decoded)
            out_o.extend([_span(origin, a, b)] * len(decoded))
        pos = b
    if pos == 0 and not out:
        return text, origin
    out.append(text[pos:])
    out_o.extend(origin[pos:])
    return "".join(out), out_o


def _drop_chars(text: str, origin: Origin, removed: list[dict]) -> tuple[str, Origin]:
    out: list[str] = []
    out_o: Origin = []
    for c, o in zip(text, origin):
        kind = char_kind(c)
        if kind is None:
            out.append(c)
            out_o.append(o)
        else:
            removed.append({"kind": kind, "text": c, "start": o[0], "end": o[1]})
    if len(out) == len(text):
        return text, origin
    return "".join(out), out_o


def _nfkc(text: str, origin: Origin) -> tuple[str, Origin]:
    """NFKC with the offset map kept. Split before every ASCII character, which no composition crosses."""
    if text.isascii():
        return text, origin
    whole = unicodedata.normalize("NFKC", text)
    if whole == text:
        return text, origin
    starts = [0] + [i for i, c in enumerate(text) if i and c < "\x80"]
    out: list[str] = []
    out_o: Origin = []
    for a, b in zip(starts, starts[1:] + [len(text)]):
        seg = text[a:b]
        norm = unicodedata.normalize("NFKC", seg)
        out.append(norm)
        if norm == seg:
            out_o.extend(origin[a:b])
        else:
            out_o.extend([_span(origin, a, b)] * len(norm))
    joined = "".join(out)
    if joined != whole:  # the split assumption failed: keep the right text, coarse offsets
        return whole, [_span(origin, 0, len(text))] * len(whole)
    return whole, out_o


def _drop_unstable(text: str, origin: Origin, removed: list[dict]) -> tuple[str, Origin]:
    out: list[str] = []
    out_o: Origin = []
    for c, o in zip(text, origin):
        if c in "<>&":
            removed.append({"kind": "unstable_markup", "text": c, "start": o[0], "end": o[1]})
        else:
            out.append(c)
            out_o.append(o)
    return _nfkc("".join(out), out_o)


def _one_pass(text: str, origin: Origin, removed: list[dict]) -> tuple[str, Origin]:
    text, origin = _html_pass(text, origin, removed)
    text, origin = _unescape(text, origin, removed)
    text, origin = _drop_chars(text, origin, removed)
    return _nfkc(text, origin)


def _finish(removed: list[dict]) -> list[dict]:
    removed.sort(key=lambda r: (r["start"], r["end"]))
    merged: list[dict] = []
    for r in removed:
        prev = merged[-1] if merged else None
        if prev and r["kind"] == prev["kind"] and r["kind"] in _MERGE_KINDS and r["start"] <= prev["end"]:
            prev["text"] += r["text"]
            prev["end"] = max(prev["end"], r["end"])
        else:
            merged.append(dict(r))
    return merged


# ------------------------------------------------------------------------------------------- public API

def strip(text: str) -> tuple[str, list[dict]]:
    """Return (clean, removed). See the module docstring for the algorithm and the guarantees."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    removed: list[dict] = []
    cur, cur_o = text, [(i, i + 1) for i in range(len(text))]
    stable = False
    for _ in range(MAX_PASSES):
        local: list[dict] = []
        nxt, nxt_o = _one_pass(cur, cur_o, local)
        if nxt == cur and not local:
            stable = True
            break
        removed.extend(local)
        cur, cur_o = nxt, nxt_o
    if not stable:
        cur, cur_o = _drop_unstable(cur, cur_o, removed)
    return cur, _finish(removed)


def rendered(body_html: str) -> str:
    """What a person sees in the portal: hidden parts and tags gone, every <br> a line break, entities
    decoded once, no NFKC. Invisible and control characters are dropped as well, because a terminal would
    act on them. Display only: Wisp reads strip(), never this."""
    if not isinstance(body_html, str):
        body_html = "" if body_html is None else str(body_html)
    sink: list[dict] = []
    text, origin = _html_pass(body_html, [(i, i + 1) for i in range(len(body_html))], sink, breaks=True)
    text, _ = _unescape(text, origin, sink)
    return "".join(
        "\n" if c in "  " else c
        for c in text
        if c in "\t\n\r" or unicodedata.category(c) not in ("Cc", "Cf", "Cs")
    )


# ------------------------------------------------------------------------------------- the seance labels

_SHORT = {
    0x200B: "ZWSP", 0x200C: "ZWNJ", 0x200D: "ZWJ", 0x2060: "WJ", 0xFEFF: "BOM", 0x00AD: "SHY",
    0x200E: "LRM", 0x200F: "RLM", 0x061C: "ALM", 0x202A: "LRE", 0x202B: "RLE", 0x202C: "PDF",
    0x202D: "LRO", 0x202E: "RLO", 0x2066: "LRI", 0x2067: "RLI", 0x2068: "FSI", 0x2069: "PDI",
    0x180E: "MVS",
}


def _short_name(c: str) -> str:
    cp = ord(c)
    if 0xE0000 <= cp <= 0xE007F:
        return "TAG"
    kind = char_kind(c)
    if kind == "variation_selector":
        return "VS"
    if kind == "private_use":
        return "PUA"
    if kind == "control_char":
        return "CTRL"
    return _SHORT.get(cp, f"U+{cp:04X}")


def _reveal_chars(text: str) -> str:
    """Tag-block characters decode to the ASCII they carry; every other invisible character vanishes."""
    out = []
    for c in text:
        cp = ord(c)
        if 0xE0020 <= cp <= 0xE007E:
            out.append(chr(cp - 0xE0000))
        elif char_kind(c) is None or c in "\t\n\r":
            out.append(c)
    return "".join(out)


def _tidy(text: str) -> str:
    return " ".join(_reveal_chars(html.unescape(text)).split())


def describe(span: dict) -> tuple[str, str]:
    """(label, shown) for one removed span, for the seance view. `shown` is safe to print."""
    kind = span.get("kind", "")
    text = span.get("text", "")
    if kind == "hidden_html":
        toks = _tokenize(text)
        reason = None
        if toks and toks[0][0] == "start":
            reason = _hidden_reason(toks[0][3], toks[0][4]) if toks[0][5] else "tag cut off at the end"
        parts = []
        for tok in toks:
            if tok[0] == "data":
                parts.append(text[tok[1]:tok[2]])
            elif tok[0] == "comment":
                parts.append(re.sub(r"\A<!--|--!?>\Z", "", text[tok[1]:tok[2]]))
        shown = _tidy(" ".join(parts)) or _tidy(text.lstrip("<"))
        return f"HIDDEN TEXT · {reason or 'hidden element'}", shown
    if kind == "html_comment":
        body = re.sub(r"\A<!--|--!?>\Z", "", text)
        return "HIDDEN COMMENT", _tidy(body)
    if kind == "html_tag":
        return "TAG", printable(text)
    if kind == "unstable_markup":
        return f"LEFTOVER MARKUP ×{len(text)}", printable(text)
    runs: list[list] = []
    for c in text:
        name = _short_name(c)
        if runs and runs[-1][0] == name:
            runs[-1][1] += 1
        else:
            runs.append([name, 1])
    summary = " ".join(f"{name}×{count}" for name, count in runs)
    shown = "".join(chr(ord(c) - 0xE0000) for c in text if 0xE0020 <= ord(c) <= 0xE007E)
    labels = {
        "format_char": "TAG-BLOCK TEXT" if shown else "INVISIBLE",
        "control_char": "CONTROL",
        "variation_selector": "VARIATION SELECTOR",
        "private_use": "PRIVATE-USE",
    }
    return f"{labels.get(kind, kind.upper())} · {summary}", shown
