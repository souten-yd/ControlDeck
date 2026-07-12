#!/usr/bin/env bash
# ============================================================
# Ubuntu Control Deck — 唯一のエントリースクリプト
#
#   ./deck.sh              状態を自動判定 → 不足があればセットアップ → 起動
#   ./deck.sh service      systemd ユーザーサービスとして登録・起動（OS 起動時に自動起動）
#   ./deck.sh stop         サービス停止
#   ./deck.sh status       サービス状態表示
#   ./deck.sh admin <名前> 管理者ユーザー作成
#   ./deck.sh test         バックエンドテスト実行
#
# 初回でも 2 回目以降でも同じように実行するだけでよい。
# 不足要素（venv / Node 依存 / フロントエンドビルド / 設定 / linger / 管理者）は
# スクリプト側が検出して自動的に整える。
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_ROOT/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE=control-deck-web

info() { echo -e "\033[36m[deck]\033[0m $*"; }
warn() { echo -e "\033[33m[deck] 警告:\033[0m $*" >&2; }
die()  { echo -e "\033[31m[deck] エラー:\033[0m $*" >&2; exit 1; }

# ---------- 個別チェック（すべて冪等） ----------

check_root() {
  [ "$(id -u)" -ne 0 ] || die "root で実行しないでください（docs/security-model.md 参照）"
}

check_python() {
  command -v "$PYTHON_BIN" >/dev/null || die "python3 が見つかりません: sudo apt install python3 python3-venv"
  "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11 以上が必要です（現在: $($PYTHON_BIN --version)）"
}

ensure_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    info "Python 仮想環境を構築しています (.venv) ..."
    "$PYTHON_BIN" -m venv "$VENV" || die "venv 作成失敗: sudo apt install python3-venv"
  fi
  local req="$REPO_ROOT/backend/requirements.txt"
  local stamp="$VENV/.req-stamp"
  local current
  current="$(sha256sum "$req" | cut -d' ' -f1)"
  if [ ! -f "$stamp" ] || [ "$(cat "$stamp")" != "$current" ]; then
    info "Python 依存をインストールしています ..."
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$req"
    echo "$current" > "$stamp"
  fi
}

ensure_frontend() {
  local dist="$REPO_ROOT/frontend/dist/index.html"
  local need_build=0
  if [ ! -f "$dist" ]; then
    need_build=1
  elif [ -n "$(find "$REPO_ROOT/frontend/src" "$REPO_ROOT/frontend/package.json" \
               -newer "$dist" -print -quit 2>/dev/null)" ]; then
    need_build=1
  fi
  if [ "$need_build" -eq 1 ]; then
    command -v npm >/dev/null || die "フロントエンドのビルドに npm が必要です: sudo apt install nodejs npm"
    if [ ! -d "$REPO_ROOT/frontend/node_modules" ]; then
      info "フロントエンド依存をインストールしています ..."
      (cd "$REPO_ROOT/frontend" && npm install --silent --no-fund --no-audit)
    fi
    info "フロントエンドをビルドしています ..."
    (cd "$REPO_ROOT/frontend" && npm run build --silent)
  fi
}

ensure_config() {
  if [ ! -f "$REPO_ROOT/config/config.yaml" ]; then
    mkdir -p "$REPO_ROOT/config"
    cp "$REPO_ROOT/config/config.example.yaml" "$REPO_ROOT/config/config.yaml"
    info "config/config.yaml を作成しました（初期値: 127.0.0.1:8765）"
  fi
}

ensure_linger() {
  command -v loginctl >/dev/null || return 0
  if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q "Linger=yes"; then
    info "SSH 切断後もアプリを継続実行するため linger を有効化します ..."
    loginctl enable-linger "$USER" || warn "linger を設定できませんでした"
  fi
}

ensure_admin() {
  local count
  count="$(cd "$REPO_ROOT/backend" && "$VENV/bin/python" -c "
from app.bootstrap import init_db
from app.database import SessionLocal
from app.models import User
init_db()
db = SessionLocal()
print(db.query(User).count())
db.close()" 2>/dev/null || echo "?")"
  if [ "$count" = "0" ]; then
    if [ -t 0 ]; then
      info "ユーザーが存在しません。管理者を作成します。"
      read -rp "管理者ユーザー名: " admin_name
      (cd "$REPO_ROOT/backend" && "$VENV/bin/python" -m app.cli create-admin "$admin_name")
    else
      warn "ユーザーが存在しません。後で './deck.sh admin <名前>' で管理者を作成してください"
    fi
  fi
}

check_tmux() {
  command -v tmux >/dev/null \
    || warn "tmux が未インストールです。Web ターミナルのセッション永続化には 'sudo apt install tmux' を推奨します"
}

# apt パッケージを可能なら自動導入（パスワード不要 sudo のときのみ。無理なら案内）
ensure_apt_packages() {
  local missing=()
  command -v tmux >/dev/null || missing+=(tmux)
  command -v tesseract >/dev/null || missing+=(tesseract-ocr tesseract-ocr-jpn)
  [ ${#missing[@]} -eq 0 ] && return 0
  if command -v apt-get >/dev/null && sudo -n true 2>/dev/null; then
    info "システムパッケージを導入しています: ${missing[*]}"
    sudo -n apt-get update -qq && sudo -n apt-get install -y -qq "${missing[@]}" \
      || warn "一部パッケージの導入に失敗しました: ${missing[*]}"
  else
    warn "以下のワークフロー用パッケージが未導入です（任意）: ${missing[*]}"
    warn "  導入するには: sudo apt install ${missing[*]}"
  fi
}

# Playwright のブラウザ本体（OCR/ブラウザ操作ノード用、~/.cache へ。sudo 不要）
ensure_playwright_browser() {
  local stamp="$VENV/.playwright-stamp"
  [ -f "$stamp" ] && return 0
  if "$VENV/bin/python" -c "import playwright" 2>/dev/null; then
    info "Playwright ブラウザ（Chromium）を導入しています ..."
    if "$VENV/bin/python" -m playwright install chromium >/dev/null 2>&1; then
      touch "$stamp"
    else
      warn "Playwright ブラウザの導入に失敗しました。ブラウザ操作ノードは利用できません"
    fi
  fi
}

ensure_ready() {
  check_root
  check_python
  ensure_venv
  ensure_config
  ensure_frontend
  ensure_linger
  ensure_admin
  ensure_apt_packages
  ensure_playwright_browser
}

service_installed() {
  systemctl --user list-unit-files "$SERVICE.service" 2>/dev/null | grep -q "$SERVICE"
}

# ---------- コマンド ----------

cmd_start() {
  ensure_ready
  if service_installed && systemctl --user is-enabled --quiet "$SERVICE" 2>/dev/null; then
    info "サービスが登録済みのため再起動して反映します"
    systemctl --user restart "$SERVICE"
    sleep 2
    systemctl --user --no-pager --lines=3 status "$SERVICE" || true
    info "URL: http://127.0.0.1:8765"
  else
    info "フォアグラウンドで起動します（サービス化するには ./deck.sh service）"
    cd "$REPO_ROOT/backend"
    exec "$VENV/bin/python" -m uvicorn app.main:app \
      --host "${CONTROL_DECK_HOST:-127.0.0.1}" --port "${CONTROL_DECK_PORT:-8765}"
  fi
}

cmd_service() {
  ensure_ready
  local unit_dir="$HOME/.config/systemd/user"
  mkdir -p "$unit_dir"
  sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" \
      "$REPO_ROOT/deploy/systemd/control-deck-web.service.in" > "$unit_dir/$SERVICE.service"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE.service"
  sleep 1
  systemctl --user --no-pager --lines=3 status "$SERVICE" || true
  info "登録完了。OS 起動時に自動起動します。URL: http://127.0.0.1:8765"
}

cmd_stop() {
  systemctl --user stop "$SERVICE" 2>/dev/null && info "停止しました" || warn "サービスは動作していません"
}

cmd_status() {
  if service_installed; then
    systemctl --user --no-pager status "$SERVICE" || true
  else
    info "サービス未登録（./deck.sh service で登録できます）"
    pgrep -af "uvicorn app.main:app" || info "フォアグラウンドプロセスもありません"
  fi
}

cmd_admin() {
  [ $# -ge 1 ] || die "使用方法: ./deck.sh admin <ユーザー名>"
  check_root; check_python; ensure_venv
  cd "$REPO_ROOT/backend"
  exec "$VENV/bin/python" -m app.cli create-admin "$1"
}

cmd_test() {
  check_root; check_python; ensure_venv
  cd "$REPO_ROOT/backend"
  exec "$VENV/bin/python" -m pytest -q "$@"
}

case "${1:-start}" in
  start)   cmd_start ;;
  service) cmd_service ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  admin)   shift; cmd_admin "$@" ;;
  test)    shift; cmd_test "$@" ;;
  -h|--help|help)
    sed -n '3,15p' "$0" ;;
  *)
    die "不明なコマンド: $1（./deck.sh help を参照）" ;;
esac
