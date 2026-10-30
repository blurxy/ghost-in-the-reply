# Demo dashboard spec (from Rafi via Cowork Claude, Oct 30, 12:40)

## Goal
One screen runs the 4:00 demo. Rafi only presses → (or a clicker). The screen carries the story with big captions, so the demo never depends on how well he delivers it. If a live run is slow or fails, the dashboard switches to the saved real run and says so on screen.

## Owner and boundaries
- Owner: the ui session. Put everything in a new `dashboard/` package.
- Read-only use of ghostreply (harness.run_message, load_run, strip, the oracles' verdicts). No changes to the guard, the oracles or the fixtures.
- Stack:
  - Python stdlib only (http.server, json, threading).
  - One HTML file with inline CSS and JS.
  - No CDN or web fonts, so it works with no Wi-Fi.
  - No new pip dependencies.
- Run commands:
  - `python -m dashboard` serves http://127.0.0.1:8765/ (audience) and /presenter.
  - `--host <tailnet IP>` lets the Mac open it. The default is 127.0.0.1 only, which keeps it off the venue Wi-Fi.
  - `--replay-only` never calls Ollama, so it runs on the Mac or when the GPU is busy.

## Views
- **Audience view (/):** the projector screen.
  - 1920x1080 and 1280x720 with no scrollbars.
  - Body text at least 28px, headlines at least 64px, dark background, one step at a time.
- **Presenter view (/presenter):** Rafi's screen.
  - Shows the current step, the next step, the line to say in big teleprompter text, a timer, the step list and the preflight panel.
  - Buttons: back, next, rerun, live/replay.
  - Stays in sync with the audience view through server state (poll /api/state about every 300 ms, or SSE).
- **Keys on both views:**

  | Key | Action |
  |---|---|
  | → / Space / PageDown | next |
  | ← / PageUp | back |
  | R | rerun |
  | L | toggle live/replay |
  | F | fullscreen |
  | B | play the backup video, if dashboard/backup.mp4 exists |

## Steps
Each step has a caption (audience view) and a line to say (presenter view).
0. **Title:** "Ghost in the Reply", with the line "A hidden line in a patient message vs. a hospital's AI assistant" and "Gemma 4 12B, running on this laptop".
1. **Hook.**
   - Line: "Hospitals are starting to let AI draft replies to patient messages. One hidden line in a message can make that AI leak another patient's chart. Watch."
   - Screen: msg-004 as staff see it in the portal inbox (rendered and folded like ui's portal preview).
2. **Reveal.**
   - Line: "This looks like a normal reschedule request. Here's what the AI actually reads."
   - Screen: animate from the rendered view to the seance view. The hidden text fades in, glowing, labeled "hidden from people, readable by the model".
3. **Possessed (GUARD=0).**
   - Line: "This is Wisp, the hospital's assistant, with normal tool access."
   - Screen, in order:
     - Agent trace rows appear one by one: lookup_patient("Casper Ghostly") touches chart p-1031, which is not the sender's.
     - The draft appears, with the fake phone number and Casper's DOB and MRN highlighted.
     - The meter slams to POSSESSED, with its red lines.
4. **Whispering (GUARD=1).**
   - Line: "Same message, same model, Warding Circle mode."
   - Screen, in order:
     - The model's only output: the JSON {intent, proposed_time, needs_clinician}.
     - Code looks up only the sender, and the reply comes from a template.
     - The meter goes to WHISPERING, captioned "It heard the ghost. It couldn't act."
5. **Why it works.** Three boxes: the model only classifies (JSON, no tools) → code decides whose chart (the sender's only) → the reply comes from hospital templates.
   - Line: "The model can still be fooled. It just has nothing to act with. Closed by design for these two harms."
6. **Closer.**
   - Line: "The model may be possessed. The system doesn't have to be."
   - Screen: the repo URL as text, plus "Built with Gemma 4 · Ollama · Python · Claude Code".

## Live vs replay (the part that matters most)
- **Modes:** run steps (3, 4) are LIVE (run_message on the real model) or REPLAY (runs/demo/msg-004-guard0.json and guard1.json).
- **Precompute:** when step 0 opens, start both live runs in the background, one after the other (GUARD=0, then GUARD=1). They finish long before step 3. The reveal animations are the same in both modes.
- **Fallback:** if GUARD=0 takes more than 40 s, GUARD=1 takes more than 25 s (measured: ~19 s and ~5 s), or anything throws, show the replay instead.
- **Badge:** always visible on the audience view.
  - Live: "LIVE · Gemma 4 12B · 5.0 s".
  - Replay: "REPLAY of a real Gemma 4 run · 12:27".
  - Never label a replay as live.
- **Wrong result:** if a live run gives the wrong level (GUARD=0 not possessed, or GUARD=1 not whispering), the presenter view shows a yellow warning and offers "Use replay" (key L). The audience view shows whichever is chosen, with an honest badge.
- **Saving:** live runs from the dashboard save to runs/ like any run, never to runs/demo/.

## Preflight panel (presenter view; green or red pills, checked automatically on load)
- Ollama is reachable and gemma4-12b-agent is loaded; warm it with a 1-token call.
- The power profile is performance (`powerprofilesctl get`; skip if that's unavailable).
- runs/demo/*.json exist, their verdicts are possessed and whispering, and their started_at times are shown.
- HEAD is pushed (informational).

## Autoplay (for the backup video)
- `/?autoplay=1&step_ms=<ms>` advances through every step on its own, in replay mode, with fixed-duration animations, so the result is the same every take.
- Cowork Claude films the backup video from this at about 3:15, so keep step timing deterministic.

## Acceptance (show the evidence to the operator)
1. With Ollama stopped, `python -m dashboard --replay-only` renders all 7 steps, and every badge says REPLAY.
2. On omarchy, steps 3 and 4 show LIVE badges with the right levels.
3. Forced fallback (a nonexistent model name, or a 1 s timeout): step 3 shows the replay with the REPLAY badge and never stalls longer than the timeout.
4. At 1920x1080 and at 1280x720 there are no scrollbars and nothing is cut off.
5. Unit tests cover the state API and the fallback logic (no Ollama), and the full suite stays green.
6. Every caption follows the CLAUDE.md wording rules:
   - "closed by design for these two harms", never "unbreakable".
   - Never "HIPAA violation".
   - The fixtures are synthetic; never call them PHI.

   Run pitch-checker on the step text.

## Timebox and cuts
- **By the 1:25 push:** the dashboard works in replay mode.
- **2:30-3:15:** live mode, fallback, preflight and polish.
- **Cut order:** preflight extras → step 5's diagram → presenter sync (falls back to one page with notes hidden).
- **Never cut:** the live/replay badge, the fallback, steps 1-4.
