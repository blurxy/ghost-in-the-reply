# Inside view toggle + Astra's visual fixes (Rafi, Oct 30, 3:05 PM)

Rafi: "show a regular inbox first and press a button that then toggles everything that's going on on the inside."
The design is GPT-6 Astra's (brief /tmp/astra/brief2.md, answer /tmp/astra/out2.md; it reviewed screenshots of the restyle).

## Ordinary view (the default on screens 1, 3, 4)
- It looks like a normal AI-assisted product.
- No step headline, no verdict banner, no meter, no trace drawer, no highlights.
- On screen 3 (guard off), Wisp's draft sits in the reply composer under "Review Wisp's draft" in normal body text. The leak is in there, unmarked, exactly as a staff member would see it before pressing Send.
- On screen 4 (guard on), the template reply sits there the same way.

## Inside view: label "Show inside" / "Hide inside", key I
- **Position:** the top bar, right, immediately left of the REPLAY/LIVE badge. Reserve its space so nothing shifts. The badge stays visible in both views.
- **Key:** I toggles it, except when focus is in an input or textarea (the sandbox).
- **Sequence (Astra's timings):**
  - 0-150 ms: the hidden text appears in the message as a labeled "Hidden text" block. In guard-on mode, content strip() removed is marked "Removed before model".
  - 150-300 ms: an inspector opens with tabs: Model request · Model response · Records touched · Verdict.
  - 300-450 ms: the copied fields and the phishing number get highlighted in the draft, and the verdict shows.
- **Transition:** a crossfade with panels resizing, and the draft keeps its scroll position. Reduced motion switches instantly. At 720p only one inspector tab shows at a time.
- **Raw JSON, verbatim and never fabricated:**
  - Model request: the system prompt and messages (run["transcript"]), the tool list (NAIVE_TOOLS, guard off) or "tools: none" plus the format schema (INTENT_SCHEMA, guard on), the model name and options.
  - Model response: run["turns"][*].response verbatim, including the raw `tool_calls` with arguments as returned.
  - Show it in a selectable, scrollable monospace viewer with a "Copy raw JSON" button.
  - These bodies match what demo/raw/make_requests.py sends with curl, which is the bridge to the terminal demo.
- **Records touched:** each record ID, and whether it belongs to the sender (green) or not (red).
- **Verdict:** the text verdict (below), plus "Warding Circle mode: On / Off".
- **Exit:** I again restores the ordinary view and keeps the draft, the step, the mode and the replay position.

## Astra's visual fixes (apply them too)
1. Ordinary view as above: no oversized headline or verdict banner, and the draft in the composer in normal text with no coloured border or highlights.
2. Message and draft text `clamp(20px, 1.4vw, 27px)`, secondary text at least 18 px, line-height 1.4. Scroll instead of shrinking the text.
3. Below 1500 px wide: collapse the folders to a 64 px icon rail and hide the message list while a message is open.
4. Verdict findings as three rows, "Patient records accessed", "Fields copied" and "Contact added", each value shown once, replacing the six repeated red lines.
5. Replace the unlabeled coloured bars with a text verdict, "Wisp: Possessed" / "Wisp: Whispering" / "Wisp: Still", with "Warding Circle mode: On / Off" beside it, inside the inside view.

## Order and checks
- Merge the portal restyle first if it isn't merged yet, then build this on top.
- Owner: ui (page.html and viewmodel fields for the raw request and response). Tests: the inside-view JSON equals the run log fields byte for byte; there's no fabricated tool_calls when guard is on; the I key is ignored inside inputs.
- Re-export Pages after the merge. The static site gets the toggle too, with replay data only.
- The freeze is still Rafi's call (he presents later). Target: merged by 3:40.

## Update 3:12 PM: the Inside view is a ROLE SWITCH (Rafi)
Rafi: "now if you were an admin or part of the IT / security team, you would be able to see everything." So the button is a change of who's looking, not a debug toggle. Same key I, same content as above.
- **Top bar:** the user chip reads "J. Rivera · Front desk" (the ordinary view). Pressing I, or clicking the chip, switches to "Security team · Audit view" and back. The button label becomes "View as: Security team" / "View as: Front desk".
- **Security view header:** "Lantern Audit · Wisp activity", plus one alert line:
  - Guard off: severity HIGH, "Wisp opened chart p-1031, which isn't linked to this message, and copied its fields into a draft."
  - Guard on: severity INFO, "Hidden instruction removed before the model; Wisp touched only the sender's chart."
  - Build the alert from the verdict and events, never hard-coded.
- **Audit log table:** one row per run event: time, actor (Wisp), action (the tool call as made), record, linked to the sender (yes/no, green/red), result (allowed/denied). It comes from run["events"] verbatim.
- The tabs stay as specced: Model request · Model response (raw JSON, verbatim) · Records touched · Verdict.
- No compliance claims on screen. The HIPAA audit-controls point belongs in Rafi's spoken line, not the UI.
