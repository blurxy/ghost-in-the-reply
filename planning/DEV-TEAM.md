# Ghost in the Reply: Claude Code dev team runbook

Written Oct 30, before hacking began. Sources: Anthropic's Claude Code docs (subagents, agent teams, worktrees, best practices), Oct 2026.

## TL;DR

- **One lead session (you + Claude) builds the core.** Five subagents in `.claude/agents/` do side jobs in parallel.
- **Don't turn on agent teams for the build.** Anthropic's docs say teams fit independent, parallel work. Our build is a small codebase with steps that depend on each other, and teams cost much more in tokens.
- **Parallel without collisions = one owner per file** (table in CLAUDE.md). Use a second terminal in its own git worktree only for UI work.
- **Every claim needs evidence:** pytest output or a run's output. Tests never call Gemma.

## Sessions to open (3)

Open three Claude Code sessions in this folder, all in plan mode, named **lead**, **ui** and **review**. Paste the matching prompt from `planning/SESSION-PROMPTS.md` into each. Lead builds and calls ghost-writer and test-oracle; ui owns strip.py and ui.py; review plays red-team and pitch-checker and reviews diffs (the writer/reviewer pattern from Anthropic's best practices).

## The team

| Role | Who | Owns | Model | When |
| --- | --- | --- | --- | --- |
| **Lead** | You + main Claude session | main.py, ghostreply/harness.py, tools.py, templates.py, oracles.py, README | your default | All day |
| **ghost-writer** | Subagent | inbox/, records/ | Sonnet | Block 1, block 8 (tuning) |
| **seance-ui** | Subagent, or a 2nd session in a worktree | ghostreply/strip.py, ghostreply/ui.py | Sonnet | Block 3 |
| **test-oracle** | Subagent | tests/ | Sonnet | After blocks 2, 4, 5 |
| **red-team** | Subagent | tests/test_redteam.py | Same as lead, high effort | Block 7 |
| **pitch-checker** | Subagent, read-only | nothing | Haiku | Block 9 |

**Why subagents:** each one works in its own context window and hands back a summary, so your main session stays clean. Subagents started from the main conversation run in the background by default, so you keep working while they do.

## Before 11:30 (no code)

1. On the machine you'll build on, open a terminal in `hacktoberfest-2026` and run `claude` once to accept the folder trust prompt.
2. Start in plan mode: `claude --permission-mode plan`
3. Paste:

> Read CLAUDE.md and planning/DEV-TEAM.md. Don't create or edit any files: hacking starts at 11:30. Give me (1) the JSON schema for inbox/ and records/, (2) the function signatures for ghostreply/harness.py, tools.py, oracles.py and strip.py, (3) anything in CLAUDE.md that's unclear. Then wait.

4. Check the model: `ollama ps` (if the model runs on the other laptop, set `OLLAMA_HOST` to its address).

## Block-by-block prompts (copy and paste)

**Block 0, 11:30.** Leave plan mode (Shift+Tab), then:

> It's 11:30 and hacking has started. Run git init, commit the existing planning files as "Block 0: planning notes (written before the event)", then create a README.md skeleton with a "Built with" list (Python, Ollama, Gemma 4, Rich, pytest, Claude Code) and commit it. Then create a private GitHub repo with `gh repo create ghost-in-the-reply --private --source . --push` (if gh isn't installed, tell me).

**Block 1, 11:40.**

> Build the naive bot (GUARD=0) per CLAUDE.md: ghostreply/harness.py, ghostreply/tools.py, main.py, using the schema from your plan. At the same time, use the ghost-writer subagent to write inbox/ and records/ with that same schema. When both finish, run `python main.py --guard 0 --message <haunted id>` and show me the output. Commit as "Block 1".

**Blocks 2 + 3, 12:20.**

> Use the seance-ui subagent to build ghostreply/strip.py and ghostreply/ui.py. Meanwhile, add Ghost 2's phishing oracle to ghostreply/oracles.py. Then use the test-oracle subagent to test strip() and the phishing oracle. Show the pytest output, then commit.

Optional second session for UI instead of the subagent: open a new terminal and run `claude -w seance`, then give it the seance-ui prompt. It works in its own worktree under `.claude/worktrees/`. Merge with `git merge worktree-seance` when it's done.

**Solana lane (ui session, right after block 3, about 12:40-1:25, in parallel with blocks 4-5).**

> Build ghostreply/ledger.py per CLAUDE.md: hash each run record (guard, model, message id, verdict, oracle hits, timestamp) with SHA-256 and write the hash as a Memo on Solana devnet with the keypair at ~/.config/solana/ghost-ledger-devnet.json. Return the signature and explorer link. If devnet or the airdrop fails, append to runs/ledger-queue.jsonl and return 'queued'. Confirm the current client library and Memo program from official Solana docs first. Tests use a fake client; one live devnet write as evidence. Never put fixture text on-chain.

The lead wires ledger.py into the run log in block 7, and the Agent trace panel shows the explorer link.

**Block 4, 12:40.**

> Build GUARD=1 exactly as CLAUDE.md's "Capability-bound bot" section says. Then use test-oracle with a FakeModel that obeys every ghost: GUARD=0 must score Possessed; GUARD=1 must score Whispering on the haunted message (attack caught, no harm) and Still on clean ones. Show the pytest output, then commit.

**Block 5, 1:10.**

> Add Ghost 1 and the leak oracle. Run both guards on the haunted message 3 times each against the real model and show me the possession scores. Commit.

**Block 6, 1:25.** `git push`, then go to Jumu'ah.

**Block 7, 2:30.**

> Add `--replay`: save each run to runs/*.json and replay it without the model. At the same time, run the red-team subagent against GUARD=1. Show me only real breaks with failing tests.

**Block 8, 2:50.** Rehearse. If a ghost doesn't fire on GUARD=0:

> Use ghost-writer to make the ghosts blunter: keep them single-hop, pre-formed calls. Then run GUARD=0 five times and report how many fired.

**Block 9, 3:30.**

> Add the crisis tripwire per CLAUDE.md and a test for it. Then run the pitch-checker subagent on README.md and all user-facing strings and fix anything it flags.

## Effort per agent

| Agent | Effort | Why |
| --- | --- | --- |
| lead, ui, review sessions | xhigh (your default) | Multi-file design and review work |
| red-team | max | Adversarial reasoning; finding real breaks is the hardest job |
| seance-ui, test-oracle | high | Precise code and test logic |
| ghost-writer | medium | Fixture writing to a spec |
| pitch-checker, astra (relay) | low | Checklist and pass-through work; Astra itself runs at high in Codex |

## Switching accounts or devices without losing the thread

- **Every session has Remote Control on**, so the lead, ui and review sessions show up in the Claude app's **Code** tab on your phone and Mac. If omarchy sleeps, they reconnect when it wakes.
- **Hit a usage limit:** in that session run `/login`, sign in with your other Claude account, then `/remote-control` again. The conversation is stored on omarchy, so nothing is lost. A fresh session can also pick up with `claude --continue` in the folder.
- **Shared memory between sessions and accounts** is the repo itself: CLAUDE.md, planning/ and git. Commit after every block so any session or device can pull the latest state.

## Checking on the team

- `@agent-<name>` sends work to one subagent directly (for example `@agent-red-team`).
- `/tasks` lists background work, lets you open one and stop it.
- Esc stops Claude; Esc twice (or `/rewind`) rewinds to a checkpoint. Git is still the real undo.
- After two failed corrections on the same bug, `/clear` and re-prompt with what you learned.

## If a teammate joins

1. They clone the repo and run `claude -w <their-name>` so they get their own worktree and branch.
2. Give them **one** row of the ownership table (seance-ui is the easiest to hand over).
3. They merge into main at fixed times: 1:25 and 3:30.

## Optional: an agent team for the red-team review only (block 7)

Agent teams are experimental and off by default. If you have spare tokens, review work is the one place they shine:

```bash
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 claude
```

> Spawn 3 teammates using the red-team agent type: one attacks the leak path, one attacks the phishing path, one attacks input encoding (zero-width, tag block, bidi). Have them try to disprove each other's findings and report only breaks with failing tests.

Skip this if you're behind. It burns roughly one session's worth of tokens per teammate.

## Pitfalls (from Anthropic's docs)

- Two agents editing the same file overwrite each other. Stick to the ownership table.
- Subagents don't see your conversation. Put the details they need in the prompt.
- A reviewer asked to find gaps always finds some. Fix only breaks of the two harms.
- Running Ollama-heavy work from two sessions at once slows the GPU. Keep real-model runs to the lead.

## Sources

- [Claude Code docs: Create custom subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude Code docs: Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams)
- [Claude Code docs: Run parallel sessions with worktrees](https://code.claude.com/docs/en/worktrees)
- [Claude Code docs: Run agents in parallel](https://code.claude.com/docs/en/agents)
- [Claude Code docs: Best practices](https://code.claude.com/docs/en/best-practices)
- [Claude Help Center: Claude Code power user tips](https://support.claude.com/en/articles/14554000-claude-code-power-user-tips)
- [Addy Osmani: Claude Code agent teams](https://addyosmani.com/blog/claude-code-agent-teams/)
