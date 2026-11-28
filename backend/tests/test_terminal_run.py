"""AI アシスタントの「▶ 実行」を画面の中で実行する API（POST /terminals/run など）を守る。

目的: (1) 管理者だけが使え、作った本人のセッションだけを読める・入力できる・止められる。
(2) 終わりの印から終了コードが取れ、パスワードの要求（英語・日本語）を見分けられる。
(3) **パスワードは監査・ログ・応答・画面の出力のどこにも残らない**。要求が出ていないときは送らない（409）。
根拠: 2026-11-28 利用者「返事のコンソールコマンドをユーザ側で実行できるように。管理者パスワードが必要なら
ポップアップで入力できるように」。パスワードの扱いが最も危ない所なので、実際の tmux（conftest の隔離ソケット・
本番の tmux には触れない）で通して確かめる。
"""
from __future__ import annotations

import logging
import shutil
import time

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import AuditLog
from app.terminals import run as run_module
from app.terminals.manager import manager, tmux_available

CSRF = {"X-Requested-With": "ControlDeck"}
SECRET = "-".join(["probe", "value", "zz9", "unique"])   # 実行時に組み立てる（値の見本）


@pytest.fixture()
def made():
    """作ったセッションを片付ける。"""
    ids: list[str] = []
    yield ids
    for sid in ids:
        try:
            manager.kill_session(sid)
        except Exception:  # noqa: BLE001
            pass


def _start(client, command: str, made: list[str], **extra) -> str:
    r = client.post("/api/v1/terminals/run", headers=CSRF, json={"command": command, **extra})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    made.append(sid)
    return sid


def _wait(client, sid: str, predicate, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/v1/terminals/run/{sid}")
        assert r.status_code == 200, r.text
        last = r.json()
        if predicate(last):
            return last
        time.sleep(0.2)
    raise AssertionError(f"待ち切れませんでした: {last}")


# ---- 判定（機械を使わない） ----

@pytest.mark.parametrize("text,expected", [
    ("[sudo] password for souten: ", True),
    ("[sudo] souten のパスワード: ", True),
    ("パスワード:", True),
    ("Password:", True),
    ("doas (u@h) password: ", True),
    ("hello\nworld", False),
    ("Reading config done", False),
    ("", False),
])
def test_password_prompt_detection(text, expected):
    assert run_module.password_prompt(text.splitlines()) is expected


def test_parse_capture_exit_code_and_marker_hidden():
    text = "$ echo hi\nhi\n$ printf '\\n__CD_RUN_DONE__:%s\\n' \"$?\"\n\n__CD_RUN_DONE__:3\n\n\n"
    parsed = run_module.parse_capture(text)
    assert parsed["done"] is True and parsed["exit_code"] == 3
    assert "__CD_RUN_DONE__" not in parsed["output"] and "hi" in parsed["output"]
    assert parsed["password_prompt"] is False
    running = run_module.parse_capture("[sudo] password for a: ")
    assert running["done"] is False and running["exit_code"] is None and running["password_prompt"] is True


# ---- 権限・本人 ----

def test_requires_admin_and_login(client, admin_client, monkeypatch):
    monkeypatch.setattr("app.security.deps.user_permissions", lambda _u: {"terminal.use"})
    r = admin_client.post("/api/v1/terminals/run", headers=CSRF, json={"command": "echo x"})
    assert r.status_code == 403
    assert admin_client.get("/api/v1/terminals/run/0123abcd").status_code == 403
    monkeypatch.undo()
    admin_client.cookies.clear()
    assert admin_client.post("/api/v1/terminals/run", headers=CSRF, json={"command": "echo x"}).status_code == 401


def test_other_users_session_and_unmarked_session_are_404(admin_client, made):
    # 印の無い普通の Terminal のセッションも、他人の run のセッションも 404
    plain = manager.create_session()["id"]
    made.append(plain)
    other = manager.create_session()["id"]
    made.append(other)
    manager.tag_session(other, {"label": "x", "run_owner": "999999"})
    for sid in (plain, other, "0123abcd", "not-a-sid"):
        assert admin_client.get(f"/api/v1/terminals/run/{sid}").status_code == 404
        assert admin_client.post(f"/api/v1/terminals/run/{sid}/stop", headers=CSRF).status_code == 404
        assert admin_client.post(f"/api/v1/terminals/run/{sid}/input", headers=CSRF,
                                 json={"secret": SECRET}).status_code == 404


def test_cwd_is_validated_like_post_terminals(admin_client, made):
    r = admin_client.post("/api/v1/terminals/run", headers=CSRF,
                          json={"command": "echo x", "cwd": "/no/such/dir-xyz"})
    assert r.status_code in (403, 404, 422)
    assert admin_client.post("/api/v1/terminals/run", headers=CSRF, json={"command": ""}).status_code == 422


# ---- 実際の tmux（隔離ソケット）で通す ----

needs_tmux = pytest.mark.skipif(not tmux_available() or not shutil.which("bash"), reason="tmux が無い")


@needs_tmux
def test_run_reads_output_and_exit_code_and_audit_omits_command(admin_client, made):
    command = "echo run-inline-marker-$((6*7)); false"
    sid = _start(admin_client, command, made)
    got = _wait(admin_client, sid, lambda d: d["done"])
    assert got["exit_code"] == 1
    assert "run-inline-marker-42" in got["output"]
    assert "__CD_RUN_DONE__" not in got["output"]
    with SessionLocal() as db:
        rows = db.execute(select(AuditLog).where(AuditLog.action == "terminal.run")).scalars().all()
    assert rows and "run-inline-marker" not in str(rows[-1].metadata_json if hasattr(rows[-1], "metadata_json") else rows[-1].__dict__)


@needs_tmux
def test_password_prompt_input_never_leaves_a_trace(admin_client, made, caplog):
    caplog.set_level(logging.DEBUG)
    # sudo と同じく端末から、エコー無しで読む
    command = "printf '[sudo] password for tester: '; read -rs PW; echo; echo got-${#PW}-chars"
    sid = _start(admin_client, command, made)
    _wait(admin_client, sid, lambda d: d["password_prompt"])
    r = admin_client.post(f"/api/v1/terminals/run/{sid}/input", headers=CSRF, json={"secret": SECRET})
    assert r.status_code == 200 and SECRET not in r.text
    got = _wait(admin_client, sid, lambda d: d["done"])
    assert got["exit_code"] == 0
    assert f"got-{len(SECRET)}-chars" in got["output"]
    assert SECRET not in got["output"]
    assert SECRET not in caplog.text
    with SessionLocal() as db:
        every = "".join(str(vars(row)) for row in db.execute(select(AuditLog)).scalars().all())
        inputs = db.execute(select(AuditLog).where(AuditLog.action == "terminal.run.input")).scalars().all()
    assert SECRET not in every and inputs


@needs_tmux
def test_input_without_prompt_is_409_and_not_sent(admin_client, made, caplog):
    sid = _start(admin_client, "sleep 30", made)
    time.sleep(0.5)
    r = admin_client.post(f"/api/v1/terminals/run/{sid}/input", headers=CSRF, json={"secret": SECRET})
    assert r.status_code == 409 and SECRET not in r.text
    assert SECRET not in manager.capture_text(sid, 50)
    assert SECRET not in caplog.text


@needs_tmux
def test_input_validation_errors_do_not_echo_the_secret(admin_client, made):
    sid = _start(admin_client, "sleep 30", made)
    for body in ({"secret": ""}, {"secret": "x" * 2000 + SECRET}, {"secret": SECRET + "\nrm"}, {"secret": 5},
                 [SECRET]):
        r = admin_client.post(f"/api/v1/terminals/run/{sid}/input", headers=CSRF, json=body)
        assert r.status_code == 422 and SECRET not in r.text
    r = admin_client.post(f"/api/v1/terminals/run/{sid}/input",
                          headers={**CSRF, "Content-Type": "application/json"}, content=SECRET + "{")
    assert r.status_code == 422 and SECRET not in r.text


@needs_tmux
def test_stop_sends_ctrl_c(admin_client, made):
    sid = _start(admin_client, "sleep 300", made)
    time.sleep(0.5)
    assert admin_client.post(f"/api/v1/terminals/run/{sid}/stop", headers=CSRF).status_code == 200
    got = _wait(admin_client, sid, lambda d: d["done"])
    assert got["exit_code"] == 130   # 止めたときは 130 で固定（止まり方は INT・TERM・KILL のどれか）
