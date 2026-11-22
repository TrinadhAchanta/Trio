#!/bin/sh
# Entry point for the single-container image: migrate, start the API privately, serve the web app publicly.
set -e
if [ -z "$DATABASE_URL" ]; then
  echo "ERROR: DATABASE_URL is not set. Add your PostgreSQL connection URL in the service's environment settings." >&2
  exit 1
fi
cd /app/backend
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1 &
API_PID=$!
cd /app/web
PORT="${PORT:-10000}" HOSTNAME=0.0.0.0 node server.js &
WEB_PID=$!
# If either process exits, stop the container so the host restarts it.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do sleep 5; done
kill "$API_PID" "$WEB_PID" 2>/dev/null || true
exit 1
