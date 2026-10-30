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

GLOW = "bold black on bright_magenta"
LEVELS = ("still", "whispering", "possessed")
LEVEL_COLOR = {"still": "bright_green", "whispering": "bright_yellow", "possessed": "bright_red"}
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
        head = Text(" WARDING CIRCLE ON  ·  GUARD=1 ", style="bold black on bright_green", justify="center")
        sub = Text("Hidden text is stripped before Wisp reads. Closed by design for these two harms: "
                   "leaking another chart, phishing numbers.", style="black on bright_green", justify="center")
        style = "black on bright_green"
    else:
        head = Text(" NO GUARD  ·  GUARD=0 ", style="bold white on red", justify="center")
        sub = Text("Wisp reads the raw message, hidden text included, and its tools are open on purpose.",
                   style="white on red", justify="center")
        style = "white on red"
    return Panel(Group(head, sub), style=style, box=box.HEAVY, padding=(0, 2))


def _header(run: dict) -> Text:
    message = _as_dict(run.get("message"))
    model = _as_dict(run.get("model"))
    parts = ["Hollowveil Memorial Hospital", "Wisp", str(message.get("id", "?"))]
    if model.get("name"):
        parts.append(str(model["name"]))
    ms = run.get("duration_ms")
    if isinstance(ms, (int, float)) and ms:
        parts.append(f"{ms / 1000:.1f} s")
    return _line("  ·  ".join(parts), "dim")


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
        title_align="left", border_style="bright_white", box=box.HEAVY, padding=(1, 2),
    )


# ----------------------------------------------------------------------------------------- seance view

def _is_block(tag_text: str) -> bool:
    return any(name.lower() in BLOCK_TAGS for name in re.findall(r"</?\s*([A-Za-z][A-Za-z0-9]*)", tag_text))


def _append_ghost(out: Text, span: dict) -> None:
    label, shown = describe(span)
    shown = _display(shown, 600).strip()
    if shown or span["kind"] in ("hidden_html", "html_comment"):
        if out.plain and not out.plain.endswith("\n"):
            out.append("\n")
        out.append(f" ▌ {label} ▐ ", GLOW)
        out.append("\n")
        out.append(f" {shown or '(nothing inside)'} ", GLOW)
        out.append("\n")
    else:
        out.append(f"⟨{label.split(' · ', 1)[-1]}⟩", GLOW)


def _seance_text(original: str, removed: list[dict]) -> Text:
    n = len(original)
    cover: list[int | None] = [None] * n
    for idx, span in enumerate(removed):
        hidden = span.get("kind") in HIDDEN_KINDS
        for i in range(max(0, int(span.get("start", 0))), min(n, int(span.get("end", 0)))):
            cur = cover[i]
            if cur is None or (hidden and removed[cur].get("kind") not in HIDDEN_KINDS):
                cover[i] = idx
    out = Text()
    shown_ids: set[int] = set()
    i = 0
    while i < n:
        idx = cover[i]
        j = i
        while j < n and cover[j] == idx:
            j += 1
        if idx is None:
            out.append(_display(html.unescape(original[i:j])))
        elif removed[idx].get("kind") == "html_tag":
            if _is_block(removed[idx].get("text", "")) and out.plain and out.plain[-1] not in " \n":
                out.append("\n")
        elif idx not in shown_ids:
            shown_ids.add(idx)
            _append_ghost(out, removed[idx])
        i = j
    return out


def seance_view(message: dict, removed: list[dict]) -> RenderableType:
    message = _as_dict(message)
    removed = [s for s in (removed or []) if isinstance(s, dict)]
    ghosts = sum(1 for s in removed if s.get("kind") in HIDDEN_KINDS)
    body = _seance_text(str(message.get("body_html", "")), removed)
    body.rstrip()
    if ghosts:
        note = Text("Glowing = hidden from people, readable by the model.", style="dim")
        subtitle = Text(f"{ghosts} hidden {'span' if ghosts == 1 else 'spans'} found", style="bold bright_magenta")
    else:
        note = Text("No hidden text found in this message.", style="bright_green")
        subtitle = Text("0 hidden spans", style="bright_green")
    return Panel(
        Group(body, Text(""), note),
        title=Text("SÉANCE VIEW · the message behind the message", style="bold bright_magenta"),
        title_align="left", subtitle=subtitle, subtitle_align="right",
        border_style="bright_magenta", box=box.DOUBLE, padding=(1, 2),
    )


def clean_view(run: dict) -> RenderableType:
    clean = _as_dict(run.get("strip")).get("clean")
    if clean is None:
        clean = strip(str(_as_dict(run.get("message")).get("body_html", "")))[0]
    return Panel(
        Text(_display(clean).strip() or "(empty message)"),
        title=Text("WHAT WISP READ · after the Warding Circle", style="bold bright_green"),
        title_align="left", border_style="bright_green", box=box.ROUNDED, padding=(0, 2),
    )


# --------------------------------------------------------------------------------------- Agent trace

def _value(v: object) -> str:
    if isinstance(v, str):
        v = v if len(v) <= 40 else v[:40] + "…"
        return json.dumps(v, ensure_ascii=False)
    return repr(v)


def _call(tool: object, args: object) -> str:
    name = str(tool or "?")
    if isinstance(args, dict):
        return f"{name}({', '.join(_value(v) for v in args.values())})"
    return f"{name}({_value(args) if args not in (None, '') else ''})"


def trace_view(events: list[dict] | None, *, sender_id: str | None = None,
               message_id: str | None = None) -> RenderableType:
    events = [e for e in (events or []) if isinstance(e, dict)]
    title = Text("Agent trace", style="bold bright_cyan")
    if not events:
        return Panel(Text("No tool calls. Under the Warding Circle, code does the reading.", style="dim"),
                     title=title, title_align="left", border_style="bright_cyan", box=box.ROUNDED,
                     padding=(0, 2))
    table = Table(box=None, expand=True, pad_edge=False, header_style="bold bright_cyan")
    table.add_column("#", justify="right", no_wrap=True)
    table.add_column("Wisp's call", overflow="fold")
    table.add_column("Result", no_wrap=True)
    table.add_column("Touched", overflow="fold")
    for n, event in enumerate(events, 1):
        allowed = bool(event.get("allowed"))
        result = Text("✓ allowed", style="green") if allowed else Text("✗ refused", style="bold red")
        touched = Text()
        for pid in _as_list(event.get("patients_touched")):
            foreign = sender_id is not None and pid != sender_id
            touched.append(_display(f"chart {pid}" + (" (not the sender)" if foreign else "") + "  "),
                           "bold red" if foreign else "")
        for mid in _as_list(event.get("messages_touched")):
            foreign = message_id is not None and mid != message_id
            touched.append(_display(f"message {mid}" + (" (not this one)" if foreign else "") + "  "),
                           "bold red" if foreign else "")
        note = event.get("note")
        if note:
            touched.append(_display(note, 120), "dim italic")
        table.add_row(Text(str(n)), _line(_call(event.get("tool"), event.get("args")), "bold", 160),
                      result, touched)
    return Panel(table, title=title, title_align="left", border_style="bright_cyan", box=box.ROUNDED,
                 padding=(0, 2))


# -------------------------------------------------------------------------------------------- the draft

def _highlight(text: str, needles: list[str]) -> Text:
    out = Text(text)
    digit_sets = {re.sub(r"\D", "", n) for n in needles if len(re.sub(r"\D", "", n)) >= 7}
    for n in needles:
        if len(n.strip()) >= 3:
            for m in re.finditer(re.escape(n.strip()), text, re.I):
                out.stylize("bold white on red", m.start(), m.end())
    if digit_sets:
        for m in re.finditer(r"\+?\d[\d\s().-]{6,}\d", text):
            digits = re.sub(r"\D", "", m.group(0))
            if any(d in digits or digits in d for d in digit_sets):
                out.stylize("bold white on red", m.start(), m.end())
    return out


def draft_view(run: dict) -> RenderableType:
    status = run.get("status")
    verdict = _as_dict(run.get("verdict"))
    message = _as_dict(run.get("message"))
    if status == "urgent":
        return Panel(Text("URGENT · flagged for a human. Nothing was drafted.", style="bold white on red"),
                     title=Text("DRAFT", style="bold"), title_align="left", border_style="bright_red",
                     box=box.HEAVY, padding=(0, 2))
    if status == "held":
        reason = _display(run.get("held_reason") or "held for review", 300)
        return Panel(Group(Text("HELD · no reply goes to the patient.", style="bold bright_yellow"),
                           Text(reason, style="bright_yellow")),
                     title=Text("DRAFT", style="bold"), title_align="left", border_style="bright_yellow",
                     box=box.HEAVY, padding=(0, 2))
    draft = run.get("draft")
    if status == "drafted" and isinstance(draft, dict):
        sender = message.get("sender_patient_id")
        to = _display(draft.get("to", "?"), 60)
        head = Text("To: ")
        if sender is not None and draft.get("to") != sender:
            head.append(f"{to} (NOT the sender)", "bold white on red")
        else:
            head.append(to, "bold")
        needles = [str(x) for x in _as_list(verdict.get("phishing")) + _as_list(verdict.get("leak"))]
        level = verdict.get("level")
        color = "bright_white" if level == "still" else LEVEL_COLOR.get(str(level), "bright_white")
        return Panel(Group(head, Text(""), _highlight(_display(draft.get("text", "")), needles)),
                     title=Text("DRAFT REPLY", style="bold"), title_align="left",
                     subtitle=Text("drafted only · nothing is sent", style="dim"), subtitle_align="right",
                     border_style=color, box=box.HEAVY, padding=(1, 2))
    if status == "error":
        return Panel(Text(_display(run.get("error") or "the run failed", 300), style="bright_red"),
                     title=Text("RUN ERROR", style="bold"), title_align="left", border_style="bright_red",
                     box=box.HEAVY, padding=(0, 2))
    return Panel(Text("No draft was written.", style="dim"), title=Text("DRAFT", style="bold"),
                 title_align="left", border_style="grey50", box=box.HEAVY, padding=(0, 2))


# -------------------------------------------------------------------------------------------- the meter

_FONT = {
    "S": [" ████", "█    ", " ███ ", "    █", "████ "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
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
        labels.append(Text(f" {name.upper()} ", style=f"bold black on {LEVEL_COLOR[name]}" if i == at
                           else (f"bold {LEVEL_COLOR[name]}" if lit else "dim")))
        bars.append(Text(("█" if lit else "░") * 14, style=LEVEL_COLOR[name] if lit else "grey37"))
    track.add_row(*labels)
    track.add_row(*bars)
    lines = [track, Text(""), Align.center(_big(level.upper(), f"bold {color}")), Text(""),
             Align.center(Text(LEVEL_MEANING[level], style=f"bold {color}"))]
    reasons = Text()
    for key, mark, style in (("leak", "✗ leak: ", "bold red"), ("phishing", "✗ phishing: ", "bold red"),
                             ("evidence", "▸ caught: " if level == "whispering" else "▸ saw: ", "bright_yellow")):
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
        console.print(Align.center(Text("[ press Enter to light the candles ]", style="bold bright_magenta")))
        try:
            console.input("")
        except EOFError:
            pass
    _emit(console, "seance view", lambda: seance_view(message, removed))
    if guard and show_clean:
        _emit(console, "clean view", lambda: clean_view(run))
    _emit(console, "Agent trace", lambda: trace_view(
        run.get("events"), sender_id=message.get("sender_patient_id"), message_id=message.get("id")))
    _emit(console, "draft", lambda: draft_view(run))
    _emit(console, "meter", lambda: meter(run.get("verdict")))
