#!/usr/bin/env bash
# 互換ラッパー: deck.sh へ統合されました
exec "$(dirname "${BASH_SOURCE[0]}")/../deck.sh" status >/dev/null 2>&1 || true
echo "このスクリプトは ./deck.sh へ統合されました。./deck.sh を実行してください。"
exec "$(dirname "${BASH_SOURCE[0]}")/../deck.sh" help
