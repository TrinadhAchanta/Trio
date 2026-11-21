#!/usr/bin/env bash
# Writes a source archive (with git history, without dependencies/build output) to $1.
set -euo pipefail
OUT="${1:?usage: scripts/package.sh <output.tar.gz>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$(dirname "$ROOT")"
tar --exclude=node_modules --exclude=.venv --exclude=.next --exclude=test-results --exclude=__pycache__ \
    --exclude=.pytest_cache --exclude=.ruff_cache --exclude=backend/data --exclude=.logs \
    -czf "$OUT" "$(basename "$ROOT")"
