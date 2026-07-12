#!/usr/bin/env bash
# 共通処理: venv の自動構築（起動時に毎回呼ばれても高速に済むよう冪等）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

die() { echo "エラー: $*" >&2; exit 1; }

refuse_root() {
  if [ "$(id -u)" -eq 0 ]; then
    die "root で実行しないでください（docs/security-model.md 参照）"
  fi
}

ensure_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    echo "venv を構築しています ($VENV) ..."
    "$PYTHON_BIN" -m venv "$VENV" || die "venv の作成に失敗しました。'sudo apt install python3-venv' を確認してください"
  fi
  # 依存の同期（requirements のハッシュが変わったときのみ再インストール）
  local req="$REPO_ROOT/backend/requirements.txt"
  local stamp="$VENV/.req-stamp"
  local current
  current="$(sha256sum "$req" | cut -d' ' -f1)"
  if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$current" ]; then
    echo "Python 依存をインストールしています ..."
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$req"
    echo "$current" > "$stamp"
  fi
}
