# Team session prompts (paste one into each new Claude Code session)

Start every session in the hacktoberfest-2026 folder, in **plan mode** (read-only).
Before 11:30 nobody writes files: no repo, no code, no fixtures (MLH rule).
Name each session so the others can find it: lead, ui, review.

---

## Session 1: lead

You are the LEAD for Ghost in the Reply. Read CLAUDE.md, planning/DEV-TEAM.md and the "Build order" and
"Gaps closed by design" sections of planning/Ghost-in-the-Reply-Hack-Day-Game-Plan.md.
Do not create or edit any file before 11:30 AM ET.
In chat only, give me:
1. The JSON schema for inbox/*.json, records/*.json and records/hospital.json.
2. Function signatures (name, args, return) for ghostreply/harness.py, ghostreply/tools.py (naive + guarded),
   ghostreply/templates.py and ghostreply/oracles.py, plus the run-log JSON shape for --replay.
3. The exact interface you need from the ui session: strip(text) -> (clean, removed) and the ui.py functions.
4. Anything in CLAUDE.md that is unclear or contradictory.
Then wait. At 11:30 I'll tell you to start block 0.

## Session 2: ui

You are SEANCE-UI for Ghost in the Reply. Read CLAUDE.md and .claude/agents/seance-ui.md and follow that role.
Do not create or edit any file before 11:30 AM ET. You own only ghostreply/strip.py and ghostreply/ui.py.
In chat only, give me:
1. The strip() algorithm step by step, including how you record removed spans for the seance view.
2. A list of test inputs strip() must handle (display:none span, zero-width chars, Unicode tag block
   U+E0000-E007F, bidi controls, normal accented text that must survive NFKC).
3. A text sketch of the projector layout: rendered view, seance view, possession meter, GUARD banner.
Then wait. After block 0 is committed (about 11:40) I'll restart you in your own worktree.

## Session 3: review

You are the REVIEWER for Ghost in the Reply: you play red-team and pitch-checker
(.claude/agents/red-team.md and .claude/agents/pitch-checker.md). You stay read-only all day except
tests/test_redteam.py after 2:30 PM.
Do not create or edit any file before 11:30 AM ET.
In chat only, give me:
1. An attack checklist for GUARD=1 built from the 9 rows of "Gaps closed by design" in
   planning/Ghost-in-the-Reply-Hack-Day-Game-Plan.md, assuming the model obeys every hidden instruction.
2. The README checklist: "Built with" list, synthetic-data statement, banned and required phrases.
3. The 3 questions a skeptical judge is most likely to ask that the Judge Q&A doesn't cover yet.
Then wait. I'll send you diffs to review after each block.
