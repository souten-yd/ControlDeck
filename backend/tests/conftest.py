import os
import tempfile
import shutil
import shlex
from pathlib import Path

import pytest

# app import 前にテスト用の隔離環境を設定する
_tmp = tempfile.mkdtemp(prefix="cd-test-")
_config = Path(_tmp) / "config.yaml"
_sandbox = Path(_tmp) / "sandbox"
_sandbox.mkdir()
_config.write_text(
    f"""
data_dir: {_tmp}/data
server:
  host: 127.0.0.1
  port: 18765
files:
  allowed_roots:
    - {_sandbox}
commander:
  workdir_roots:
    - {_sandbox}
""",
    encoding="utf-8",
)
os.environ["CONTROL_DECK_CONFIG"] = str(_config)
os.environ["CONTROL_DECK_NEWS_AUTO"] = "0"  # ニュースの定期取得は外へ通信するので試験では動かさない
os.environ["CONTROL_DECK_DB_URL"] = f"sqlite:///{_tmp}/test.db"
# 2026-11-28: 全 backend の生 tmux 呼出しを明示的な -L へ通す。
# TMUX_TMPDIR だけでは親 TMUX のソケットを引き継ぐため足りない。
# argv を mock する試験は変更せず、実行時だけ PATH の wrapper が隔離する。
_tmux_dir = Path(_tmp) / "tmux-sock"
_tmux_dir.mkdir()
os.environ["TMUX_TMPDIR"] = str(_tmux_dir)
os.environ.pop("TMUX", None)
os.environ.pop("TMUX_PANE", None)
_tmux_real = shutil.which("tmux")
if _tmux_real:
    _bin = Path(_tmp) / "bin"
    _bin.mkdir()
    _wrapper = _bin / "tmux"
    _socket = "nexarch-test-" + str(os.getpid())
    os.environ["CONTROL_DECK_TEST_TMUX_SOCKET"] = _socket
    # 呼出し側から別ソケットへ抜けたり全サーバーを停止する命令は許さない。
    _wrapper.write_text(
        "#!/bin/sh\n"
        "unset TMUX TMUX_PANE\n"
        'for arg do\n case "$arg" in -L*|-S*|kill-server) exit 97;; esac\ndone\n'
        + "export TMUX_TMPDIR=" + shlex.quote(str(_tmux_dir)) + "\n"
        + "exec " + shlex.quote(_tmux_real) + " -L " + shlex.quote(_socket) + ' "$@"\n',
        encoding="utf-8",
    )
    _wrapper.chmod(0o700)
    os.environ["PATH"] = str(_bin) + os.pathsep + os.environ.get("PATH", "")
# 添付の置き場も本番（/dev/shm/control-deck-terminal-<uid>）と分ける（attachments._default_root）。
os.environ["CONTROL_DECK_ATTACHMENTS_DIR"] = str(Path(_tmp) / "attachments")

from fastapi.testclient import TestClient  # noqa: E402

from app.bootstrap import create_admin, init_db, seed_roles  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

CSRF_HEADERS = {"X-Requested-With": "ControlDeck"}


@pytest.fixture(scope="session")
def client():
    init_db()
    db = SessionLocal()
    try:
        seed_roles(db)
        create_admin(db, "admin", "test-password-123")
    finally:
        db.close()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_client(client):
    r = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "test-password-123"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200, r.text
    return client


@pytest.fixture(autouse=True)
def _notify_at_noon():
    """通知のお休みモード（既定 22:00〜07:00）で、夜に流した試験だけ落ちないよう、時計を昼に固定する。

    monkeypatch を使わない: autouse で monkeypatch を先に作ると、片付けの順が変わり、os.fstat などを差し替えた
    試験の tmp_path の削除が差し替えたままの関数で走って壊れる（test_remote_desktop_toggle・test_system_services）。"""
    from datetime import datetime

    from app.alerts import events

    original = events.local_now
    events.local_now = lambda: datetime(2026, 11, 23, 12, 0)
    yield
    events.local_now = original


@pytest.fixture(autouse=True)
def _fresh_feature_cache():
    """feature 一覧の覚え書きを test をまたいで持ち越さない。

    本番では状態を変えたときに捨てるが、test は registry を直接触るので、
    前の test の値が次へ漏れる。
    """
    from app.features import registry

    registry.invalidate_feature_cache()
    yield
    registry.invalidate_feature_cache()


@pytest.fixture(autouse=True)
def _no_auto_browser():
    """静的で足りないページの「自動のブラウザ」を試験では起こさない（Chrome を勝手に起動しない）。
    使う試験（test_web_browser.py・test_js_render.py）は monkeypatch で明示的に有効にする。"""
    from app.web_collector import browser

    original = browser.is_enabled
    browser.is_enabled = lambda: False
    yield
    browser.is_enabled = original
