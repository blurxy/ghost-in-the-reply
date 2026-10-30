#!/usr/bin/env bash
# Raw demo: stock Gemma 4 through Ollama, plain terminal, nothing hidden. Run from the repo root:
#   bash demo/raw/demo.sh 0    `ollama ps`: the stock model, loaded on this laptop's GPU
#   bash demo/raw/demo.sh 1    plain `ollama run`: the model reads the raw message and drafts a reply
#   bash demo/raw/demo.sh 2    guard off, raw curl to /api/chat with three tools: look at tool_calls
#   bash demo/raw/demo.sh 3    guard on, raw curl: no tools, format= schema: look at the only output
#   bash demo/raw/demo.sh 4    the real loop executes the tools: python main.py, guard off then on
#   bash demo/raw/demo.sh 5    the test suite (no model)
#   bash demo/raw/demo.sh all  everything, pausing between steps
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
MODEL="${GHOST_MODEL:-gemma4-12b-agent}"
HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"; [[ "$HOST" == *://* ]] || HOST="http://$HOST"
OUT=demo/raw/out
say() { printf '\n\033[1;35m$ %s\033[0m\n' "$*"; }
pause() { [ "${STEP:-}" = all ] && { read -r -p $'\n[enter for the next step] ' _; } || true; }

prep() { GHOST_MODEL="$MODEL" "$PY" demo/raw/make_requests.py msg-004 >/dev/null; }

step0() {
  say "ollama ps"
  ollama ps
}
step1() {
  prep
  say "ollama run $MODEL --think=false \"\$(cat $OUT/chat-prompt.txt)\""
  ollama run "$MODEL" --think=false --nowordwrap "$(cat "$OUT/chat-prompt.txt")"
}
step2() {
  prep
  say "jq '.tools[].function.name' $OUT/req-guard0.json"
  jq -r '.tools[].function.name' "$OUT/req-guard0.json"
  say "curl -s $HOST/api/chat -d @$OUT/req-guard0.json | jq '.message | {content, tool_calls}'"
  curl -s "$HOST/api/chat" -d @"$OUT/req-guard0.json" | jq '.message | {content, tool_calls}'
}
step3() {
  prep
  say "jq '{tools, format}' $OUT/req-guard1.json"
  jq '{tools: (.tools // "none"), format}' "$OUT/req-guard1.json"
  say "curl -s $HOST/api/chat -d @$OUT/req-guard1.json | jq -r .message.content | jq ."
  curl -s "$HOST/api/chat" -d @"$OUT/req-guard1.json" | jq -r .message.content | jq .
}
step4() {
  say "$PY main.py --guard 0 --message msg-004 --no-save"
  GHOST_MODEL="$MODEL" "$PY" main.py --guard 0 --message msg-004 --no-save
  pause
  say "$PY main.py --guard 1 --message msg-004 --no-save"
  GHOST_MODEL="$MODEL" "$PY" main.py --guard 1 --message msg-004 --no-save
}
step5() {
  say "$PY -m pytest -q"
  "$PY" -m pytest -q 2>&1 | tail -3
}

STEP="${1:-all}"
case "$STEP" in
  0) step0 ;; 1) step1 ;; 2) step2 ;; 3) step3 ;; 4) step4 ;; 5) step5 ;;
  all) step1; pause; step2; pause; step3; pause; step4; pause; step5 ;;
  *) echo "usage: bash demo/raw/demo.sh 0|1|2|3|4|5|all" >&2; exit 2 ;;
esac
