#!/bin/bash
# LIVE mode: today's real NFL games off ESPN. Rebuilds today's slate + league,
# then serves the console (no --reload: a restart mid-game costs a re-prime).
set -euo pipefail
cd "$(dirname "$0")/.."
RETROFFB_SEASON="${RETROFFB_SEASON:-$(date +%Y)}" .venv/bin/python scripts/fetch_nflverse.py
[ "${1:-}" = "--keep" ] || .venv/bin/python scripts/build_live.py
.venv/bin/python scripts/build_sprites.py --live
npm run --silent build
RETROFFB_LIVE=1 PYTHONPATH=backend exec .venv/bin/uvicorn retroffb.server:app --host 127.0.0.1 --port "${RETROFFB_PORT:-8082}"
