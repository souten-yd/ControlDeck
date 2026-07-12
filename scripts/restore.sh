#!/usr/bin/env bash
# リストア: backup.sh が作成した tar.gz から DB / 設定 / ユニットを復元する。
# 使用: ./deck.sh restore <アーカイブ.tar.gz>
# 注意: 既存の DB / 設定を上書きするため、事前に自動で退避コピーを取る。
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib_paths.sh"

ARCHIVE="${1:-}"
[ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ] || { echo "使用方法: ./deck.sh restore <アーカイブ.tar.gz>" >&2; exit 1; }

DATA_DIR="$(resolve_data_dir)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$DATA_DIR" "$UNIT_DIR" "$REPO_ROOT/config"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$ARCHIVE" -C "$TMP"
SRC="$TMP/control-deck-backup"
[ -d "$SRC" ] || { echo "不正なバックアップ形式です" >&2; exit 1; }

echo "== リストア内容 =="
cat "$SRC/MANIFEST.txt" 2>/dev/null || true
echo ""
read -rp "既存のデータを上書きします。続行しますか？ [y/N] " ans
[ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "中止しました"; exit 0; }

# 退避
SAFETY="$DATA_DIR/pre-restore-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SAFETY"
[ -f "$DATA_DIR/control-deck.db" ] && cp "$DATA_DIR/control-deck.db" "$SAFETY/" || true

# 復元
[ -d "$SRC/data" ] && cp -r "$SRC/data/." "$DATA_DIR/"
[ -f "$SRC/config/config.yaml" ] && cp "$SRC/config/config.yaml" "$REPO_ROOT/config/"
for unit in "$SRC"/systemd/cdapp-*.service; do
  [ -e "$unit" ] && cp "$unit" "$UNIT_DIR/" 2>/dev/null || true
done
command -v systemctl >/dev/null && systemctl --user daemon-reload 2>/dev/null || true

echo "リストア完了。退避データ: $SAFETY"
echo "サービスを再起動してください: ./deck.sh"
