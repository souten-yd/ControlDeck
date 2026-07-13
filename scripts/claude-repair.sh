#!/usr/bin/env bash
# Control Deck 修復コンソール。
# ~/ControlDeck 上で Claude Code を tmux セッション(cdterm-claude)で起動する。
# 起動後は Web の「ターミナル」タブに "cdterm-claude" として現れ、アタッチして
# Claude と対話しながら Control Deck を改修できる（再起動後も linger + tmux で継続）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="cdterm-claude"

# claude CLI の場所を解決（PATH に無い環境向けに pnpm/npm グローバルも探す）
CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
for cand in "$HOME/.local/share/pnpm/bin/claude" "$HOME/.local/bin/claude" "/usr/local/bin/claude"; do
  [ -z "$CLAUDE_BIN" ] && [ -x "$cand" ] && CLAUDE_BIN="$cand"
done
[ -n "$CLAUDE_BIN" ] || { echo "claude CLI が見つかりません" >&2; exit 1; }

command -v tmux >/dev/null || { echo "tmux が必要です: sudo apt install tmux" >&2; exit 1; }

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "既に起動しています（Web ターミナルの 'claude' セッションにアタッチしてください）"
else
  tmux new-session -d -s "$SESSION" -c "$REPO_ROOT" "$CLAUDE_BIN"
  echo "Claude 修復コンソールを起動しました（Web ターミナルの 'claude' セッションから利用）"
fi
