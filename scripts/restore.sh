#!/usr/bin/env bash
# リストア: backup.sh が作成した tar.gz から DB / 設定 / ユニットを復元する。
# 使用: ./deck.sh restore <アーカイブ.tar.gz>
# 注意: 既存の DB / 設定を上書きするため、事前に自動で退避コピーを取る。
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib_paths.sh"

ARCHIVE="${1:-}"
[ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ] || { echo "使用方法: ./deck.sh restore <アーカイブ.tar.gz>" >&2; exit 1; }

DATA_DIR="$(resolve_data_dir)"
VENV="$REPO_ROOT/.venv"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$DATA_DIR" "$UNIT_DIR" "$REPO_ROOT/config"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
(cd "$REPO_ROOT/backend" && "$VENV/bin/python" -m app.database.backup_archive "$ARCHIVE" "$TMP") \
  || { echo "不正または安全上限を超えたバックアップ形式です" >&2; exit 1; }
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
DB_BACKEND="$(cd "$REPO_ROOT/backend" && "$VENV/bin/python" -c 'from app.config import db_url; from app.database.runtime import validate_database_url; print(validate_database_url(db_url()).get_backend_name())')"
WAS_ACTIVE=0
if [ -f "$SRC/data/control-deck.postgresql.dump" ]; then
  [ "$DB_BACKEND" = "postgresql" ] || { echo "PostgreSQL backupはPostgreSQL設定時だけ復元できます" >&2; exit 1; }
  (cd "$REPO_ROOT/backend" && "$VENV/bin/python" -m app.database.pg_tools dump "$SAFETY/control-deck.postgresql.dump") \
    || { echo "復元前PostgreSQL safety backupに失敗しました" >&2; exit 1; }
  systemctl --user is-active --quiet control-deck-web.service 2>/dev/null && WAS_ACTIVE=1
  [ "$WAS_ACTIVE" -eq 0 ] || systemctl --user stop control-deck-web.service
  if ! (cd "$REPO_ROOT/backend" && "$VENV/bin/python" -m app.database.pg_tools restore "$SRC/data/control-deck.postgresql.dump"); then
    [ "$WAS_ACTIVE" -eq 0 ] || systemctl --user start control-deck-web.service
    echo "PostgreSQL restoreに失敗しました。safety backup: $SAFETY/control-deck.postgresql.dump" >&2
    exit 1
  fi
elif [ "$DB_BACKEND" = "postgresql" ]; then
  echo "SQLite backupはPostgreSQL設定中に復元できません。先に ./deck.sh database sqlite を実行してください" >&2
  exit 1
else
  [ -f "$DATA_DIR/control-deck.db" ] && cp "$DATA_DIR/control-deck.db" "$SAFETY/" || true
fi

# 復元
for item in control-deck.db secret.key rag icons; do
  [ -e "$SRC/data/$item" ] && cp -r "$SRC/data/$item" "$DATA_DIR/"
done
[ -f "$SRC/config/config.yaml" ] && cp "$SRC/config/config.yaml" "$REPO_ROOT/config/"
for unit in "$SRC"/systemd/cdapp-*.service; do
  [ -e "$unit" ] && cp "$unit" "$UNIT_DIR/" 2>/dev/null || true
done
command -v systemctl >/dev/null && systemctl --user daemon-reload 2>/dev/null || true
[ "$WAS_ACTIVE" -eq 0 ] || systemctl --user start control-deck-web.service

echo "リストア完了。退避データ: $SAFETY"
echo "サービスを再起動してください: ./deck.sh"
