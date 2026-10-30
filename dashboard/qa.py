"""The judge Q&A for practice mode: question | model answer. Text copied from planning/DEMO-SCRIPT.md (judge Q&A),
which mirrors the doc "Haunted Inbox: Demo Script (Best Use of Gemma 4)". Wording rules (CLAUDE.md) apply to every line.
"""

from __future__ import annotations

import random

QUESTIONS: list[tuple[str, str]] = [
    ("Isn't this just input sanitization?",
     "No. strip() is one layer, and a copy of the ghost survives it on purpose. The guarantee comes from the model "
     "having no tools and no free-text path to the patient."),
    ("What if the injection changes the JSON, like a wrong intent or a poisoned time?",
     "Every field is checked in code: intent is an enum, and proposed_time must parse as ISO inside 90 days and is "
     "re-printed from the parsed value. Anything else holds the draft for a human. Worst case is a wrong template, "
     "never another patient's data."),
    ("Is this a Gemma weakness?",
     "No. Hidden-instruction attacks work on every LLM; it's #1 on the OWASP LLM Top 10. We didn't modify Gemma; the "
     "weights are stock. Gemma 4's structured output is what made the fix cheap."),
    ("Why not a guard model like ShieldGemma?",
     "ShieldGemma classifies harmful content, not injections, and detection is probabilistic. We wanted a property "
     "that holds even when detection fails. A classifier can sit on top as defense in depth."),
    ("Doesn't Warding Circle mode lose flexibility?",
     "Yes, on purpose. It's the action-selector pattern, the most restrictive one in the literature. Triage into a "
     "few intents is the inbox's job; anything else goes to a human."),
    ("Why 12B, not E4B or 26B?",
     "12B fits the 8 GB card at 100% GPU and its function calling is reliable enough to show the attack every run. "
     "E4B is the offline fallback."),
    ("Is this live?",
     "Look at the badge: LIVE means Gemma 4 just ran on this laptop; REPLAY means a saved real run, and the screen "
     "never labels a replay as live."),
    ("Why Whispering and not Still in guard mode?",
     "Because an attack was there and got contained. Still would hide that something was tried."),
    ("Does strip() catch everything?",
     "No, and it doesn't need to. Guard mode stays safe even if the model obeys every ghost, and the tests drive "
     "exactly that."),
    ("How does this relate to research?",
     "It's the action-selector pattern from Beurer-Kellner et al. (2025). Google DeepMind's CaMeL generalizes the "
     "idea: a quarantined LLM with no tools, plus capabilities enforced in code."),
    ("What's next for production?",
     "A human review queue for held drafts, staff-written templates per intent, and the next ghost: hidden text in "
     "images, since Gemma 4 reads images too."),
    ("Why should a patient care?",
     "The patient never sees the hidden line, and the leak is the kind of disclosure HIPAA exists to prevent."),
]

PER_SESSION = 3


def pick(rng: random.Random | None = None, count: int = PER_SESSION) -> list[int]:
    """Indexes of `count` different questions, in a random order (SystemRandom unless a test passes its own)."""
    rng = rng or random.SystemRandom()
    return rng.sample(range(len(QUESTIONS)), min(count, len(QUESTIONS)))
