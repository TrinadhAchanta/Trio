#!/usr/bin/env bash
# Starts the API (:8000) and the built web app (:3000) in the background for local testing.
# Usage: scripts/start-local.sh            (normal)
#        E2E=1 scripts/start-local.sh      (raises the sign-up rate limit for browser tests)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="${LOGS:-$ROOT/.logs}"; mkdir -p "$LOGS"
cd "$ROOT/backend"
if [ -n "${E2E:-}" ]; then export RATE_LIMIT_AUTH_PER_MINUTE=500; fi
.venv/bin/alembic upgrade head
nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$LOGS/api.log" 2>&1 < /dev/null &
cd "$ROOT/frontend"
[ -d .next ] || npm run build
nohup npx next start -p 3000 > "$LOGS/web.log" 2>&1 < /dev/null &
echo "API: http://localhost:8000/api/docs   Web: http://localhost:3000   Logs: $LOGS"
