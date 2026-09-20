#!/bin/bash
# Backend with reload + esbuild watch. Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."
npm run --silent watch &
trap 'kill %1 2>/dev/null' EXIT
.venv/bin/uvicorn retrogrid.server:app --host 127.0.0.1 --port "${RETROGRID_PORT:-8082}" --reload --reload-dir backend
