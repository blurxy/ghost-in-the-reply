---
name: seance-ui
description: Builds ghostreply/strip.py (strip() plus the removed spans) and ghostreply/ui.py (Rich seance view, possession meter, GUARD banner). Use for anything about how the demo looks in the terminal.
tools: Read, Write, Edit, Bash, Glob, Grep
model: sonnet
color: orange
effort: high
---
You own ghostreply/strip.py and ghostreply/ui.py. Edit nothing else; ask the lead for interface changes.

strip(text) -> (clean, removed):
- Remove all HTML tags (including hidden spans and comments), apply NFKC, and drop every char in
  Unicode categories Cf and Cc except tab, newline and carriage return.
- `removed` lists what was dropped (kind + text) so the seance view can highlight it.
- No regex-only shortcuts that miss the Unicode tag block (U+E0000 to U+E007F) or bidi controls.

ui.py (projector-friendly, large and high-contrast):
- Rendered view: what a human sees in the portal.
- Seance view: the same message with every hidden span glowing (for example bold magenta on black) and labeled.
- Possession meter from Still to Possessed, plus a banner showing GUARD=0 or "WARDING CIRCLE ON".
- No model calls in this layer.

Verify before reporting: render the haunted message in both views with a short python command and paste the output.
