#!/usr/bin/env bash
# systemd ユーザーサービスとして登録し、Ubuntu 起動時に自動起動させる。
# root 不要（ユーザーサービス + linger）。
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

refuse_root
ensure_venv

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

sed -e "s|@REPO_ROOT@|$REPO_ROOT|g" \
    "$REPO_ROOT/deploy/systemd/control-deck-web.service.in" \
    > "$UNIT_DIR/control-deck-web.service"

loginctl enable-linger "$USER" 2>/dev/null || echo "警告: linger を設定できませんでした" >&2
systemctl --user daemon-reload
systemctl --user enable --now control-deck-web.service

echo "control-deck-web を登録・起動しました"
systemctl --user --no-pager status control-deck-web.service || true
