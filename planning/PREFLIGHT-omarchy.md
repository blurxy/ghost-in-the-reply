# Preflight on omarchy (do before 11:30; tooling only, no project code)

## 1. Copy this folder from the Mac to omarchy (pick one)

On the Mac, over your tailnet:

    rsync -av --exclude _to_delete ~/hacktoberfest-2026/ omarchy:~/hacktoberfest-2026/

If SSH to omarchy isn't set up, use Taildrop instead:

    cd ~ && zip -r hacktoberfest-2026.zip hacktoberfest-2026 -x 'hacktoberfest-2026/_to_delete/*'
    tailscale file cp hacktoberfest-2026.zip omarchy:
    # then on omarchy:
    tailscale file get ~/ && cd ~ && unzip hacktoberfest-2026.zip

## 2. Checks on omarchy (each should pass)

    nvidia-smi                              # RTX 5050 listed
    ollama list | grep gemma4-12b-agent     # model built from the Modelfile
    ollama run gemma4-12b-agent "say ready" # warm-up
    ollama ps                               # PROCESSOR 100% GPU, CONTEXT 8192
    python3 --version                       # 3.11+
    claude --version                        # Claude Code installed and logged in
    gh auth status                          # needed for block 0 (private repo)

## 3. Python packages (installs are allowed before the event)

    cd ~/hacktoberfest-2026
    python3 -m venv .venv && source .venv/bin/activate
    pip install ollama rich pytest sentry-sdk

## 4. Open the three team sessions (plan mode, nothing gets written)

Open three terminal windows (Hyprland tiles them). In each:

    cd ~/hacktoberfest-2026 && claude --permission-mode plan

Accept the folder trust prompt the first time. Then name the session with /rename lead, /rename ui
or /rename review, and paste its prompt from planning/SESSION-PROMPTS.md.

## 5. At 11:30

In the lead session: Shift+Tab out of plan mode, then paste the Block 0 prompt from planning/DEV-TEAM.md.
