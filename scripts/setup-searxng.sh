#!/usr/bin/env bash
# ============================================================
# SearXNG を Docker なしで直接導入し、ControlDeck の管理アプリとして登録する。
#
#   scripts/setup-searxng.sh          導入（既存なら更新）+ 管理アプリ登録
#   scripts/setup-searxng.sh update   git pull + 依存更新のみ
#
# 冪等: 何度実行してもよい。
# - 導入先: ~/.local/share/searxng（src / venv / settings.yml / run.sh）
# - 待受: 127.0.0.1:8888（ローカル専用・limiter 無効・JSON API 有効）
# - 起動/停止は ControlDeck の Apps ページ or ワークフローから。
#   web.search（engine=searxng）利用時は停止していても自動起動される。
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${SEARXNG_DIR:-$HOME/.local/share/searxng}"
SRC="$INSTALL_DIR/src"
VENV="$INSTALL_DIR/venv"
PORT="${SEARXNG_PORT:-8888}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

info() { echo -e "\033[36m[searxng]\033[0m $*"; }
die()  { echo -e "\033[31m[searxng] エラー:\033[0m $*" >&2; exit 1; }

command -v git >/dev/null || die "git が必要です"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 3.10 以上が必要です"

mkdir -p "$INSTALL_DIR"

# ---- 1. ソース取得 / 更新 ----
if [ -d "$SRC/.git" ]; then
  info "既存ソースを更新します: $SRC"
  git -C "$SRC" pull --ff-only || info "更新をスキップ（ローカル変更あり?）"
else
  info "SearXNG を取得します（github.com/searxng/searxng）"
  git clone --depth 1 https://github.com/searxng/searxng "$SRC"
fi

# ---- 2. venv + 依存 ----
if [ ! -x "$VENV/bin/python" ]; then
  info "専用 venv を作成します: $VENV"
  "$PYTHON_BIN" -m venv "$VENV"
fi
info "依存パッケージを導入します（初回は数分かかります）"
"$VENV/bin/pip" install -q -U pip setuptools wheel
"$VENV/bin/pip" install -q -r "$SRC/requirements.txt"

# ---- 3. settings.yml（初回のみ生成。JSON API 有効・ローカル専用） ----
SETTINGS="$INSTALL_DIR/settings.yml"
if [ ! -f "$SETTINGS" ]; then
  info "設定を生成します: $SETTINGS"
  SECRET="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$SETTINGS" <<EOF
# ControlDeck 用 SearXNG 設定（ローカル専用）。項目は公式 settings.yml に準拠
use_default_settings: true
general:
  instance_name: "ControlDeck SearXNG"
  debug: false
server:
  bind_address: "127.0.0.1"
  port: ${PORT}
  secret_key: "${SECRET}"
  limiter: false            # ローカル利用のためレート制限不要
  public_instance: false
  image_proxy: false
search:
  formats:                  # ControlDeck の web.search は json を使う
    - html
    - json
EOF
else
  info "既存の設定を維持します: $SETTINGS"
fi

# ---- 4. 起動スクリプト ----
RUN="$INSTALL_DIR/run.sh"
cat > "$RUN" <<EOF
#!/usr/bin/env bash
# ControlDeck 管理アプリから起動される SearXNG ランチャー
export SEARXNG_SETTINGS_PATH="$SETTINGS"
cd "$SRC"
exec "$VENV/bin/python" -m searx.webapp
EOF
chmod +x "$RUN"

[ "${1:-}" = "update" ] && { info "更新のみ完了しました"; exit 0; }

# ---- 5. ControlDeck 管理アプリとして登録（冪等 upsert） ----
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  info "ControlDeck の管理アプリとして登録します"
  cd "$REPO_ROOT/backend"
  RUN_SH="$RUN" SRC_DIR="$SRC" WEB_PORT="$PORT" "$REPO_ROOT/.venv/bin/python" - <<'PY'
import os

from app.applications import systemd as sd
from app.bootstrap import init_db
from app.database import SessionLocal
from app.models import ManagedApplication

init_db()
db = SessionLocal()
try:
    app = next((a for a in db.query(ManagedApplication).all()
                if a.name.strip().lower() == "searxng"), None)
    if app is None:
        app = ManagedApplication(name="SearXNG", application_type="shell_script")
        db.add(app)
    app.description = "プライベート検索エンジン（直接導入）。ControlDeck と同期起動/停止。web.search / チャット検索が利用"
    app.application_type = "shell_script"
    app.script_path = os.environ["RUN_SH"]
    app.working_directory = os.environ["SRC_DIR"]
    app.web_port = int(os.environ["WEB_PORT"])
    app.restart_policy = "on-failure"
    app.auto_start = False  # ControlDeck の lifespan が起動/停止を同期する
    db.flush()
    app.systemd_unit_name = sd.unit_name_for(app.id)
    db.commit()
    print(f"登録完了: id={app.id} name={app.name} unit={app.systemd_unit_name}")
finally:
    db.close()
PY
else
  info "ControlDeck の venv が見つからないため管理アプリ登録をスキップしました（./deck.sh 実行後に再実行してください）"
fi

info "完了。Apps ページの「SearXNG」から起動/停止できます（http://127.0.0.1:${PORT}）"
info "web.search / アシスタントの SearXNG は URL 未指定でこのインスタンスを使い、停止中は自動起動されます"
