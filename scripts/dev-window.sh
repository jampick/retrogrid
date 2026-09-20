#!/bin/bash
# Open (or reopen) the console as a chromeless app window on a Hyprland
# workspace WITHOUT stealing focus. Usage: scripts/dev-window.sh [path] [workspace]
set -euo pipefail
URL="http://127.0.0.1:${RETROFFB_PORT:-8082}${1:-/}"
WS="${2:-4}"
PROFILE="$(cd "$(dirname "$0")/.." && pwd)/.chrome-profile"
pkill -f -- "--user-data-dir=$PROFILE" 2>/dev/null || true
sleep 0.5
hyprctl dispatch "hl.dsp.exec_cmd('chromium --user-data-dir=$PROFILE --no-first-run --disable-session-crashed-bubble --autoplay-policy=no-user-gesture-required --class=retroffb --app=$URL', { workspace = '$WS silent' })" >/dev/null
