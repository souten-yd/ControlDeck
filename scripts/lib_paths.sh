#!/usr/bin/env bash
# backup/restore 共通: リポジトリルートと data_dir を解決する。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"

resolve_data_dir() {
  # config.py の解決ロジックを利用して正確な data_dir を得る
  if [ -x "$VENV/bin/python" ]; then
    (cd "$REPO_ROOT/backend" && CONTROL_DECK_CONFIG="${CONTROL_DECK_CONFIG:-$REPO_ROOT/config/config.yaml}" \
      "$VENV/bin/python" -c "from app.config import data_dir; print(data_dir())" 2>/dev/null) && return
  fi
  echo "$HOME/.local/share/control-deck"
}
