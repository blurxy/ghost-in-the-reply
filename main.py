"""Haunted Inbox: run Wisp over the Hollowveil inbox and score how possessed it got.

    python main.py --guard 0|1 [--message ID] [--replay runs/FILE.json]

Wisp only drafts. Nothing is ever sent.
"""

from __future__ import annotations

import argparse
import contextlib
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ghostreply import harness
from ghostreply.oracles import score
from ghostreply.tools import DENIAL

try:  # owned by the ui session; the plain view below keeps the demo alive without it
    from ghostreply import ui
except ImportError:
    ui = None

console = Console()


def _status(label: str):
    if ui is not None:
        return ui.status(label)
    return console.status(label, spinner="moon")


def _plain_show_run(run: dict) -> None:
    """Fallback view for when ui.py is missing or broken: every fact, no styling."""
    guard = "GUARD=1  WARDING CIRCLE ON" if run["guard"] else "GUARD=0  naive Wisp"
    message = run["message"]
    console.rule(f"[bold]{guard}[/]  ·  {message['id']}  ·  {message['subject']}")
    console.print(Panel(message["body_html"], title="message (raw portal HTML)", border_style="dim"))
    trace = Table(title="tool calls", show_lines=False, expand=True)
    for column in ("tool", "args", "allowed", "patients", "note"):
        trace.add_column(column, overflow="fold")
    for event in run["events"]:
        args = {k: v for k, v in event["args"].items() if k != "text"} if isinstance(event["args"], dict) else event["args"]
        trace.add_row(event["tool"], str(args), "yes" if event["allowed"] else "NO",
                      ", ".join(event["patients_touched"]), event["note"] or "")
    console.print(trace)
    if run["draft"]:
        console.print(Panel(run["draft"]["text"], title=f"draft to {run['draft']['to']} (not sent)", border_style="cyan"))
    elif run["status"] == "urgent":
        console.print(Panel("URGENT: flagged for a human. Nothing was drafted.", border_style="bold red"))
    elif run["status"] == "held":  # one fixed denial on screen; the reason is in the verdict's evidence and the log
        console.print(Panel(DENIAL, title="held for staff", border_style="yellow"))
    else:
        console.print(Panel(run.get("error") or "no draft", title=f"status: {run['status']}", border_style="yellow"))
    verdict = run.get("verdict")
    console.print(f"[bold]verdict:[/] {verdict['level'].upper()}  {verdict}" if verdict
                  else "[bold]verdict:[/] not scored")


def show_run(run: dict) -> None:
    if ui is not None:
        try:
            ui.show_run(run, console)
            return
        except Exception as e:  # a broken view must never cost the demo its result
            console.print(f"[red]ui.show_run failed ({e!r}); plain view follows[/]")
    _plain_show_run(run)


def _model_line(run: dict) -> str:
    turns = run["turns"]
    rates = [t["tokens_per_s"] for t in turns if t.get("tokens_per_s") and t.get("eval_count", 0) >= 20]
    rate = f"{sum(rates) / len(rates):.1f} tok/s" if rates else "tok/s n/a"
    return (f"{run['model'].get('name')} · {len(turns)} model calls · {run['duration_ms'] / 1000:.1f}s · {rate}"
            f" · saved {run.get('_saved_to', '(not saved)')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wisp drafts replies to the Hollowveil inbox; the meter scores them.")
    parser.add_argument("--guard", type=int, choices=(0, 1), help="0 = naive Wisp, 1 = Warding Circle mode")
    parser.add_argument("--message", help="one inbox message ID, e.g. msg-004 (default: the whole inbox)")
    parser.add_argument("--replay", metavar="RUN_JSON", help="re-show a saved run without calling the model")
    parser.add_argument("--model", help="Ollama model (default: $GHOST_MODEL or gemma4-12b-agent)")
    parser.add_argument("--no-save", action="store_true", help="do not write the run log to runs/")
    args = parser.parse_args(argv)

    if args.replay:
        run = harness.load_run(args.replay)
        rescored = score(run)
        if run.get("verdict") not in (None, rescored):
            console.print(f"[yellow]note: saved verdict {run['verdict'].get('level')} differs from re-scored "
                          f"{rescored['level']}; showing the re-scored one[/]")
        run["verdict"] = rescored
        show_run(run)
        return 0
    if args.guard is None:
        parser.error("--guard 0|1 is required (or use --replay)")

    inbox = harness.load_inbox()
    patients = harness.load_patients()
    hospital = harness.load_hospital()
    ids = [args.message] if args.message else sorted(inbox)
    model = harness.OllamaModel(model=args.model)
    failed = False
    for msg_id in ids:
        try:
            with _status(f"Wisp is reading {msg_id}…"):
                run = harness.run_message(msg_id, bool(args.guard), model,
                                          inbox=inbox, patients=patients, hospital=hospital)
        except (KeyError, NotImplementedError) as e:
            console.print(f"[red]{e}[/]")
            return 2
        if not args.no_save:
            run["_saved_to"] = str(harness.save_run(run).relative_to(harness.ROOT))
        show_run(run)
        console.print(f"[dim]{_model_line(run)}[/]")
        if run["status"] == "error":
            console.print(f"[red]model error: {run['error']}[/]")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
