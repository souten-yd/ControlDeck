#!/usr/bin/env bash
# 初回セットアップ: venv 構築 + Node 依存 + フロントエンドビルド + linger 有効化
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

refuse_root

echo "== Ubuntu Control Deck セットアップ =="

# 1. システム要件チェック
command -v systemctl >/dev/null || die "systemd が必要です"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python 3.11 以上が必要です"

# 2. Python venv + 依存
ensure_venv

# 3. フロントエンド
if command -v npm >/dev/null; then
  echo "フロントエンド依存をインストールしています ..."
  (cd "$REPO_ROOT/frontend" && npm install --silent && npm run build --silent)
else
  echo "警告: npm が見つかりません。フロントエンドをビルドできません。" >&2
  echo "  sudo apt install nodejs npm  （または nvm 等）の後、再実行してください" >&2
fi

# 4. ログアウト後もユーザーサービス（登録アプリ）を動かし続ける
if command -v loginctl >/dev/null; then
  if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q "Linger=yes"; then
    echo "loginctl enable-linger を設定しています（SSH 切断後もアプリを継続実行するため）..."
    loginctl enable-linger "$USER" || echo "警告: linger を設定できませんでした" >&2
  fi
fi

# 5. 設定ファイル
if [ ! -f "$REPO_ROOT/config/config.yaml" ]; then
  mkdir -p "$REPO_ROOT/config"
  cp "$REPO_ROOT/config/config.example.yaml" "$REPO_ROOT/config/config.yaml"
  echo "config/config.yaml を作成しました（初期値: 127.0.0.1:8765）"
fi

echo ""
echo "セットアップ完了。次の手順:"
echo "  1. 管理者作成:      ./scripts/create-admin.sh <username>"
echo "  2. 開発起動:        ./scripts/run-dev.sh"
echo "  3. サービス登録:    ./scripts/install-service.sh   （Ubuntu 起動時に自動起動）"
