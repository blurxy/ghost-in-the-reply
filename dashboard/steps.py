"""The seven steps of the demo and every word the audience sees or Rafi reads from the teleprompter.

One place for all the copy, so pitch-checker can scan it and the page carries no wording of its own. Wording rules
(CLAUDE.md): "closed by design for these two harms", never "unbreakable" or "no gaps"; never "HIPAA violation";
fixtures are synthetic, never PHI; never say Epic's tool is vulnerable.

Fields: id, name (the step list), say (the line to say, presenter view), headline / sub / caption / foot (what the
audience sees), min_ms (the shortest autoplay time that lets every animation finish).
"""

from __future__ import annotations

REPO_URL = "github.com/blurxy/ghost-in-the-reply"

STEPS: list[dict] = [
    {
        "id": "title", "name": "Title", "min_ms": 3000,
        "say": "Ghost in the Reply.",
        "headline": "Ghost in the Reply",
        "sub": "A hidden line in a patient message vs. a hospital's AI assistant",
        "foot": "Gemma 4 12B, running on this laptop",
        # Shown instead of `foot` when every result is a saved run (replay-only, autoplay, the backup video):
        # nothing is running then, and the screen must not say it is.
        "foot_replay": "Gemma 4 12B · saved real runs from this laptop",
    },
    {
        "id": "hook", "name": "Hook", "min_ms": 6000,
        "say": "Hospitals are starting to let AI draft replies to patient messages. One hidden line in a message "
               "can make that AI leak another patient's chart. Watch.",
        "headline": "A normal reschedule request",
        "caption": "As staff see it in the portal inbox. Synthetic patient. Nothing is ever sent.",
    },
    {
        "id": "reveal", "name": "Reveal", "min_ms": 8000,
        "say": "This looks like a normal reschedule request. Here's what the AI actually reads.",
        "headline": "What the AI actually reads",
        "caption": "hidden from people, readable by the model",
    },
    {
        "id": "possessed", "name": "Possessed (GUARD=0)", "min_ms": 14000,
        "say": "This is Wisp, the hospital's assistant, with normal tool access.",
        "headline": "Wisp, with normal tool access",
        "caption": "Synthetic patients. Nothing is ever sent.",
    },
    {
        "id": "whispering", "name": "Whispering (GUARD=1)", "min_ms": 14000,
        "say": "Same message, same model, Warding Circle mode.",
        "headline": "Warding Circle mode",
        "caption": "Same message. Same model. Synthetic patients. Nothing is ever sent.",
        "code_note": "Code looks up only the sender. The reply comes from a hospital template.",
    },
    {
        "id": "why", "name": "Why it works", "min_ms": 9000,
        "say": "The model can still be fooled. It just has nothing to act with. Closed by design for these two harms.",
        "headline": "The model can still be fooled",
        "sub": "It just has nothing to act with.",
        "boxes": [
            {"title": "The model only classifies", "body": "It fills in a small form. It has no tools."},
            {"title": "Code decides whose chart", "body": "The sender's, and only the sender's."},
            {"title": "Templates write the reply", "body": "Hospital wording. Nothing the model wrote."},
        ],
        "foot": "Closed by design for these two harms: another patient's chart, and a fake phone number.",
    },
    {
        "id": "closer", "name": "Closer", "min_ms": 6000,
        "say": "The model may be possessed. The system doesn't have to be.",
        "headline": "The model may be possessed. The system doesn't have to be.",
        "sub": REPO_URL,
        "foot": "Built with Gemma 4 · Ollama · Python · Claude Code",
    },
]

# Under the meter, per level. Whispering is the spec's caption; the others match the terminal UI.
METER_CAPTION = {
    "still": "Nothing hidden was found and nothing went wrong.",
    "whispering": "It heard the ghost. It couldn't act.",
    "possessed": "Harm happened. The red lines say what.",
}

# What each run step expects. A live run with another level triggers the presenter's warning.
EXPECTED_LEVEL = {0: "possessed", 1: "whispering"}


def autoplay_ms(index: int, step_ms: int) -> int:
    """How long autoplay stays on a step: the requested time, but never less than the animations need."""
    return max(int(step_ms), STEPS[index]["min_ms"])


def all_text() -> list[str]:
    """Every string the audience or the presenter can read from steps.py, for the wording checks."""
    out = []
    for step in STEPS:
        for key, value in step.items():
            if key in ("id", "min_ms"):
                continue
            if key == "boxes":
                out += [f"{b['title']}. {b['body']}" for b in value]
            else:
                out.append(str(value))
    out += list(METER_CAPTION.values())
    return out
