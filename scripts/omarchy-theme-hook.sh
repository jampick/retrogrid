#!/bin/bash
# OPTIONAL. Makes the console retune the instant the Omarchy theme changes,
# instead of on the next 1s poll. Install (your call, never done for you):
#   omarchy hook install theme-set scripts/omarchy-theme-hook.sh
curl -fsS -m 2 -X POST "http://127.0.0.1:${RETROGRID_PORT:-8082}/api/theme/poke" >/dev/null 2>&1 || true
