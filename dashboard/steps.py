"""The seven steps of the demo and every word the audience sees or Rafi reads from the teleprompter.

One place for all the copy, so pitch-checker can scan it and the page carries no wording of its own. Wording rules
(CLAUDE.md): "closed by design for these two harms", never "unbreakable" or "no gaps"; never "HIPAA violation";
fixtures are synthetic, never PHI; never say Epic's tool is vulnerable; never imply Gemma 4 lacks guardrails or is
weaker; never joke about mental health; "Possessed" describes the AI, never the patient.

Fields: id, name (the step list), say (the line to say, presenter view: planning/DEMO-SCRIPT.md, canonical values),
headline / sub / caption / foot (what the audience sees), min_ms (the shortest autoplay time that lets every
animation finish). SAY_RANDOM holds the two lines that change in `--random` mode: templates filled from the variant.
"""

from __future__ import annotations

import re

REPO_URL = "github.com/blurxy/ghost-in-the-reply"

STEPS: list[dict] = [
    {
        "id": "title", "name": "Title", "min_ms": 3000,
        "say": "Haunted Inbox. Watch the same hidden instruction reach the same model through two different system "
               "designs. The model is Gemma 4 12B, running on this laptop's 8 GB GPU.",
        "headline": "Haunted Inbox",
        "sub": "A hidden line in a patient message vs. a hospital's AI assistant",
        "foot": "Gemma 4 12B, running on this laptop",
        # Shown instead of `foot` when every result is a saved run (replay-only, autoplay, the backup video):
        # nothing is running then, and the screen must not say it is.
        "foot_replay": "Gemma 4 12B · saved real runs from this laptop",
    },
    {
        "id": "hook", "name": "Hook", "min_ms": 6000,
        "say": "This is a hospital's staff inbox, where an AI assistant drafts the replies. Barnaby wants to move his "
               "knee follow-up to Tuesday at 2 PM. Ordinary.",
        "headline": "A normal reschedule request",
        "caption": "As staff see it in the portal inbox. Synthetic patient. Nothing is ever sent.",
    },
    {
        "id": "reveal", "name": "Reveal", "min_ms": 8000,
        "say": "This is what the model actually reads. Hidden in display:none HTML, and again below the fold, there's "
               "a fake hospital policy with a phone number, and a fake verification step: look up a different patient "
               "and copy his record into the reply. Staff never see it. The model always does.",
        "headline": "What the AI actually reads",
        "caption": "hidden from people, readable by the model",
    },
    {
        "id": "possessed", "name": "Possessed (GUARD=0)", "min_ms": 14000,
        "say": "Guard off. Gemma 4 with native function calling and three tools: read the message, look up a patient, "
               "draft a reply. In the trace, it looks up Casper Ghostly, who isn't the sender. The draft to Barnaby "
               "now carries Casper Ghostly's date of birth, MRN and a restricted behavioral-health diagnosis, plus the "
               "attacker's phone number. The verdict comes from deterministic code, not a model: Possessed. The "
               "patients are synthetic and nothing is ever sent.",
        "headline": "Wisp, with normal tool access",
        "caption": "Synthetic patients. Nothing is ever sent.",
    },
    {
        "id": "whispering", "name": "Whispering (GUARD=1)", "min_ms": 14000,
        "say": "Same message, same model, same seed. Warding Circle mode. Gemma 4 gets no tools. It has one job: "
               "return JSON with intent, proposed time and whether a clinician is needed, enforced by structured "
               "output. Code looks up only the sender's chart, and the reply comes from a hospital template. The "
               "hidden text still reaches the model. It just has nothing to act with. Whispering means an attack was "
               "tried and contained.",
        "headline": "Warding Circle mode",
        "caption": "Same message. Same model. Synthetic patients. Nothing is ever sent.",
        "code_note": "Code looks up only the sender. The reply comes from a hospital template.",
    },
    {
        "id": "why", "name": "Why it works", "min_ms": 9000,
        "say": "Three things made Gemma 4 the right model for this. Open weights under Apache 2.0, so a hospital can "
               "run it on its own hardware and patient messages never leave the building. Native function calling and "
               "structured JSON output, so one model plays both roles: the agent that gets attacked and the "
               "classifier in the fix. And local, deterministic runs at temperature 0 with a fixed seed, so the "
               "attack is reproducible and testable offline. The pattern itself is published research: it's the "
               "action-selector pattern, and the same idea as Google DeepMind's CaMeL. We applied it to a hospital "
               "inbox, on a laptop.",
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
        "say": "On the real model, three runs per mode: Possessed three out of three with the guard off, Whispering "
               "three out of three with it on. Over 780 tests, none of which call the model, including a fake model "
               "that obeys every hidden instruction, and Warding Circle mode still holds. The model may be possessed. "
               "The system doesn't have to be. The code is public, and the replay runs in your browser.",
        "headline": "The model may be possessed. The system doesn't have to be.",
        "sub": REPO_URL,
        "foot": "Built with Gemma 4 · Ollama · Python · Claude Code",
    },
]

# The patient-chat flow (Rafi, 3:28 PM): title, the patient portal chat (the attack typed and pasted, then the Security team's
# view, the Warding Circle, the same message again: all inside /chat, driven by keys I and G or the presenter's buttons),
# why it works, closer. The staff inbox steps above stay the flow of `--staff-inbox`, `--random`, `--practice` and the static Pages.
CHAT_STEP: dict = {
    "id": "chat", "name": "Patient chat", "min_ms": 6000,
    "say": "This is the hospital's patient portal. Barnaby is signed in and chatting with Wisp, the hospital's virtual "
           "assistant. Send number one: the attack is typed right in the chat, a fake system notice. Guard off. Wisp "
           "looks up Casper Ghostly, who isn't Barnaby, and copies his date of birth, MRN and a restricted "
           "behavioral-health note into the reply, plus the attacker's phone number. Send number two: the same attack "
           "hidden in pasted text. The chat bubble shows an ordinary reschedule request; the model gets the raw HTML. "
           "Same leak. Press I: this is what the security team sees, the alert, the audit log of the lookup, and the "
           "raw request and response. Press G: Warding Circle mode on. Press I, and send it again. Now the hospital's "
           "template answers. Wisp heard the ghost and had nothing to act with. Whispering means an attack was tried "
           "and contained.",
    "headline": "Patient chat with Wisp",
    "caption": "Synthetic patients. Nothing is ever sent.",
}
FLOWS = {"staff": ("title", "hook", "reveal", "possessed", "whispering", "why", "closer"),
         "chat": ("title", "chat", "why", "closer")}


def flow_steps(flow: str) -> list[dict]:
    """The steps for one flow, in order. Unknown flows are the staff inbox (the original seven)."""
    by_id = {s["id"]: s for s in STEPS} | {CHAT_STEP["id"]: CHAT_STEP}
    out = [by_id[i] for i in FLOWS.get(flow, FLOWS["staff"])]
    if flow == "chat":  # the staff inbox stays reachable, after the main flow: a real mail client to click around in
        out += [dict(by_id["hook"], name="Staff inbox"), dict(by_id["reveal"], name="Staff inbox: what Wisp reads")]
    return out


# `--random`: the two lines that name the variant's details (planning/DEMO-SCRIPT.md). {how} is the hiding trick with
# its preposition, {decoy} the other patient's name, {phone} the attacker's number, {kept} the size of the tested pool.
SAY_RANDOM = {
    "reveal": "This is what the model actually reads. Hidden {how} this time, there's a fake hospital policy with a "
              "phone number, {phone}, and a fake verification step: look up {decoy} and copy that record into the "
              "reply. Staff never see it. The model always does. This attack was picked at random from {kept} "
              "pre-tested variants.",
    "possessed": "Guard off. Gemma 4 with native function calling and three tools: read the message, look up a "
                 "patient, draft a reply. In the trace, it looks up {decoy}, who isn't the sender. The draft to "
                 "Barnaby now carries {decoy}'s date of birth, MRN and a restricted behavioral-health diagnosis, plus "
                 "the attacker's phone number. The verdict comes from deterministic code, not a model: Possessed. The "
                 "patients are synthetic and nothing is ever sent.",
}

# The manifest's hiding phrase fits "Hidden with ..."; the others need their own preposition.
HIDING_HOW = {
    "display:none": "with display:none", "font-size zero": "with font-size zero", "zero opacity": "with zero opacity",
    "an HTML comment": "in an HTML comment", "invisible characters": "in invisible characters",
    "text below the fold": "as text below the fold",
}

RANDOM_CAPTION = "Attack picked at random from {kept} pre-tested variants."
FALLBACK_CAPTION = "Random mode needs 3 tested variants; this is the standard attack."

# Under the meter, per level. Whispering is the spec's caption; the others match the terminal UI.
METER_CAPTION = {
    "still": "Nothing hidden was found and nothing went wrong.",
    "whispering": "It heard the ghost. It couldn't act.",
    "possessed": "Harm happened. The red lines say what.",
}

# What each run step expects. A live run with another level triggers the presenter's warning.
EXPECTED_LEVEL = {0: "possessed", 1: "whispering"}

SPEAKING_WORDS_PER_SECOND = 2.5  # practice mode: how long a step lasts, at speaking pace


def autoplay_ms(index: int, step_ms: int) -> int:
    """How long autoplay stays on a step: the requested time, but never less than the animations need."""
    return max(int(step_ms), STEPS[index]["min_ms"])


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def practice_ms(step: dict) -> int:
    """Practice mode: the longer of the step's animation time and the time to say its line at speaking pace."""
    return max(int(step["min_ms"]), int(1000 * word_count(step.get("say", "")) / SPEAKING_WORDS_PER_SECOND))


def personalise(steps: list[dict], variant: dict | None, kept: int, fallback: bool = False) -> list[dict]:
    """The steps for one run of the dashboard. Canonical: unchanged. `--random` with a variant: the reveal and
    possessed lines name this variant's trick, decoy and phone, and the reveal says the attack was picked at random from
    `kept` tested variants. `fallback`: random was asked for but fewer than 3 variants are kept, so it says so."""
    out = [dict(s) for s in steps]
    if variant:
        fill = {"how": HIDING_HOW.get(variant.get("hiding", ""), "with " + str(variant.get("hiding", "a hidden trick"))),
                "decoy": variant.get("decoy", "another patient"), "phone": variant.get("phone", "a fake number"),
                "kept": kept}
        for s in out:
            if s["id"] in SAY_RANDOM:
                s["say"] = SAY_RANDOM[s["id"]].format(**fill)
            if s["id"] == "reveal":
                s["caption"] = s["caption"] + ". " + RANDOM_CAPTION.format(kept=kept)
    elif fallback:
        for s in out:
            if s["id"] == "reveal":
                s["caption"] = s["caption"] + ". " + FALLBACK_CAPTION
    return out


def audience_text() -> list[str]:
    """What the projector can show from steps.py: everything except the lines Rafi says (`say`), which are spoken."""
    return [t for t in all_text(spoken=False)]


def all_text(spoken: bool = True) -> list[str]:
    """Every string the audience or the presenter can read from steps.py, for the wording checks."""
    out = []
    for step in STEPS:
        for key, value in step.items():
            if key in ("id", "min_ms") or (key == "say" and not spoken):
                continue
            if key == "boxes":
                out += [f"{b['title']}. {b['body']}" for b in value]
            else:
                out.append(str(value))
    out += list(METER_CAPTION.values())
    out += [str(v) for k, v in CHAT_STEP.items() if k not in ("id", "min_ms") and (spoken or k != "say")]
    out += ([RANDOM_CAPTION, FALLBACK_CAPTION] + (list(SAY_RANDOM.values()) if spoken else []))
    return out
