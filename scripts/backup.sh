#!/usr/bin/env bash
# バックアップ: DB / 設定 / 暗号鍵 / systemd ユニット / アプリログ を tar.gz にまとめる。
# 使用: ./deck.sh backup [出力先ディレクトリ]（既定: repo/backups）
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib_paths.sh"

OUT_DIR="${1:-$REPO_ROOT/backups}"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$OUT_DIR/control-deck-backup-$STAMP.tar.gz"

DATA_DIR="$(resolve_data_dir)"
UNIT_DIR="$HOME/.config/systemd/user"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
STAGE="$TMP/control-deck-backup"
mkdir -p "$STAGE/data" "$STAGE/config" "$STAGE/systemd"

# DB は WAL を確定してからコピー（sqlite3 があれば checkpoint）
if command -v sqlite3 >/dev/null && [ -f "$DATA_DIR/control-deck.db" ]; then
  sqlite3 "$DATA_DIR/control-deck.db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true
fi

# データ（DB / 暗号鍵 / RAG / アイコン）。ログは容量が大きいため既定で除外
for item in control-deck.db secret.key rag icons; do
  [ -e "$DATA_DIR/$item" ] && cp -r "$DATA_DIR/$item" "$STAGE/data/" 2>/dev/null || true
done

# 設定
[ -f "$REPO_ROOT/config/config.yaml" ] && cp "$REPO_ROOT/config/config.yaml" "$STAGE/config/"

# 管理アプリの systemd ユニット
for unit in "$UNIT_DIR"/cdapp-*.service; do
  [ -e "$unit" ] && cp "$unit" "$STAGE/systemd/" 2>/dev/null || true
done

cat > "$STAGE/MANIFEST.txt" <<EOF
Ubuntu Control Deck backup
created: $(date -Iseconds)
host: $(hostname)
data_dir: $DATA_DIR
EOF

tar -czf "$ARCHIVE" -C "$TMP" control-deck-backup
echo "バックアップを作成しました: $ARCHIVE"
echo "サイズ: $(du -h "$ARCHIVE" | cut -f1)"
