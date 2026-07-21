#!/usr/bin/env bash
# バックアップ: DB / 設定 / 暗号鍵 / systemd ユニット / アプリログ を tar.gz にまとめる。
# 使用: ./deck.sh backup [出力先ディレクトリ]（既定: repo/backups）
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib_paths.sh"

VENV="$REPO_ROOT/.venv"
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

# DB は WAL を確定してからコピー。sqlite3 CLI に依存せず venv Python で checkpoint する。
# さらに sqlite3 の backup API で整合性のあるスナップショットを取得する。
DB_SRC="$DATA_DIR/control-deck.db"
DB_BACKEND="$(cd "$REPO_ROOT/backend" && "$VENV/bin/python" -c 'from app.config import db_url; from app.database.runtime import validate_database_url; print(validate_database_url(db_url()).get_backend_name())')"
if [ "$DB_BACKEND" = "postgresql" ]; then
  (cd "$REPO_ROOT/backend" && "$VENV/bin/python" -m app.database.pg_tools dump "$STAGE/data/control-deck.postgresql.dump") \
    || { echo "PostgreSQL backupに失敗しました（pg_dumpと接続設定を確認してください）" >&2; exit 1; }
  DB_COPIED=1
elif [ -f "$DB_SRC" ] && [ -x "$VENV/bin/python" ]; then
  "$VENV/bin/python" - "$DB_SRC" "$STAGE/data/control-deck.db" <<'PY' || cp "$DB_SRC" "$STAGE/data/control-deck.db"
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src)
s.execute("PRAGMA wal_checkpoint(TRUNCATE)")
d = sqlite3.connect(dst)
with d:
    s.backup(d)  # 一貫したオンラインバックアップ
d.close(); s.close()
PY
  DB_COPIED=1
fi

# データ（暗号鍵 / RAG / アイコン）。DB は上で backup API により取得済み。ログは既定除外
for item in secret.key rag icons; do
  [ -e "$DATA_DIR/$item" ] && cp -r "$DATA_DIR/$item" "$STAGE/data/" 2>/dev/null || true
done
# backup API が使えなかった場合のフォールバック
[ -z "${DB_COPIED:-}" ] && [ -f "$DB_SRC" ] && cp "$DB_SRC" "$STAGE/data/" 2>/dev/null || true

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
database_backend: $DB_BACKEND
EOF

tar -czf "$ARCHIVE" -C "$TMP" control-deck-backup
echo "バックアップを作成しました: $ARCHIVE"
echo "サイズ: $(du -h "$ARCHIVE" | cut -f1)"
