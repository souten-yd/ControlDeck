"""AI Commander のコンソール（app/commander/consoles.py・manager の env と印・/consoles API）の試験。

目的: 「コンソール = Terminal の tmux セッション + 印」を守る。
- 既定のアカウントは環境変数を付けない・名前付きアカウントは CLI の表の環境変数を
  tmux へ配列引数で渡すこと（shell を使わない）。
- 印（@cd-commander-*）がセッションへ付き、一覧で読み返せること。
- Terminal の上限に当たったら部分的に開いて理由を返すこと。作業フォルダは許可の外なら 400。

根拠: c1-spec.md（2026-11-26）と AGENTS.md の「shell=True 禁止」「async の中で
  同期的に待たない」（endpoint は def）。env をシェル文字列へ連結すると
  パスに空白が含まれるだけで壊れるので、配列引数であることを機械的に確かめる。
  Terminal の manager を広げたので test_terminals.py と役割を分担する
  （あちらは engine・PTY の既存の振る舞い、こちらは env と印）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from sqlalchemy import select

import app.terminals.manager as terminal_manager_module
from app.commander import accounts, consoles
from app.database import SessionLocal
from app.models import AuditLog
from app.terminals.manager import TerminalManager
from tests.conftest import CSRF_HEADERS


def _root() -> str:
    from app.config import get_config

    return str(Path(get_config().files.allowed_roots[0]).resolve())


# ---------------------------------------------------------------- manager: env と印

def test_create_session_passes_env_as_array_arguments(monkeypatch):
    """環境変数は tmux へ配列引数として渡ること（シェルの文字列に連結しない）。

    根拠: AGENTS.md「subprocess は必ず配列引数。ユーザー入力をシェル文字列へ連結しない」。
    tmux new-session -e は KEY=VALUE を 1 引数で受け取る。
    """
    calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003 - subprocess 互換
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(terminal_manager_module.subprocess, "run", fake_run)
    mgr = TerminalManager()
    session = mgr.create_session(
        cwd=_root(), command="claude", env={"CLAUDE_CONFIG_DIR": "/tmp/cd-acct/claude/work"},
    )
    assert session["id"]
    new_session_calls = [argv for argv in calls if "new-session" in argv]
    assert new_session_calls, calls
    for argv in new_session_calls:
        # 配列引数: KEY=VALUE が 1 要素。シェル文字列への連結（1 要素に複数の -e など）が無い。
        assert "-e" in argv
        assert "CLAUDE_CONFIG_DIR=/tmp/cd-acct/claude/work" in argv
        assert argv[0] in ("tmux", "systemd-run")  # shell=True の形は argv が 1 個の文字列になる
        assert all(isinstance(a, str) for a in argv)
    # 環境変数の名前・値が不正ならセッションを作らない
    for bad in ({"BAD KEY": "x"}, {"K": ""}, {"K": "a\nb"}, {"K": "a\x00b"}):
        before = len([argv for argv in calls if "new-session" in argv])
        try:
            mgr.create_session(env=bad)
        except ValueError:
            pass
        after = len([argv for argv in calls if "new-session" in argv])
        assert after == before, bad


def test_commander_marks_roundtrip_with_real_tmux():
    """印（@cd-commander-*）を付けたセッションが一覧で読み返せること。

    根拠: c1-spec.md「C2 以降で使う印は C1 で付けておく」。tmux の user option は
    list-sessions の形式文字列で読むので、形式を変えたときに壊れないか確かめる。
    tmux が無い環境（フォールバック）では同じ印を in-memory で持つ。
    """
    mgr = TerminalManager()
    session = mgr.create_session(cwd=_root(), command="bash")
    try:
        mgr.tag_session(session["id"], {
            "cli": "claude", "account": "default",
            "label": "検証ラベル", "created_by": "tester",
        })
        listed = next(s for s in mgr.list_sessions() if s["id"] == session["id"])
        assert listed["commander_cli"] == "claude"
        assert listed["commander_account"] == "default"
        assert listed["commander_label"] == "検証ラベル"
        assert listed["commander_created_by"] == "tester"
        # 印の無いセッションには鍵を足さない（既存の Terminal と同じ形を保つ）
        plain = mgr.create_session(cwd=_root())
        try:
            plain_row = next(s for s in mgr.list_sessions() if s["id"] == plain["id"])
            assert not any(k.startswith("commander_") for k in plain_row)
        finally:
            mgr.kill_session(plain["id"])
    finally:
        mgr.kill_session(session["id"])
    assert all(s["id"] != session["id"] for s in mgr.list_sessions())


def test_env_reaches_tmux_session_environment():
    """create_session(env=) の値が tmux のセッション環境に実際に届くこと。

    根拠: アカウント分離はこの 1 本（CLAUDE_CONFIG_DIR など）で成り立つ。
    引数の形だけ見て実際に効かないと意味が無いので、実 tmux で確かめる。
    """
    if not terminal_manager_module.tmux_available():
        return  # tmux の無い環境ではフォールバック経路を別に試験する
    mgr = TerminalManager()
    target_dir = Path(_root()) / "cd-env-probe"
    target_dir.mkdir(exist_ok=True)
    session = mgr.create_session(
        cwd=_root(), command="bash", env={"CLAUDE_CONFIG_DIR": str(target_dir)},
    )
    try:
        shown = subprocess.run(
            ["tmux", "show-environment", "-t", f"cdterm-{session['id']}", "CLAUDE_CONFIG_DIR"],
            capture_output=True, text=True, timeout=10,
        )
        assert shown.returncode == 0, shown.stderr
        assert shown.stdout.strip() == f"CLAUDE_CONFIG_DIR={target_dir}"
    finally:
        mgr.kill_session(session["id"])


# ---------------------------------------------------------------- /consoles API

class FakeManager:
    """tmux を呼ばない置き換え。呼ばれ方（コマンド・env・印）を記録する。"""

    def __init__(self, existing: int = 0) -> None:
        self.existing = existing
        self.created: list[dict] = []
        self.envs: list[dict | None] = []
        self.calls: list[dict] = []
        self.marks: dict[str, dict] = {}
        self.killed: list[str] = []

    def list_sessions(self) -> list[dict]:
        return [{"id": f"{i:08x}"} for i in range(self.existing)]

    def create_session(self, cwd=None, command=None, *, engine="v1", env=None):  # noqa: ANN001
        sid = f"aa{len(self.created):06x}"
        self.existing += 1
        row = {"id": sid, "name": f"cdterm-{sid}", "persistent": True, "engine": engine}
        self.created.append(row)
        self.envs.append(env)
        self.calls.append({"cwd": cwd, "command": command, "env": env})
        return row

    def tag_session(self, sid: str, marks: dict) -> None:
        self.marks[sid] = marks

    def kill_session(self, sid: str) -> None:
        self.killed.append(sid)


def test_consoles_api_flow_default_account_and_batch(admin_client, monkeypatch):
    """コンソールの作成: 既定アカウントは環境変数無し・コマンドは表の実行ファイル名だけ。

    根拠: c1-spec.md「コマンドは CLI の表の実行ファイル名だけ（利用者の入力を混ぜない）」
    「既定アカウントは環境変数を付けない」。監査ログもこの操作で必須。
    """
    fake = FakeManager()
    monkeypatch.setattr(consoles, "manager", fake)
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)

    body = {"cli": "claude", "account": "default", "cwd": _root(), "count": 3, "label": "調査"}
    r = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS, json=body)
    assert r.status_code == 201, r.text
    result = r.json()
    assert len(result["opened"]) == 3
    assert result["not_opened"] == 0
    assert result["reason"] == ""
    assert [row["commander_cli"] for row in result["opened"]] == ["claude"] * 3
    assert len(fake.created) == 3
    for call in fake.calls:
        assert call["command"] == "claude"  # 表の実行ファイル名だけ（利用者の入力を混ぜない）
        assert call["cwd"] == _root()
        assert call["env"] is None  # 既定のアカウント: 環境変数を付けない
    sid = result["opened"][0]["id"]
    assert fake.marks[sid]["cli"] == "claude"
    assert fake.marks[sid]["account"] == "default"
    assert fake.marks[sid]["label"] == "調査"
    assert fake.marks[sid]["created_by"] == "admin"

    with SessionLocal() as db:
        row = db.execute(
            select(AuditLog).where(AuditLog.action == "commander.console.create")
        ).scalars().first()
        assert row is not None
        assert row.resource_id == "claude/default"


def test_consoles_api_named_account_uses_env(admin_client, monkeypatch):
    """名前付きアカウントは CLI の表の環境変数（パスは accounts の管理下だけ）を付ける。"""
    accounts.create_account(accounts.CLIS["claude"], "work")
    fake = FakeManager()
    monkeypatch.setattr(consoles, "manager", fake)
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)

    body = {"cli": "claude", "account": "work", "cwd": _root(), "count": 1}
    r = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS, json=body)
    assert r.status_code == 201, r.text
    env = fake.envs[0]
    assert set(env) == {"CLAUDE_CONFIG_DIR"}
    assert env["CLAUDE_CONFIG_DIR"].startswith(str(accounts.accounts_root()))
    assert "claude" in env["CLAUDE_CONFIG_DIR"]
    assert fake.marks[fake.created[0]["id"]]["account"] == "work"


def test_consoles_api_limit_reports_partial_open(admin_client, monkeypatch):
    """Terminal の上限に当たったら、開けた分と理由を返す（例外にしない）。

    根拠: c1-spec.md「何個開けて何個開けなかったかを返す（上限は設定で変えられる
    ことを案内）」。config.terminal.max_sessions は config/config.yaml で変える。
    """
    fake = FakeManager(existing=9)  # 既定の上限は 10
    monkeypatch.setattr(consoles, "manager", fake)
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)

    body = {"cli": "claude", "cwd": _root(), "count": 3}
    r = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS, json=body)
    assert r.status_code == 201, r.text
    result = r.json()
    assert len(result["opened"]) == 1
    assert result["not_opened"] == 2
    assert "上限（10）" in result["reason"]
    assert "terminal.max_sessions" in result["reason"]


def test_consoles_api_rejects_bad_requests(admin_client, monkeypatch):
    """入力の断り: 不明な CLI・未導入・許可の外の作業フォルダ・無いアカウント・個数。

    根拠: c1-spec.md「作業フォルダは files.resolve で許可ルートの中か検証」「未導入は
    開けないようにする」。個数は 1〜8。
    """
    fake = FakeManager()
    monkeypatch.setattr(consoles, "manager", fake)

    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "ghost", "cwd": _root()},
    ).status_code == 400
    # claude が未導入の環境では断る（この環境に有るかに関わらず判定は is_installed）
    monkeypatch.setattr(accounts, "is_installed", lambda cli: False)
    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "claude", "cwd": _root()},
    ).status_code == 400
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)

    # 作業フォルダが許可の外・存在しない
    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "claude", "cwd": "/etc"},
    ).status_code == 400
    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "claude", "cwd": "/nonexistent-cd-test-dir"},
    ).status_code == 404

    # gemini は名前付きアカウントを断る・無いアカウントは 404
    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "gemini", "account": "work", "cwd": _root()},
    ).status_code == 400
    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "claude", "account": "ghost", "cwd": _root()},
    ).status_code == 404

    # 個数は 1〜8
    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "claude", "cwd": _root(), "count": 9},
    ).status_code == 422
    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "claude", "cwd": _root(), "count": 0},
    ).status_code == 422
    # 表示名の tab は一覧（tab 区切り）を壊すので断る
    assert admin_client.post(
        "/api/v1/commander/consoles", headers=CSRF_HEADERS,
        json={"cli": "claude", "cwd": _root(), "label": "a\tb"},
    ).status_code == 422
    assert fake.created == []


def test_consoles_api_delete_only_marked_sessions(admin_client, monkeypatch):
    """コンソールの削除: 印のあるセッションだけを閉じられる（Terminal と同じ閉じ方）。

    根拠: c1-spec.md「閉じる: Terminal のセッションを閉じるのと同じ」。既存の
    Terminal のセッション（印が無い）を commander から消せないようにする。
    """
    fake = FakeManager(existing=1)
    monkeypatch.setattr(consoles, "manager", fake)
    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)

    assert admin_client.delete(
        "/api/v1/commander/consoles/0123abcd", headers=CSRF_HEADERS,
    ).status_code == 404

    # 一覧は印のあるものだけ（fake の一覧に印を付けて出す）
    fake.list_sessions = lambda: [{
        "id": "0123abcd", "commander_cli": "claude", "commander_account": "default",
    }]
    r = admin_client.delete("/api/v1/commander/consoles/0123abcd", headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "0123abcd" in fake.killed
    with SessionLocal() as db:
        row = db.execute(
            select(AuditLog).where(AuditLog.action == "commander.console.kill")
        ).scalars().first()
        assert row is not None
        assert row.resource_id == "0123abcd"
