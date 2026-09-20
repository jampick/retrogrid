#!/bin/bash
# LIVE mode from a checkout: build the frontend, then hand over to the CLI
# (which fetches rosters/stats, builds today's slate + sprites, and serves).
set -euo pipefail
cd "$(dirname "$0")/.."
npm run --silent build
exec .venv/bin/retrogrid live --no-window "$@"
