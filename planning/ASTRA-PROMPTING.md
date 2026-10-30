# How we prompt GPT-6 Astra (from OpenAI's official docs, read Oct 30 2026)

Sources:
- Using GPT-6: https://developers.openai.com/api/docs/guides/latest-model
- Rethinking skills and prompts for GPT-6 Astra: https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra
- Prompt engineering: https://developers.openai.com/api/docs/guides/prompt-engineering

## What the docs say, applied to us
1. **Goal plus a definition of done, little scaffolding.** Astra is a reasoning model ("give them a goal... trust them to work out the details"), and step-by-step recipes and strict boundary language can make it stop early. We state the goal and what "done" looks like (for example "3 options per task, then a pick with a one-line reason").
2. **Sections:** Identity, Goal, Tasks, Constraints, Output, then Context last, inside <context> tags. The docs put variable context near the end, Markdown headers for sections and XML tags for documents.
3. **Say where exploration stops.** For creative work, give the number of options and the word limit.
4. **Style:** concise paragraphs, plain language, active voice, and lists only for parallel items. We repeat the docs' slop list in the brief: no "delve", "leverage", "foster", "importantly", "genuinely", no "X, not Y" contrast lines, no invented compound labels, no "In short" closers.
5. **Reasoning effort:** Astra does not support `none`. We use `high` for creative and strategy work (`-c model_reasoning_effort=high`). Temperature is not used with reasoning.
6. **Instruction files:** Astra is sensitive to AGENTS.md and skill files, and the user's instructions take precedence. We run it from /tmp with `--skip-git-repo-check`, and we embed the context in the brief, so it never picks up repo instruction files or wanders the repo.
7. **Ask for final copy, not descriptions of copy,** when the words go straight into the product.

## Command
```
cd /tmp/astra && codex exec -m gpt-6-astra -c model_reasoning_effort=high --sandbox read-only --skip-git-repo-check "$(cat brief.md)" > out.md
```
Worked example: /tmp/astra/brief.md and out.md from 2:52 PM (portal identity, pitch lines, sandbox copy). It took about 2 minutes and 25k tokens.
