#!/usr/bin/env bash
# Switch the projector between the terminal and the dashboard (Hyprland workspaces). Called only by the dashboard's
# allowlisted /api/stage endpoint, or by hand:
#   bash demo/raw/stage.sh show-terminal | show-dashboard
# The workspace numbers come from demo/raw/stage.env (set at stage prep, not committed), for example:
#   TERMINAL_WS=3
#   DASHBOARD_WS=2
# stage.env is parsed for digits only, never sourced, so nothing in it can run as code.
set -euo pipefail
cd "$(dirname "$0")"
ws() { [ -f stage.env ] && sed -n "s/^$1=\([0-9]\{1,2\}\)[[:space:]]*$/\1/p" stage.env | tail -1; }
TERMINAL_WS="$(ws TERMINAL_WS || true)"
DASHBOARD_WS="$(ws DASHBOARD_WS || true)"
case "${1:-}" in
  show-terminal)  [ -n "$TERMINAL_WS" ]  || { echo "set TERMINAL_WS=<n> in demo/raw/stage.env" >&2; exit 3; }
                  exec hyprctl dispatch workspace "$TERMINAL_WS" ;;
  show-dashboard) [ -n "$DASHBOARD_WS" ] || { echo "set DASHBOARD_WS=<n> in demo/raw/stage.env" >&2; exit 3; }
                  exec hyprctl dispatch workspace "$DASHBOARD_WS" ;;
  *) echo "usage: bash demo/raw/stage.sh show-terminal|show-dashboard" >&2; exit 2 ;;
esac
