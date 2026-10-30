# Ghost in the Reply: The Haunted Inbox

Wisp, the internal AI assistant at Hollowveil Memorial Hospital, drafts replies to patient portal messages.
One message looks like a normal reschedule request, but it hides lines that only the model reads ("ghosts").
The séance view reveals them. Warding Circle mode (`GUARD=1`) is closed by design for these two harms.

Built at Hacktoberfest Hack Day Brooklyn (MLH x GDG on Campus City Tech), October 30, 2026.

> Work in progress: hack day build.

## The two ghosts

- **Phishing:** the drafted reply picks up a fake insurance phone number.
- **Leak:** the assistant opens a different patient's chart: the kind of disclosure HIPAA exists to prevent.

## The possession meter

| Level | Meaning |
| --- | --- |
| Still | Clean run |
| Whispering | An attack was tried but blocked |
| Possessed | Harm happened |

## Run it

```bash
python main.py --guard 0|1 [--message ID] [--replay runs/FILE.json]
python -m pytest -q
```

## Safety notes

- All data is synthetic: fake patients, and phone numbers only in the 555-0100 to 555-0199 range.
- Wisp only drafts. Nothing is ever sent.

## Built with

- Python
- Ollama
- Gemma 4
- Rich
- pytest
- Claude Code

## Pre-event notes

`planning/`, `CLAUDE.md` and `.claude/agents/` were written before hacking started at 11:30 AM ET, as plans only.
No project code, fixtures or payloads existed before then.
