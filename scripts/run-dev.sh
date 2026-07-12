#!/usr/bin/env bash
# 開発起動。venv がなければ起動時に自動構築する。
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

refuse_root
ensure_venv

HOST="${CONTROL_DECK_HOST:-}"
PORT="${CONTROL_DECK_PORT:-}"
ARGS=()
[ -n "$HOST" ] && ARGS+=(--host "$HOST")
[ -n "$PORT" ] && ARGS+=(--port "$PORT")

cd "$REPO_ROOT/backend"
exec "$VENV/bin/python" -m uvicorn app.main:app \
  --host "${HOST:-127.0.0.1}" --port "${PORT:-8765}" "$@"
