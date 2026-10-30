"""ui.py: the projector screens. Reads only the run-log dict; no model calls, no file I/O, no harness or tools.

    show_run(run)    the one entry point; live runs and --replay both call it

Stacked, about 100 columns, high contrast: banner, what staff see, the seance view (hidden text glowing),
Agent trace, the draft, the possession meter. Everything that came from a message is attacker-controlled, so
it only ever enters as a Rich Text (never parsed as markup) after control, bidi and invisible characters are
dropped. A panel that fails to draw is replaced by a one-line note so the demo keeps going; tests set STRICT.
"""

from __future__ import annotations

import html
import json
import re
from typing import ContextManager
from urllib.parse import urlparse

from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.constrain import Constrain
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .strip import BLOCK_TAGS, HIDDEN_KINDS, describe, printable, rendered, strip

STRICT = False  # tests set this so a broken panel fails loudly instead of degrading
WIDTH = 110
# The same sentence as tools.DENIAL. ui.py must not import tools, so tests/test_ui_smoke.py fails if they drift.
HELD_NOTICE = "Wisp can't help with this request. A member of staff will review the message."

GLOW = "bold #000000 on #ff3df0"
LEVELS = ("still", "whispering", "possessed")
LEVEL_COLOR = {"still": "#00e676", "whispering": "#ffd600", "possessed": "#ff1744"}
LEVEL_MEANING = {
    "still": "Nothing hidden was found and nothing went wrong.",
    "whispering": "A hidden attack was tried and caught before it could do harm.",
    "possessed": "Harm happened. The red lines say what.",
}

_console: Console | None = None


def _default_console() -> Console:
    global _console
    if _console is None:
        _console = Console(highlight=False)
    return _console


# ---------------------------------------------------------------------------------------- safe text

def _display(text: object, limit: int = 2000) -> str:
    """Message text made safe for a terminal: no control, bidi or invisible characters, no bare CR."""
    s = printable(str(text if text is not None else ""))
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return s if len(s) <= limit else s[:limit] + " …(cut)"


def _line(text: object, style: str = "", limit: int = 200) -> Text:
    return Text(_display(text, limit).replace("\n", " "), style=style)


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    """A run log is data from disk; anything that should be a list but is not becomes a list or nothing."""
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value] if isinstance(value, str) and value else []


# --------------------------------------------------------------------------------------------- banner

def banner(guard: int) -> RenderableType:
    if guard:
        head = Text(" WARDING CIRCLE ON  ·  GUARD=1 ", style="bold #000000 on #00e676", justify="center")
        sub = Text("Hidden text is stripped before Wisp reads. Closed by design for these two harms: "
                   "leaking another chart, phishing numbers.", style="#000000 on #00e676", justify="center")
        style = "#000000 on #00e676"
    else:
        head = Text(" NO GUARD  ·  GUARD=0 ", style="bold #ffffff on #d50000", justify="center")
        sub = Text("Wisp reads the raw message, hidden text included, and its tools are open on purpose.",
                   style="#ffffff on #d50000", justify="center")
        style = "#ffffff on #d50000"
    return Panel(Group(head, sub), style=style, box=box.HEAVY, padding=(0, 2))


def _header(run: dict) -> Text:
    message = _as_dict(run.get("message"))
    return _line("  ·  ".join(["Hollowveil Memorial Hospital", "Wisp", str(message.get("id", "?"))]), "dim")


def _trace_meta(run: dict) -> str | None:
    """The Gemma facts for the Agent trace subtitle: model, where it runs, calls, speed, time."""
    model = _as_dict(run.get("model"))
    turns = [t for t in _as_list(run.get("turns")) if isinstance(t, dict)]
    parts = []
    if model.get("name"):
        host = urlparse(str(model.get("host") or "")).hostname or ""
        where = "" if not host else (" on this machine" if host in ("localhost", "127.0.0.1", "::1") else f" on {host}")
        parts.append(f"{model['name']}{where}")
    if turns:
        parts.append(f"{len(turns)} model call{'s' if len(turns) != 1 else ''}")
    rates = [t["tokens_per_s"] for t in turns if isinstance(t.get("tokens_per_s"), (int, float)) and t["tokens_per_s"] > 0]
    if rates:
        parts.append(f"{sum(rates) / len(rates):.1f} tok/s")
    ms = run.get("duration_ms")
    if isinstance(ms, (int, float)) and ms > 0:
        parts.append(f"{ms / 1000:.1f} s")
    return "  ·  ".join(parts) or None


# ------------------------------------------------------------------------------------------- staff view

_SIGNOFF_WORD = r"(?:thanks|thank you|many thanks|best regards|best wishes|kind regards|warm regards|regards|best|sincerely|warmly|cheers|respectfully)"
_SIGNOFF = re.compile(r"\s*" + _SIGNOFF_WORD + r"\b", re.I)
_SIGNOFF_ONLY = re.compile(r"\s*" + _SIGNOFF_WORD + r"\s*[,!.]*\s*\Z", re.I)
_BLANK_RUN = re.compile(r"(?:\n[ \t]*){4,}")  # three or more blank lines in a row
FOLD_LINES = 12  # a portal preview never shows more than this many lines


def _fold(body: str) -> tuple[str, bool]:
    """Cut a message the way a portal preview does: after the sender's sign-off (and the name under it), before
    the first run of three blank lines, or after FOLD_LINES lines, whichever comes first. Display only;
    nothing downstream sees this."""
    cuts = []
    m = _BLANK_RUN.search(body)
    if m:
        cuts.append(m.start())
    pos = 0
    lines = body.split("\n")
    if len(lines) > FOLD_LINES:
        cuts.append(len("\n".join(lines[:FOLD_LINES])))
    for i, line in enumerate(lines):
        end = pos + len(line)
        if body[:pos].strip() and len(line.strip()) <= 60 and _SIGNOFF.match(line):
            if _SIGNOFF_ONLY.match(line):
                for nxt in lines[i + 1:i + 3]:
                    if nxt.strip():
                        if len(nxt.strip()) <= 40 and len(nxt.split()) <= 4:
                            end = body.index(nxt, end) + len(nxt)
                        break
            cuts.append(end)
            break
        pos = end + 1
    if not cuts:
        return body, False
    cut = min(cuts)
    if not body[cut:].strip():
        return body, False
    return body[:cut].rstrip(), True


def rendered_view(message: dict, *, fold: bool = True) -> RenderableType:
    message = _as_dict(message)
    head = Table.grid(padding=(0, 2))
    head.add_column(style="dim", no_wrap=True)
    head.add_column()
    head.add_row("From", _line(f"patient {message.get('sender_patient_id', '?')} (portal sender)"))
    head.add_row("Subject", _line(message.get("subject", ""), "bold"))
    head.add_row("Received", _line(message.get("received_at", "")))
    body = _display(rendered(message.get("body_html", ""))).strip()
    folded = False
    if fold:
        body, folded = _fold(body)
    parts = [head, Text(""), Text(body or "(empty message)")]
    if folded:
        parts.append(Text("… show more", style="dim underline"))
    return Panel(
        Group(*parts),
        title=Text("WHAT STAFF SEE · the portal inbox", style="bold"),
        title_align="left", border_style="#ffffff", box=box.HEAVY, padding=(1, 2),
    )


# ----------------------------------------------------------------------------------------- seance view

def _is_block(tag_text: str) -> bool:
    return any(name.lower() in BLOCK_TAGS for name in re.findall(r"</?\s*([A-Za-z][A-Za-z0-9]*)", tag_text))


_TOOL_CALL = re.compile(r"\b[a-z]+_[a-z_]+\((?:\"[^\"]*\"|'[^']*'|[^()]*)\)")
_PHONE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}")
_LINK = re.compile(r"https?://\S+|www\.\S+", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _signals(text: str) -> list[str]:
    """What a ghost's words contain that matters for the two harms. Plain pattern checks, not a judgement."""
    found = []
    if _TOOL_CALL.search(text):
        found.append("a tool call")
    if _PHONE.search(text):
        found.append("a phone number")
    if _LINK.search(text):
        found.append("a link")
    if _EMAIL.search(text):
        found.append("an email address")
    return found


def _ghost_segment(span: dict) -> tuple:
    """One removed hidden span as data: ("ghost", label, shown, signals) for text with words in it, or
    ("mark", summary) for a bare run of invisible characters."""
    label, shown = describe(span)
    shown = _display(shown, 600).strip()
    if shown or span["kind"] in ("hidden_html", "html_comment"):
        return ("ghost", label, shown or "(nothing inside)", _signals(shown))
    return ("mark", label.split(" · ", 1)[-1])


def _append_segment(out: Text, seg: tuple) -> None:
    kind = seg[0]
    if kind == "text":
        out.append(seg[1])
    elif kind == "break":
        if out.plain and out.plain[-1] not in " \n":
            out.append("\n")
    elif kind == "ghost":
        _, label, shown, signals = seg
        if out.plain and not out.plain.endswith("\n"):
            out.append("\n")
        out.append(f" ▌ {label} ▐ ", GLOW)
        out.append("\n")
        out.append(f" {shown} ", GLOW)
        out.append("\n")
        if signals:
            out.append(f" → contains {', '.join(signals)}", "bold #ffb3f8")
            out.append("\n")
    else:
        out.append(f"⟨{seg[1]}⟩", GLOW)


def seance_segments(original: str, removed: list[dict]) -> list[tuple]:
    """The seance view as data, for any front end: ("text", str), ("break",), ("ghost", label, shown, signals)
    or ("mark", summary), in reading order. Offsets in `removed` point into `original`."""
    n = len(original)
    cover: list[int | None] = [None] * n
    for idx, span in enumerate(removed):
        hidden = span.get("kind") in HIDDEN_KINDS
        for i in range(max(0, int(span.get("start", 0))), min(n, int(span.get("end", 0)))):
            cur = cover[i]
            if cur is None or (hidden and removed[cur].get("kind") not in HIDDEN_KINDS):
                cover[i] = idx
    segments: list[tuple] = []
    shown_ids: set[int] = set()
    i = 0
    while i < n:
        idx = cover[i]
        j = i
        while j < n and cover[j] == idx:
            j += 1
        if idx is None:
            segments.append(("text", _display(html.unescape(original[i:j]))))
        elif removed[idx].get("kind") == "html_tag":
            if _is_block(removed[idx].get("text", "")):
                segments.append(("break",))
        elif idx not in shown_ids:
            shown_ids.add(idx)
            segments.append(_ghost_segment(removed[idx]))
        i = j
    return segments


def _seance_text(original: str, removed: list[dict]) -> Text:
    out = Text()
    for seg in seance_segments(original, removed):
        _append_segment(out, seg)
    return out


def seance_view(message: dict, removed: list[dict], *, subject_removed: list[dict] | None = None,
                guard: int | None = None) -> RenderableType:
    message = _as_dict(message)
    removed = [s for s in _as_list(removed) if isinstance(s, dict)]
    subject_removed = [s for s in _as_list(subject_removed) if isinstance(s, dict)]
    subject_ghosts = [s for s in subject_removed if s.get("kind") in HIDDEN_KINDS]
    ghosts = sum(1 for s in removed if s.get("kind") in HIDDEN_KINDS) + len(subject_ghosts)
    body = Text()
    if subject_ghosts:  # a ghost in the subject line is still a ghost
        body.append("Subject: ", "bold")
        body.append_text(_seance_text(str(message.get("subject", "")), subject_removed))
        body.append("\n\n")
    body.append_text(_seance_text(str(message.get("body_html", "")), removed))
    body.rstrip()
    if ghosts:
        what = ("The Warding Circle strips it before Wisp reads." if guard == 1
                else "Wisp reads it anyway." if guard == 0 else "The model may read it.")
        note = Text("Glowing = hidden from people. " + what, style="dim")
        subtitle = Text(f"{ghosts} hidden {'span' if ghosts == 1 else 'spans'} found", style="bold #ff3df0")
    else:
        note = Text("No hidden text found in this message.", style="#00e676")
        subtitle = Text("0 hidden spans", style="#00e676")
    return Panel(
        Group(body, Text(""), note),
        title=Text("SÉANCE VIEW · the message behind the message", style="bold #ff3df0"),
        title_align="left", subtitle=subtitle, subtitle_align="right",
        border_style="#ff3df0", box=box.DOUBLE, padding=(1, 2),
    )


def clean_view(run: dict) -> RenderableType:
    clean = _as_dict(run.get("strip")).get("clean")
    if clean is None:
        clean = strip(str(_as_dict(run.get("message")).get("body_html", "")))[0]
    return Panel(
        Text(_display(clean).strip() or "(empty message)"),
        title=Text("WHAT WISP READ · after the Warding Circle", style="bold #00e676"),
        title_align="left", border_style="#00e676", box=box.ROUNDED, padding=(0, 2),
    )


# --------------------------------------------------------------------------------------- Agent trace

def _value(v: object) -> str:
    if isinstance(v, str):
        v = " ".join(v.split())  # a draft's newlines would show up as \n
        v = v if len(v) <= 28 else v[:28] + "…"
        return json.dumps(v, ensure_ascii=False)
    return repr(v)


def _call(tool: object, args: object) -> str:
    name = str(tool or "?")
    if isinstance(args, dict):
        return f"{name}({', '.join(_value(v) for v in args.values())})"
    return f"{name}({_value(args) if args not in (None, '') else ''})"


def trace_view(events: list[dict] | None, *, sender_id: str | None = None,
               message_id: str | None = None, meta: str | None = None) -> RenderableType:
    events = [e for e in _as_list(events) if isinstance(e, dict)]
    title = Text("Agent trace", style="bold #00e5ff")
    subtitle = Text(_display(meta, 160).replace("\n", " "), style="dim") if meta else None
    if not events:
        return Panel(Text("No tool calls. Under the Warding Circle, code does the reading.", style="dim"),
                     title=title, title_align="left", subtitle=subtitle, subtitle_align="right",
                     border_style="#00e5ff", box=box.ROUNDED, padding=(0, 2))
    table = Table(box=None, expand=True, pad_edge=False, header_style="bold #00e5ff")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Wisp's call", overflow="fold")
    table.add_column("Result", no_wrap=True)
    table.add_column("Touched", overflow="fold")
    foreign_charts: set[str] = set()
    foreign_messages: set[str] = set()
    refused = 0
    for n, event in enumerate(events, 1):
        allowed = bool(event.get("allowed"))
        refused += not allowed
        result = Text("✓ allowed", style="#00e676") if allowed else Text("✗ refused", style="bold #ff1744")
        touched = Text()
        for pid in _as_list(event.get("patients_touched")):
            if sender_id is None:
                touched.append(_display(f"chart {pid}  "))
            elif pid == sender_id:
                touched.append(_display(f"chart {pid} (the sender)  "), "#00e676")
            else:
                foreign_charts.add(str(pid))
                touched.append(_display(f"chart {pid} (not the sender)  "), "bold #ff1744")
        for mid in _as_list(event.get("messages_touched")):
            if message_id is None:
                touched.append(_display(f"message {mid}  "))
            elif mid == message_id:
                touched.append(_display(f"message {mid} (this message)  "), "#00e676")
            else:
                foreign_messages.add(str(mid))
                touched.append(_display(f"message {mid} (not this one)  "), "bold #ff1744")
        note = event.get("note")
        if note:
            touched.append(_display(note, 120), "dim italic")
        table.add_row(Text(str(n)), _line(_call(event.get("tool"), event.get("args")), "bold", 160),
                      result, touched)
    parts: list[RenderableType] = [table]
    if sender_id is not None:  # the one line a judge can read from the back of the room
        verdict = Text()
        if foreign_charts or foreign_messages:
            reached = [f"chart {c}" for c in sorted(foreign_charts)] + [f"message {m}" for m in sorted(foreign_messages)]
            verdict.append("✗ Wisp reached beyond this message: " + _display(", ".join(reached), 120), "bold #ff1744")
        else:
            verdict.append("✓ Every call stayed inside the sender's chart and this message.", "bold #00e676")
        if refused:
            verdict.append(f"   {refused} call{'s' if refused != 1 else ''} refused", "bold #ffd600")
        parts += [Text(""), verdict]
    return Panel(Group(*parts), title=title, title_align="left", subtitle=subtitle, subtitle_align="right",
                 border_style="#00e5ff", box=box.ROUNDED, padding=(0, 2))


# -------------------------------------------------------------------------------------------- the draft

def _highlight(text: str, needles: list[str]) -> Text:
    out = Text(text)
    digit_sets = {re.sub(r"\D", "", n) for n in needles if len(re.sub(r"\D", "", n)) >= 7}
    for n in needles:
        if len(n.strip()) >= 3:
            for m in re.finditer(re.escape(n.strip()), text, re.I):
                out.stylize("bold #ffffff on #d50000", m.start(), m.end())
    if digit_sets:
        for m in re.finditer(r"[+(]?\d[\d\s().-]{6,}\d", text):
            digits = re.sub(r"\D", "", m.group(0))
            if any(d in digits or digits in d for d in digit_sets):
                out.stylize("bold #ffffff on #d50000", m.start(), m.end())
    return out


def draft_view(run: dict) -> RenderableType:
    status = run.get("status")
    verdict = _as_dict(run.get("verdict"))
    message = _as_dict(run.get("message"))
    if status == "urgent":
        phrase = _display(run.get("urgent"), 80).replace("\n", " ").strip()
        lines = [Align.center(_big("URGENT", "bold #ffffff on #d50000")), Text(""),
                 Align.center(Text("Crisis language. Flagged for a human to read now. Nothing was drafted.",
                                   style="bold #ffffff on #d50000"))]
        if phrase:
            lines.append(Align.center(Text(f"matched: “{phrase}”", style="#ffffff on #d50000")))
        return Panel(Group(*lines), title=Text(" URGENT ", style="bold #ffffff on #d50000"), title_align="left",
                     border_style="bold #ff1744", style="#ffffff on #d50000", box=box.HEAVY, padding=(1, 2))
    if status == "held":
        # One fixed string, as tools.DENIAL: the why stays in the log and in the meter's evidence, not here.
        return Panel(Group(Text(HELD_NOTICE, style="bold #ffd600"),
                           Text("Held for staff review. Nothing was sent.", style="dim")),
                     title=Text("DRAFT · HELD", style="bold"), title_align="left",
                     border_style="#ffd600", box=box.HEAVY, padding=(0, 2))
    draft = run.get("draft")
    if status == "drafted" and isinstance(draft, dict):
        sender = message.get("sender_patient_id")
        to = _display(draft.get("to", "?"), 60)
        head = Text("To: ")
        if sender is not None and draft.get("to") != sender:
            head.append(f"{to} (NOT the sender)", "bold #ffffff on #d50000")
        else:
            head.append(to, "bold")
        needles = [str(x) for x in _as_list(verdict.get("phishing")) + _as_list(verdict.get("leak"))]
        level = verdict.get("level")
        color = "#ffffff" if level == "still" else LEVEL_COLOR.get(str(level), "#ffffff")
        return Panel(Group(head, Text(""), _highlight(_display(draft.get("text", "")), needles)),
                     title=Text("DRAFT REPLY", style="bold"), title_align="left",
                     subtitle=Text("drafted only · nothing is sent", style="dim"), subtitle_align="right",
                     border_style=color, box=box.HEAVY, padding=(1, 2))
    if status == "error":
        return Panel(Text(_display(run.get("error") or "the run failed", 300), style="#ff1744"),
                     title=Text("RUN ERROR", style="bold"), title_align="left", border_style="#ff1744",
                     box=box.HEAVY, padding=(0, 2))
    return Panel(Text("No draft was written.", style="dim"), title=Text("DRAFT", style="bold"),
                 title_align="left", border_style="grey50", box=box.HEAVY, padding=(0, 2))


# -------------------------------------------------------------------------------------------- the meter

_FONT = {
    "S": [" ████", "█    ", " ███ ", "    █", "████ "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "I": ["█████", "  █  ", "  █  ", "  █  ", "█████"],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "W": ["█   █", "█   █", "█ █ █", "██ ██", "█   █"],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "G": [" ████", "█    ", "█  ██", "█   █", " ███ "],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
}


def _big(word: str, style: str) -> Text:
    if not all(ch in _FONT for ch in word):
        return Text(word, style=style)
    rows = ["" for _ in range(5)]
    for ch in word:
        rows = [row + glyph + " " for row, glyph in zip(rows, _FONT[ch])]
    return Text("\n".join(rows), style=style)


def meter(verdict: dict | None) -> RenderableType:
    verdict = _as_dict(verdict)
    level = verdict.get("level")
    title = Text("POSSESSION METER", style="bold")
    if level not in LEVELS:
        return Panel(Text("No verdict yet.", style="dim"), title=title, title_align="left",
                     border_style="grey50", box=box.HEAVY, padding=(0, 2))
    at = LEVELS.index(level)
    color = LEVEL_COLOR[level]
    track = Table.grid(expand=True)
    for _ in LEVELS:
        track.add_column(justify="center", ratio=1)
    labels, bars = [], []
    for i, name in enumerate(LEVELS):
        lit = i <= at
        label = Text(f" {name.upper()} ")
        # stylize, not Text(style=...): a base style would also paint the padding out to the cell edge
        label.stylize(f"bold #000000 on {LEVEL_COLOR[name]}" if i == at
                      else (f"bold {LEVEL_COLOR[name]}" if lit else "dim"))
        labels.append(label)
        bars.append(Text(("█" if lit else "░") * 14, style=LEVEL_COLOR[name] if lit else "grey37"))
    track.add_row(*labels)
    track.add_row(*bars)
    lines = [track, Text(""), Align.center(_big(level.upper(), f"bold {color}")), Text(""),
             Align.center(Text(LEVEL_MEANING[level], style=f"bold {color}"))]
    reasons = Text()
    for key, mark, style in (("leak", "✗ leak: ", "bold #ff1744"), ("phishing", "✗ phishing: ", "bold #ff1744"),
                             ("evidence", "▸ caught: " if level == "whispering" else "▸ saw: ", "#ffd600")):
        for item in _as_list(verdict.get(key)):
            reasons.append(mark, style)
            reasons.append(_display(item, 160).replace("\n", " ") + "\n")
    if reasons.plain:
        reasons.rstrip()
        lines += [Text(""), reasons]
    return Panel(Group(*lines), title=title, title_align="left", border_style=color, box=box.HEAVY,
                 padding=(1, 2))


# --------------------------------------------------------------------------------------------- the run

def status(label: str) -> ContextManager:
    """A spinner for while the model works: `with ui.status("Wisp is reading..."): ...`"""
    return _default_console().status(Text(label), spinner="dots")


def _emit(console: Console, name: str, build) -> None:
    try:
        renderable = build()
    except Exception as exc:  # a broken panel must not end the demo
        if STRICT:
            raise
        renderable = Text(f"(could not draw {name}: {type(exc).__name__})", style="dim")
    console.print(Constrain(renderable, WIDTH))


def show_run(run: dict, console: Console | None = None, *, pause_before_seance: bool = False,
             show_clean: bool = False) -> None:
    """Draw one run log. With pause_before_seance, wait for Enter before the reveal (the demo beat). With
    show_clean, a GUARD=1 run also shows the text Wisp read after strip(), which still holds any visible
    ghost; off by default because it changes what the audience sees."""
    console = console or _default_console()
    run = _as_dict(run)
    message = _as_dict(run.get("message"))
    guard = 1 if run.get("guard") else 0
    info = _as_dict(run.get("strip"))
    removed = info.get("removed")
    if removed is None:
        removed = strip(str(message.get("body_html", "")))[1]

    _emit(console, "header", lambda: _header(run))
    _emit(console, "banner", lambda: banner(guard))
    _emit(console, "staff view", lambda: rendered_view(message))
    if pause_before_seance and console.is_terminal:
        console.print(Align.center(Text("[ press Enter to light the candles ]", style="bold #ff3df0")))
        try:
            console.input("")
        except EOFError:
            pass
    subject_removed = _as_dict(info.get("subject")).get("removed")
    if subject_removed is None:
        subject_removed = strip(str(message.get("subject", "")))[1]
    _emit(console, "seance view",
          lambda: seance_view(message, removed, subject_removed=subject_removed, guard=guard))
    if guard and show_clean:
        _emit(console, "clean view", lambda: clean_view(run))
    _emit(console, "Agent trace", lambda: trace_view(
        run.get("events"), sender_id=message.get("sender_patient_id"), message_id=message.get("id"),
        meta=_trace_meta(run)))
    _emit(console, "draft", lambda: draft_view(run))
    _emit(console, "meter", lambda: meter(run.get("verdict")))
