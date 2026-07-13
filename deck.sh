#!/usr/bin/env bash
# ============================================================
# Ubuntu Control Deck — 唯一のエントリースクリプト
#
#   ./deck.sh              状態を自動判定 → 不足があればセットアップ → 起動
#   ./deck.sh service      systemd ユーザーサービスとして登録・起動（OS 起動時に自動起動）
#   ./deck.sh stop         サービス停止
#   ./deck.sh status       サービス状態表示
#   ./deck.sh admin <名前> 管理者ユーザー作成
#   ./deck.sh passwd <名前> ログインパスワードを変更
#   ./deck.sh reset-totp <名前>   二要素認証を解除（ロックアウト復旧用。--all で全員）
#   ./deck.sh backup [出力先]      DB/設定/ユニットをバックアップ
#   ./deck.sh restore <ファイル>   バックアップから復元
#   ./deck.sh enable-desktop      この PC のリモートデスクトップを有効化（既定=ヘッドレス）
#                                 --active で現在のログインセッション共有
#   ./deck.sh disable-desktop     リモートデスクトップを無効化
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

# apt パッケージを導入する。パスワード不要 sudo → そのまま、対話端末 → sudo を対話実行、
# どちらも不可なら失敗を返す（呼び出し側が案内を出す）。
apt_install() {
  command -v apt-get >/dev/null || return 1
  if sudo -n true 2>/dev/null; then
    sudo -n apt-get update -qq && sudo -n apt-get install -y -qq "$@"
  elif [ -t 0 ]; then
    info "不足パッケージを導入します: $*（sudo パスワードを求められる場合があります）"
    sudo apt-get update -qq && sudo apt-get install -y -qq "$@"
  else
    return 1
  fi
}

check_python() {
  if ! command -v "$PYTHON_BIN" >/dev/null; then
    apt_install python3 python3-venv || die "python3 が見つかりません: sudo apt install python3 python3-venv"
  fi
  "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11 以上が必要です（現在: $($PYTHON_BIN --version)）。Ubuntu 24.04 以降を推奨します"
}

ensure_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    info "Python 仮想環境を構築しています (.venv) ..."
    if ! "$PYTHON_BIN" -m venv "$VENV" 2>/dev/null; then
      rm -rf "$VENV"
      apt_install python3-venv || die "venv 作成失敗: sudo apt install python3-venv"
      "$PYTHON_BIN" -m venv "$VENV" || die "venv 作成失敗: sudo apt install python3-venv"
    fi
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
    if ! command -v npm >/dev/null; then
      apt_install nodejs npm || die "フロントエンドのビルドに npm が必要です: sudo apt install nodejs npm"
    fi
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
  command -v git >/dev/null || missing+=(git)
  command -v gh >/dev/null || missing+=(gh)   # GitHub 管理（非公開リポジトリのログイン）用
  command -v tesseract >/dev/null || missing+=(tesseract-ocr tesseract-ocr-jpn)
  # リモートデスクトップ関連（config で有効時のみ導入を試みる）
  if grep -qsE '^[[:space:]]*enabled:[[:space:]]*true' "$REPO_ROOT/config/config.yaml" 2>/dev/null \
     && grep -qs 'remote_desktop' "$REPO_ROOT/config/config.yaml" 2>/dev/null; then
    command -v guacd >/dev/null || missing+=(guacd)
    # ヘッドレス（xrdp）を設定済みなら、セッション用 XFCE も揃える
    if command -v xrdp >/dev/null && ! command -v xfce4-session >/dev/null; then
      missing+=(xfce4 xfce4-goodies dbus-x11)
    fi
  fi
  [ ${#missing[@]} -eq 0 ] && return 0
  info "システムパッケージを導入しています: ${missing[*]}"
  if ! apt_install "${missing[@]}"; then
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
    exec "$VENV/bin/python" -m app.server
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

cmd_passwd() {
  [ $# -ge 1 ] || die "使用方法: ./deck.sh passwd <ユーザー名>"
  check_root; check_python; ensure_venv
  cd "$REPO_ROOT/backend"
  exec "$VENV/bin/python" -m app.cli reset-password "$1"
}

cmd_reset_totp() {
  [ $# -ge 1 ] || die "使用方法: ./deck.sh reset-totp <ユーザー名>|--all"
  check_root; check_python; ensure_venv
  cd "$REPO_ROOT/backend"
  exec "$VENV/bin/python" -m app.cli reset-totp "$1"
}

cmd_test() {
  check_root; check_python; ensure_venv
  cd "$REPO_ROOT/backend"
  exec "$VENV/bin/python" -m pytest -q "$@"
}

cmd_backup() {
  check_root
  exec bash "$REPO_ROOT/scripts/backup.sh" "$@"
}

# GNOME Remote Desktop を設定してこの PC を Web から操作可能にする。
# 既定はヘッドレス（--system: 接続時に仮想セッションを作成、物理画面は不要）。
# --active で現在のログインセッションを共有する。
cmd_enable_desktop() {
  check_root; check_python; ensure_venv

  local mode="headless"
  [ "${1:-}" = "--active" ] && mode="active"

  command -v openssl >/dev/null || die "openssl が必要です: sudo apt install openssl"

  # guacd（トンネルに必須）
  if ! command -v guacd >/dev/null; then
    info "guacd（ブラウザ接続に必須）を導入します（sudo が必要です）..."
    sudo apt-get install -y -qq guacd || die "guacd の導入に失敗しました。手動で: sudo apt install guacd"
  fi

  if [ "$mode" = "headless" ]; then
    # ヘッドレスは xrdp を使う（OS 同梱 guacd 1.3.0/FreeRDP2 と互換。
    # GNOME Remote Desktop は FreeRDP3 系で OS 同梱 guacd と非互換のため使わない）。
    if ! command -v xrdp >/dev/null; then
      info "xrdp を導入します（sudo が必要です）..."
      sudo apt-get install -y -qq xrdp || die "xrdp の導入に失敗しました: sudo apt install xrdp"
    fi
    # xrdp セッション用の軽量デスクトップ（XFCE）。GNOME の同時 1 セッション制約を避ける。
    if ! command -v xfce4-session >/dev/null; then
      info "xrdp セッション用に XFCE を導入します（sudo が必要です）..."
      sudo apt-get install -y -qq xfce4 xfce4-goodies dbus-x11 || warn "XFCE の導入に失敗しました"
    fi
    # このユーザーの xrdp セッションで XFCE を起動する設定
    if command -v xfce4-session >/dev/null; then
      # コンソール GNOME セッションと D-Bus/ディスプレイが競合しないよう、
      # 分離した専用セッションバス(dbus-run-session)で XFCE を起動する。
      {
        echo '#!/bin/sh'
        echo '# Control Deck: xrdp セッション用（分離 D-Bus で XFCE 起動）'
        echo 'unset WAYLAND_DISPLAY'
        echo 'unset DBUS_SESSION_BUS_ADDRESS'
        echo '# GTK3 は WAYLAND_DISPLAY なしでも wayland-0 ソケットへ接続しようとするため、'
        echo '# コンソール GNOME(Wayland) と併存するホストでは X11 バックエンドを強制する。'
        echo '# これがないと xfce4-panel/xfdesktop がモニター0個の Wayland を掴んで segfault し黒画面になる。'
        echo 'export GDK_BACKEND=x11'
        echo 'export QT_QPA_PLATFORM=xcb'
        echo 'export CLUTTER_BACKEND=x11'
        echo 'export XDG_SESSION_TYPE=x11'
        echo 'export XDG_CURRENT_DESKTOP=XFCE'
        echo 'export XDG_SESSION_DESKTOP=xfce'
        echo 'exec dbus-run-session -- xfce4-session'
      } > "$HOME/.xsession"
      chmod +x "$HOME/.xsession"
      info "XFCE を xrdp セッションに設定しました（~/.xsession、分離 D-Bus）。"
    fi
    # GNOME Remote Desktop が 3389 を占有していれば解放
    if command -v grdctl >/dev/null; then
      sudo grdctl --system rdp disable 2>/dev/null || true
      sudo systemctl restart gnome-remote-desktop.service 2>/dev/null || true
    fi
    sudo systemctl enable --now xrdp || die "xrdp サービスを開始できませんでした"
    info "xrdp を有効化しました（接続時に XFCE の新規セッションを作成、ログインはシステムアカウント）。"
  else
    command -v grdctl >/dev/null || die "gnome-remote-desktop が必要です: sudo apt install gnome-remote-desktop"
    warn "アクティブセッション共有は GNOME Remote Desktop を使います。OS 同梱の guacd 1.3.0 とは"
    warn "  RDP 非互換の場合があります（動かない場合はヘッドレス=xrdp を使ってください）。"
    local data_dir; data_dir="$(cd "$REPO_ROOT/backend" && CONTROL_DECK_CONFIG="$REPO_ROOT/config/config.yaml" "$VENV/bin/python" -c 'from app.config import data_dir; print(data_dir())')"
    local crt="$data_dir/grd-tls.crt" key="$data_dir/grd-tls.key"
    [ -f "$crt" ] || { openssl req -new -newkey rsa:2048 -days 3650 -nodes -x509 -subj "/CN=control-deck-rdp" -out "$crt" -keyout "$key" 2>/dev/null; chmod 600 "$key"; }
  fi

  # 認証情報: 環境変数があれば非対話、なければ TTY で入力
  # ヘッドレス(xrdp)はシステムアカウントで PAM 認証、active はGNOME RD の RDP 認証
  local rdp_user rdp_pass rdp_pass2 prompt_user
  [ "$mode" = "headless" ] && prompt_user="ログインユーザー名（システムアカウント）" || prompt_user="RDP ユーザー名"
  if [ -n "${RDP_USERNAME:-}" ] && [ -n "${RDP_PASSWORD:-}" ]; then
    rdp_user="$RDP_USERNAME"; rdp_pass="$RDP_PASSWORD"
  elif [ -t 0 ]; then
    read -rp "${prompt_user} [${USER}]: " rdp_user
    rdp_user="${rdp_user:-$USER}"
    read -rsp "パスワード: " rdp_pass; echo
    read -rsp "パスワード（確認）: " rdp_pass2; echo
    [ "$rdp_pass" = "$rdp_pass2" ] || die "パスワードが一致しません"
  else
    die "対話端末がありません。実端末で実行するか、環境変数 RDP_USERNAME / RDP_PASSWORD を指定してください"
  fi
  [ -n "$rdp_pass" ] || die "パスワードは必須です"

  if [ "$mode" = "active" ]; then
    info "GNOME Remote Desktop を設定しています（active）..."
    grdctl rdp set-tls-cert "$crt"
    grdctl rdp set-tls-key "$key"
    grdctl rdp set-credentials "$rdp_user" "$rdp_pass"
    grdctl rdp enable
  fi

  # config を有効化
  if ! grep -qs 'remote_desktop' "$REPO_ROOT/config/config.yaml" 2>/dev/null; then
    printf '\nremote_desktop:\n  enabled: true\n  guacd_host: 127.0.0.1\n  guacd_port: 4822\n' >> "$REPO_ROOT/config/config.yaml"
  fi

  # Control Deck に接続を登録（headless=xrdp は security=any、active=GNOME RD は tls）
  local sec="any"; [ "$mode" = "active" ] && sec="tls"
  ( cd "$REPO_ROOT/backend" && \
    CONTROL_DECK_CONFIG="$REPO_ROOT/config/config.yaml" \
    RDP_NAME="ServerPC" RDP_HOST="127.0.0.1" RDP_PORT="3389" \
    RDP_USERNAME="$rdp_user" RDP_PASSWORD="$rdp_pass" RDP_SECURITY="$sec" \
    "$VENV/bin/python" -m app.cli register-local-desktop )

  echo ""
  info "完了しました。Web の「リモート」から「ServerPC」に接続できます。"
  warn "セキュリティ: RDP は 3389 番で待ち受けます。外部からのアクセスはファイアウォールや"
  warn "  Tailscale/VPN で遮断し、必ず Control Deck 経由で利用してください。"
  [ "$mode" = "headless" ] && info "ヘッドレス: 接続時に仮想セッションが作成されます（物理画面は不要）。"
}

cmd_disable_desktop() {
  if [ "${1:-}" = "--active" ]; then
    command -v grdctl >/dev/null && grdctl rdp disable && info "アクティブセッション共有を無効化しました"
  else
    # ヘッドレス=xrdp を停止
    sudo systemctl disable --now xrdp 2>/dev/null && info "ヘッドレス（xrdp）を無効化しました" \
      || warn "xrdp を停止できませんでした"
  fi
}

cmd_restore() {
  check_root
  exec bash "$REPO_ROOT/scripts/restore.sh" "$@"
}

case "${1:-start}" in
  start)   cmd_start ;;
  service) cmd_service ;;
  stop)    cmd_stop ;;
  status)  cmd_status ;;
  admin)   shift; cmd_admin "$@" ;;
  passwd)  shift; cmd_passwd "$@" ;;
  reset-totp) shift; cmd_reset_totp "$@" ;;
  backup)  shift; cmd_backup "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  enable-desktop)  shift; cmd_enable_desktop "$@" ;;
  disable-desktop) shift; cmd_disable_desktop "$@" ;;
  test)    shift; cmd_test "$@" ;;
  -h|--help|help)
    sed -n '3,15p' "$0" ;;
  *)
    die "不明なコマンド: $1（./deck.sh help を参照）" ;;
esac
