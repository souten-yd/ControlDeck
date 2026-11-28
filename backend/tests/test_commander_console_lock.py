"""Nexarch のコンソールのロック（中央の保護・保存・検知・自動復旧）の試験。

目的: ロックをかけたコンソールが誰にも勝手に閉じられないことを守る。
- 中央の保護: manager.kill_session はロックの印（@cd-commander-locked=1）のあるセッションを
  断る（Terminal の DELETE・Nexarch の DELETE・一括で閉じる・コードの他の呼び出し元のどれでも）。
  強制解除の抜け道は無い。外すのは画面（terminal.use）の操作だけ。
- 保存: ロックしたコンソールの情報（cli・account・cwd・label・model・effort・owner）を
  data/ai-commander の小さな JSON（0600・置き換え）へ残す。tmux が消えた後も分かる。
- 検知: ロックしたセッションが ControlDeck を通さずに消えたら監査（commander.console.vanished）
  と通知（タイトルだけ）。tmux の確認に失敗しただけでは消失と扱わない。dead pane は消失ではない。
- 自動復旧: 同じ cli・account・作業フォルダ・label で開き直し、続きから再開する
  （Claude --continue・Codex resume --last・OpenCode は既存の再開）。1 つのコンソールで
  10 分 3 回まで（失敗も数える・ID が変わっても記録は引き継ぐ）。設定で切れる（既定は入）。

根拠: 2026-11-28 利用者「開いているコンソールごとに、ロックをかけるようにしてほしい。
勝手に tmux とかを停止させられないようにしてほしい。ロックをかけたものは落とさないように」
（.nexarch/brief-console-lock.md）。ControlDeck の中からの kill は元々監査に残っていたが、
外のプロセスが tmux kill-session / kill-server を直接打つと記録に残らず消えるので、
印による保護・保存による検知・復旧の歯止めを仕組みで守る。API の契約（PUT …/lock の
body={locked:boolean}・応答={ok:true,locked:boolean}・settings の auto_restore）は
frontend の別担当と固定しているので、ここで契約どおりであることを機械的に確かめる。
2026-11-28 Nexarch #5 の根拠: show-options の timeout / OSError / 非0終了で
kill に進む、has-session の permission denied を消失と誤認、解除後の JSON 書込失敗を
成功にする不具合を実測。失敗注入・Event で順序を固定した競合・復旧途中の再起動を
同じファイルで守る。実 tmux は全 backend 共通の PATH wrapper により明示 -L へ隔離する。
既存の試験に同じ観点は無い（./deck.sh test-catalog で確認。test_terminals は engine・PTY の
既存の振る舞い、test_commander_consoles は印と env、test_commander_resume は上限からの再開）。
"""
from __future__ import annotations

import json
import asyncio
import threading
import time
import os
import stat as stat_mod
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

import app.terminals.manager as manager_module
from app.commander import consoles, locks
from app.database import SessionLocal
from app.models import AuditLog
from app.terminals.manager import SessionLockedError, TerminalManager
from tests.conftest import CSRF_HEADERS

LOCK_DETAIL = "ロック中です。先にロックを外してください"


def _root() -> str:
    from app.config import get_config

    return str(Path(get_config().files.allowed_roots[0]).resolve())


def _row(sid="0123abcd", **kw):
    row = {
        "id": sid, "name": f"cdterm-{sid}", "commander_cli": "claude",
        "commander_account": "default", "commander_label": "検証ラベル",
        "commander_model": "sonnet", "commander_effort": "high",
        "commander_created_by": "admin", "alive": True, "cwd": "~",
    }
    row.update(kw)
    return row


class FakeManager:
    """tmux を呼ばない置き換え。ロックの印（set_locked）・一覧・殺しを記録する。"""

    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or [])}
        self.locked: set[str] = set()
        self.set_locked_calls: list[tuple[str, bool]] = []
        self.killed: list[str] = []
        self.tags: dict[str, dict] = {}
        self.cleared: list[tuple[str, tuple]] = []
        self.created: list[dict] = []
        self.exists: dict[str, bool] = {}
        self.exists_error: Exception | None = None
        self.fail_set_locked = False

    def list_sessions(self):
        out = []
        for sid, row in self.rows.items():
            item = dict(row)
            if sid in self.locked:
                item["commander_locked"] = "1"
            out.append(item)
        return out

    def create_session(self, cwd=None, command=None, *, engine="v1", env=None, session_id=None):  # noqa: ANN001
        sid = session_id or f"bb{len(self.created):06x}"
        self.created.append({"cwd": cwd, "command": command, "env": env})
        self.rows[sid] = _row(sid, commander_label="", cwd=cwd or "~")
        return {"id": sid, "name": f"cdterm-{sid}", "persistent": True, "engine": engine}

    def tag_session(self, sid, marks):
        self.tags.setdefault(sid, {}).update(marks)

    def clear_marks(self, sid, keys):
        self.cleared.append((sid, tuple(keys)))

    def capture_text(self, sid, lines=60):  # noqa: ARG002
        return ""

    def kill_session(self, sid):
        if sid in self.locked:
            raise SessionLockedError(LOCK_DETAIL)
        self.killed.append(sid)

    def set_locked(self, sid, locked):
        if self.fail_set_locked:
            raise manager_module.SessionStateError("tmux の操作に失敗しました")
        self.set_locked_calls.append((sid, bool(locked)))
        if locked:
            self.locked.add(sid)
        else:
            self.locked.discard(sid)

    def is_locked(self, sid):
        return sid in self.locked

    def session_exists(self, sid):
        if self.exists_error is not None:
            raise self.exists_error
        return self.exists.get(sid, sid in self.rows)

    def session_cwd(self, sid):
        return str(self.rows.get(sid, {}).get("cwd") or "")


def _fake(monkeypatch, fake):
    """consoles と manager の両方を置き換える（endpoint は import の仕方に沿って両方を見る）。"""
    monkeypatch.setattr(manager_module, "manager", fake)
    monkeypatch.setattr(consoles, "manager", fake)


def _store_path() -> Path:
    from app.config import data_dir

    return data_dir() / "ai-commander" / "console-locks.json"


def _settings_path() -> Path:
    from app.config import data_dir

    return data_dir() / "ai-commander" / "settings.json"


@pytest.fixture(autouse=True)
def _clean_lock_store():
    """鍵の保存と Nexarch の設定を試験ごとに元へ戻す（data は試験全体で 1 つの置き場）。"""
    saved_settings = _settings_path().read_bytes() if _settings_path().is_file() else None
    _store_path().unlink(missing_ok=True)
    yield
    _store_path().unlink(missing_ok=True)
    if saved_settings is not None:
        _settings_path().write_bytes(saved_settings)
    else:
        _settings_path().unlink(missing_ok=True)


def _store():
    try:
        return json.loads(_store_path().read_text(encoding="utf-8")).get("locks", {})
    except (OSError, ValueError):
        return {}


def _audit_rows(action: str, resource_id: str = ""):
    with SessionLocal() as db:
        q = select(AuditLog).where(AuditLog.action == action)
        if resource_id:
            q = q.where(AuditLog.resource_id == resource_id)
        return db.execute(q.order_by(AuditLog.id)).scalars().all()


# ---------------------------------------------------------------- 中央の保護（manager）

def test_manager_refuses_to_kill_locked_session_without_bypass(monkeypatch):
    """ロックしたセッションは manager.kill_session でも殺せない（中央の保護・抜け道無し）。

    根拠: brief-console-lock.md「強制解除抜け道なし」。保護が endpoint 側だけだと、
    新しい呼び出し元・一括処理が印を見落として閉じられる。tmux に印を見に行く
    show-options を先に打ち、ロックなら kill-session を 1 度も打たない。
    """
    locked_sessions: set[str] = set()
    kill_calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003 - subprocess 互換
        if "kill-session" in argv:
            kill_calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0)
        if "show-options" in argv and "@cd-commander-locked" in argv:
            value = "1\n" if argv[argv.index("-t") + 1].removeprefix("cdterm-") in locked_sessions else ""
            return subprocess.CompletedProcess(argv, 0 if value else 1, stdout=value,
                                               stderr="" if value else "invalid option: @cd-commander-locked\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(manager_module.subprocess, "run", fake_run)
    mgr = TerminalManager()
    sid = "aa000001"
    mgr.set_locked(sid, True)
    locked_sessions.add(sid)
    try:
        mgr.kill_session(sid)
        raise AssertionError("ロックしたセッションを殺せてしまった")
    except SessionLockedError as exc:
        assert str(exc) == LOCK_DETAIL
    assert kill_calls == [], "ロック中に kill-session を打ってはいけない"
    # 外せば殻だけのセッションと同じく殺せる
    mgr.set_locked(sid, False)
    locked_sessions.discard(sid)
    mgr.kill_session(sid)
    assert kill_calls and "kill-session" in kill_calls[0]


def test_manager_lock_survives_on_fallback_pty_and_kill_refuses(monkeypatch):
    """tmux の無い環境（フォールバック PTY）でも同じ保護が効くこと。"""
    monkeypatch.setattr(manager_module, "tmux_available", lambda: False)
    mgr = TerminalManager()
    session = mgr.create_session(cwd=_root())
    sid = session["id"]
    try:
        mgr.set_locked(sid, True)
        try:
            mgr.kill_session(sid)
            raise AssertionError("ロックしたフォールバックセッションを殺せてしまった")
        except SessionLockedError:
            pass
        assert mgr.is_locked(sid) is True
        assert mgr.session_exists(sid) is True
    finally:
        mgr.set_locked(sid, False)
        mgr.kill_session(sid)
    assert mgr.session_exists(sid) is False


def test_locked_mark_roundtrip_and_kill_refusal_with_real_tmux():
    """実 tmux（試験専用のソケット）で: 印が一覧で読み返せ、ロック中は殺せず、外せば殺せる。

    根拠: 印（@cd-commander-*）は list-sessions の形式文字列で読み返すので、鍵を足したときに
    形式が壊れていないか実物で確かめる。ソケットは conftest の TMUX_TMPDIR で本番と隔離
    （本番のソケットへは触らない）。
    """
    assert os.environ.get("CONTROL_DECK_TEST_TMUX_SOCKET"), "試験の tmux ソケット隔離（conftest）が効いていない"
    mgr = TerminalManager()
    session = mgr.create_session(cwd=_root(), command="bash")
    sid = session["id"]
    try:
        mgr.tag_session(sid, {"cli": "claude", "account": "default"})
        mgr.set_locked(sid, True)
        listed = next(s for s in mgr.list_sessions() if s["id"] == sid)
        assert listed["commander_cli"] == "claude"
        assert listed["commander_locked"] == "1"
        assert mgr.is_locked(sid) is True
        assert mgr.session_exists(sid) is True, "dead pane ではなくセッションは在る"
        try:
            mgr.kill_session(sid)
            raise AssertionError("ロックしたセッションを殺せてしまった")
        except SessionLockedError:
            pass
        mgr.set_locked(sid, False)
        assert mgr.is_locked(sid) is False
        mgr.kill_session(sid)
        assert mgr.session_exists(sid) is False
    finally:
        if mgr.session_exists(sid):
            mgr.set_locked(sid, False)
            mgr.kill_session(sid)


# ---------------------------------------------------------------- API: 鍵の付け外し・保存

def test_lock_api_roundtrip_persists_console_info(admin_client, monkeypatch):
    """PUT /consoles/{id}/lock: {locked:boolean} → {ok:true,locked:boolean}。情報の保存は 0600。"""
    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)

    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "locked": True}
    assert fake.set_locked_calls == [("0123abcd", True)]
    path = _store_path()
    assert stat_mod.S_IMODE(path.stat().st_mode) == 0o600
    entry = _store()["0123abcd"]
    assert entry["cli"] == "claude" and entry["account"] == "default"
    assert entry["label"] == "検証ラベル"
    assert entry["model"] == "sonnet" and entry["effort"] == "high"
    assert entry["owner"] == "admin" and entry["owner_id"] is not None
    assert entry["cwd"] == "~", "セッションの作業フォルダの実体のパス（session_cwd）を保存する"
    assert entry["state"] == "locked" and entry["attempts"] == []
    assert _audit_rows("commander.console.lock", "0123abcd"), "監査に残す"

    # 一覧は boolean（frontend との契約）
    fake.rows["0123abcd"]["cwd"] = str(Path(_root()))
    listed = admin_client.get("/api/v1/commander/consoles").json()["consoles"]
    row = next(c for c in listed if c["id"] == "0123abcd")
    assert row["commander_locked"] is True
    assert isinstance(row["commander_locked"], bool)

    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": False})
    assert r.status_code == 200 and r.json() == {"ok": True, "locked": False}
    assert fake.set_locked_calls[-1] == ("0123abcd", False)
    assert "0123abcd" not in _store()
    assert _audit_rows("commander.console.unlock", "0123abcd")


def test_lock_api_missing_console_and_idempotent_toggle(admin_client, monkeypatch):
    """無いコンソールは 404。既にロック済み・解除済みのままの要求はそのまま通す。"""
    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)

    assert admin_client.put("/api/v1/commander/consoles/ffffffff/lock", headers=CSRF_HEADERS,
                            json={"locked": True}).status_code == 404
    assert admin_client.put("/api/v1/commander/consoles/ffffffff/lock", headers=CSRF_HEADERS,
                            json={"locked": False}).status_code == 404
    # 同じ状態への切替は冪等（保存を壊さない）
    admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS, json={"locked": True})
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": True})
    assert r.status_code == 200 and r.json()["locked"] is True
    assert _store()["0123abcd"]["attempts"] == []
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": False})
    assert r.status_code == 200 and r.json()["locked"] is False
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": False})
    assert r.status_code == 200 and r.json()["locked"] is False


def test_unlock_accepts_vanished_console(admin_client, monkeypatch):
    """消えた（tmux に無い）ロックのコンソールでも、解除で保存の記録を消せる。

    根拠: 検知後にセッションがもう無いと画面の一覧へ出ないので、保存だけでも
    消せる道が無いと記録が永遠に残る。ロックの付け外しは画面（terminal.use）だけ。
    """
    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)
    admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS, json={"locked": True})
    # 外から消えた状態を作る（一覧から消える）
    fake.rows.clear()
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": False})
    assert r.status_code == 200 and r.json() == {"ok": True, "locked": False}
    assert _store() == {}


def test_lock_adopts_stale_vanished_record_for_same_session(admin_client, monkeypatch):
    """同じセッションを指す「消えたまま」の記録へ改めて鍵をかけたら、その記録を採用して戻す。

    根拠: レビュー指摘（2026-11-28 minor 4）。復旧の途中で残った vanished の記録を放って
    おくと、見回りが複製を開き直し得る。かけ直しても同じ論理コンソールの復旧回数を保持する。
    """
    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)
    locks._write({"locks": {"0123abcd": {
        "session_id": "0123abcd", "cli": "claude", "account": "default", "cwd": _root(),
        "label": "検証ラベル", "owner": "admin", "locked_at": 100.0, "state": "vanished",
        "attempts": [100.0, 110.0, 120.0], "restored_count": 0, "gave_up_at": 130.0,
    }}})
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "locked": True}
    store = _store()
    assert list(store) == ["0123abcd"], "新しい記録を足さず、既の記録を採用する"
    assert store["0123abcd"]["state"] == "locked"
    assert store["0123abcd"]["session_id"] == "0123abcd"
    assert store["0123abcd"]["attempts"] == [100.0, 110.0, 120.0] and store["0123abcd"]["gave_up_at"] == 130.0


def test_lock_does_not_mark_when_store_write_fails(admin_client, monkeypatch):
    """保存に書けなかったら印を変更せず失敗を返す。

    根拠: brief-console-lock.md「ロック切替とkill/復旧は競合制御し、データ保存失敗・
    tmux一覧の失敗を消失と誤認しない」。情報を残せないロックは検知・復旧が
    できなくなるので、付ける途中で失敗したら元へ戻す。
    """
    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)

    def fail_write(data):  # noqa: ANN001
        raise OSError("disk full")

    monkeypatch.setattr(locks, "_write", fail_write)
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": True})
    assert r.status_code == 503, r.text
    assert fake.set_locked_calls == [], "書けないときは印を変えない"
    assert _store() == {}
    # 監査には残さない（失敗して鍵は付いていないので lock の出来事では無い）


# ---------------------------------------------------------------- 閉じる経路の 409

def test_delete_console_locked_returns_409_before_closing_streams(admin_client, monkeypatch):
    """Nexarch の DELETE は、ロック中なら streams.close_session より前に 409 で断る。"""
    from app.commander import router as commander_router

    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)
    admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS, json={"locked": True})
    closed: list[str] = []
    monkeypatch.setattr(commander_router.streams, "close_session",
                        lambda sid: closed.append(sid))

    r = admin_client.delete("/api/v1/commander/consoles/0123abcd", headers=CSRF_HEADERS)
    assert r.status_code == 409 and r.json()["detail"] == LOCK_DETAIL
    assert closed == [], "WS の接続を閉じてはいけない（閉じる前の拒否）"
    assert fake.killed == []
    # 外せば今までどおり閉じられる
    admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS, json={"locked": False})
    r = admin_client.delete("/api/v1/commander/consoles/0123abcd", headers=CSRF_HEADERS)
    assert r.status_code == 200 and closed == ["0123abcd"] and fake.killed == ["0123abcd"]


def test_terminal_delete_locked_returns_409(admin_client, monkeypatch):
    """Terminal の DELETE も、ロック中なら 409 で断る（コンソールは Terminal のセッション）。"""
    from app.terminals import router as terminal_router

    fake = FakeManager([_row()])
    monkeypatch.setattr(manager_module, "manager", fake)
    monkeypatch.setattr(consoles, "manager", fake)
    monkeypatch.setattr(terminal_router, "manager", fake)
    fake.set_locked("0123abcd", True)
    closed: list[str] = []
    monkeypatch.setattr(terminal_router.streams, "close_session", lambda sid: closed.append(sid))

    r = admin_client.delete("/api/v1/terminals/0123abcd", headers=CSRF_HEADERS)
    assert r.status_code == 409 and r.json()["detail"] == LOCK_DETAIL
    assert closed == [] and fake.killed == []
    # ロックしていないセッションは今までどおり閉じられる
    fake.set_locked("0123abcd", False)
    r = admin_client.delete("/api/v1/terminals/0123abcd", headers=CSRF_HEADERS)
    assert r.status_code == 200 and fake.killed == ["0123abcd"]


def test_bulk_close_skips_locked_and_returns_counts(admin_client, monkeypatch):
    """DELETE /consoles（一括で閉じる）: ロックは飛ばして数を返す。

    根拠: brief-console-lock.md「一括は飛ばして数を返す」。既存の一括・チームの片付けの
    経路は調べたところコンソールを閉じない（teams.delete_team は印を外すだけ）なので、
    利用者の一括 UI 用のこの 1 本だけがある。
    """
    from app.commander import router as commander_router

    fake = FakeManager([_row("0123abcd"), _row("0123abce"), _row("0123abcf")])
    _fake(monkeypatch, fake)
    closed: list[str] = []
    monkeypatch.setattr(commander_router.streams, "close_session", lambda sid: closed.append(sid))
    fake.set_locked("0123abce", True)

    r = admin_client.delete("/api/v1/commander/consoles", headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json() == {"closed": 2, "skipped_locked": 1}
    assert sorted(fake.killed) == ["0123abcd", "0123abcf"]
    assert sorted(closed) == ["0123abcd", "0123abcf"]
    row = _audit_rows("commander.console.close_all")
    assert row, "一括の監査にも残す"
    meta = json.loads(row[-1].metadata_json or "{}")
    assert meta == {"closed": 2, "skipped_locked": 1}


# ---------------------------------------------------------------- 設定（自動で戻す）

def test_settings_auto_restore_default_on_and_keeps_resume_message(admin_client):
    """settings の auto_restore: 既定は true。resume_message を省略してもリセットしない。

    根拠: 契約「settings GET/PUT auto_restore:boolean（既定true、既存resume_messageを欠落で
    リセットしない）」。既存の PUT は resume_message を常に書き換えていたので、auto_restore
    だけ変える呼び出しで文言が既定へ戻らないようにする。
    """
    r = admin_client.get("/api/v1/commander/settings")
    assert r.status_code == 200
    assert r.json()["auto_restore"] is True

    r = admin_client.put("/api/v1/commander/settings", headers=CSRF_HEADERS,
                         json={"resume_message": "上限が戻ったので、続きをお願いします"})
    assert r.status_code == 200
    r = admin_client.put("/api/v1/commander/settings", headers=CSRF_HEADERS,
                         json={"auto_restore": False})
    assert r.status_code == 200 and r.json()["auto_restore"] is False
    assert r.json()["resume_message"] == "上限が戻ったので、続きをお願いします", "欠落でリセットしない"
    assert admin_client.get("/api/v1/commander/settings").json()["auto_restore"] is False
    # 戻せる
    assert admin_client.put("/api/v1/commander/settings", headers=CSRF_HEADERS,
                            json={"auto_restore": True}).json()["auto_restore"] is True


# ---------------------------------------------------------------- 続きから開き直すコマンド

def test_create_consoles_resume_wiring_reaches_the_command(monkeypatch):
    """create_consoles(resume=True) から _command_for まで resume が届き、コマンドへ --continue が付く。

    根拠: レビュー指摘（2026-11-28）。locks._recreate は create_consoles(resume=True) を呼ぶが、
    この配線が切れると復旧が毎回まっさらな会話になる（通知・docs と不整合）。モックで
    create_consoles を置き換える試験では見えない結合部分を、偽 manager で通す。
    """
    from app.commander import accounts
    from app.commander.accounts import CLIS

    monkeypatch.setattr(accounts, "is_installed", lambda cli: True)
    monkeypatch.setattr(accounts, "executable_path", lambda cli: f"/opt/{cli.id}")
    fake = FakeManager([])
    monkeypatch.setattr(consoles, "manager", fake)
    work = _root()
    Path(work, "cd-lock-probe").mkdir(exist_ok=True)

    result = consoles.create_consoles(
        cli=CLIS["claude"], account="default", cwd=work, count=1,
        label="", created_by="tester", model="", effort="", resume=True)
    assert result["opened"] and result["not_opened"] == 0
    assert " --continue" in fake.created[0]["command"], fake.created[0]["command"]
    # 通常の作成（resume 無し）には付けない
    consoles.create_consoles(cli=CLIS["claude"], account="default", cwd=work, count=1,
                            label="", created_by="tester")
    assert " --continue" not in fake.created[1]["command"]


def test_restore_command_for_each_cli(monkeypatch):
    """開き直しは続きから: Claude --continue・Codex resume --last・OpenCode は既存の再開（--continue）。"""
    from app.commander.accounts import CLIS
    from app.integrations.opencode import provider

    monkeypatch.setattr(consoles.accounts, "executable_path", lambda cli: f"/opt/{cli.id}")

    # Claude: 既存の引数（model・effort・statusline）に --continue が付く
    command, _env = consoles._command_for(CLIS["claude"], "/data1tb/work", 1,
                                          model="sonnet", effort="high", resume=True)
    assert " --model sonnet" in command and " --effort high" in command and " --continue" in command
    # 通常の開く（resume 無し）には付けない
    command, _env = consoles._command_for(CLIS["claude"], "/data1tb/work", 1)
    assert " --continue" not in command

    # Codex: resume --last（既存の forced_login_method はそのまま）
    command, _env = consoles._command_for(CLIS["codex"], "/data1tb/work", 1, resume=True)
    assert " resume --last" in command
    assert "forced_login_method" in command

    # OpenCode: 既存の tui_command(resume="last")（--continue）を再利用
    captured: list[dict] = []

    def fake_tui(*, project_path, owner_user_id=None, model="", config_key="", resume="", **_):  # noqa: ANN001
        captured.append({"resume": resume, "model": model})
        return "XDG_CONFIG_HOME=/x exec /opt/opencode2 --continue /data1tb/work", project_path

    monkeypatch.setattr(consoles.accounts, "opencode_connection", lambda account: {"model": "auto"})
    monkeypatch.setattr(provider, "tui_command", fake_tui)
    command, env = consoles._command_for(CLIS["opencode"], "/data1tb/work", 1, resume=True)
    assert captured == [{"resume": "last", "model": "auto"}]
    assert "--continue" in command and env is None


# ---------------------------------------------------------------- 検知と自動復旧（偽 manager）

def _lock_now(admin_client, fake, monkeypatch, sid="0123abcd"):
    _fake(monkeypatch, fake)
    r = admin_client.put(f"/api/v1/commander/consoles/{sid}/lock", headers=CSRF_HEADERS,
                         json={"locked": True})
    assert r.status_code == 200, r.text


def _spy_notify(monkeypatch):
    from app.alerts import events

    calls: list[dict] = []
    monkeypatch.setattr(events, "notify_event",
                        lambda kind, **kw: calls.append({"kind": kind, **kw}) or {"users": 0})
    return calls


def _spy_create(monkeypatch, result=None):
    calls: list[dict] = []

    def fake_create(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        if result is not None:
            return result
        sid = kwargs["session_id"]
        fake = manager_module.manager
        fake.rows[sid] = _row(sid)
        return {"opened": [{"id": sid}], "not_opened": 0, "reason": ""}

    monkeypatch.setattr(consoles, "create_consoles", fake_create)
    return calls


def test_vanished_detection_audits_and_notifies_then_restores(admin_client, monkeypatch):
    """外から消えたら検知（監査・通知はタイトルだけ）し、同じ情報で開き直してまた鍵を付ける。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    notify = _spy_notify(monkeypatch)
    created = _spy_create(monkeypatch)

    fake.rows.clear()  # 外のプロセスが kill-session したのと同じ状態
    fake.exists["0123abcd"] = False
    events = locks.check(now=1000.0)

    assert [e["event"] for e in events] == ["vanished", "restored"]
    assert _audit_rows("commander.console.vanished", "0123abcd")
    assert _audit_rows("commander.console.restored", "0123abcd")
    assert notify[0]["kind"] == "console_vanish" and "外から" in notify[0]["title"]
    assert notify[1]["kind"] == "console_vanish" and "開き直しました" in notify[1]["title"]
    assert notify[0]["body"] == "" and notify[1]["body"] == "", "通知はタイトルだけ"
    # 開き直し: 同じ cli・account・作業フォルダ・label・model・effort・owner で 1 個、続きから
    assert created[0]["cli"].id == "claude"
    assert created[0]["account"] == "default"
    assert created[0]["cwd"] == "~"
    assert created[0]["count"] == 1
    assert created[0]["label"] == "検証ラベル"
    assert created[0]["model"] == "sonnet" and created[0]["effort"] == "high"
    assert created[0]["created_by"] == "admin"
    assert created[0]["user_id"] is not None
    assert created[0]["resume"] is True
    # 新しいセッションにも鍵を付け、記録は ID が変わっても引き継ぐ
    assert (created[0]["session_id"], True) in fake.set_locked_calls
    entry = _store()["0123abcd"]
    assert entry["session_id"] == created[0]["session_id"] and entry["state"] == "locked"
    assert entry["restored_count"] == 1
    assert entry["attempts"] == [1000.0], "試みた時刻を保存する（再起動でも制限を維持）"
    assert entry["cli"] == "claude" and entry["label"] == "検証ラベル"


def test_detection_does_not_mistake_failure_or_dead_pane_for_vanished(admin_client, monkeypatch):
    """tmux の確認に失敗しても消失と扱わない。dead pane（セッションは在る）も消失ではない。

    根拠: brief-console-lock.md「データ保存失敗・tmux一覧の失敗を消失と誤認しない」
    「dead pane とセッション消失を混同しない」。誤認は復旧の空振りと通知の誤報になる。
    """
    fake = FakeManager([_row(alive=False)])  # dead pane: セッションは在る
    _lock_now(admin_client, fake, monkeypatch)
    notify = _spy_notify(monkeypatch)

    fake.exists["0123abcd"] = True
    assert locks.check(now=1000.0) == []
    assert _store()["0123abcd"]["state"] == "locked" and notify == []

    fake.exists_error = OSError("tmux socket down")
    assert locks.check(now=2000.0) == []
    assert _store()["0123abcd"]["state"] == "locked", "確認の失敗を消失にしてはいけない"
    assert notify == []


def test_restore_attempts_are_capped_at_three_per_ten_minutes(admin_client, monkeypatch):
    """復旧は 1 つの論理コンソールで 10 分 3 回まで（失敗も数える・上限で諦めたことを残す）。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    notify = _spy_notify(monkeypatch)
    created = _spy_create(monkeypatch, result={"opened": [], "not_opened": 1, "reason": "開けなかった"})
    fake.rows.clear()
    fake.exists["0123abcd"] = False

    # 1 回目: 検知 → 試みる → 失敗。試みた時刻は保存に残る
    events = locks.check(now=1000.0)
    assert [e["event"] for e in events] == ["vanished", "failed"]
    assert _store()["0123abcd"]["attempts"] == [1000.0]

    # 窓の中に 3 回 → これ以上試みない（ループしない）
    entry = _store()["0123abcd"]
    entry["attempts"] = [1100.0, 1150.0, 1160.0]
    locks._write({"locks": {"0123abcd": entry}})
    created.clear()
    events = locks.check(now=1200.0)
    assert created == [], "10 分 3 回に達したら開き直さない"
    assert [e["event"] for e in events] == ["gave_up"]
    assert _audit_rows("commander.console.restore_gave_up", "0123abcd")
    assert any("諦め" in n["title"] or "止め" in n["title"] for n in notify), notify
    # 上限の監査・通知は 1 度だけ（見回りのたびに繰り返さない）
    gave_up_before = len(_audit_rows("commander.console.restore_gave_up", "0123abcd"))
    notify.clear()
    assert locks.check(now=1260.0) == []
    assert len(_audit_rows("commander.console.restore_gave_up", "0123abcd")) == gave_up_before
    assert notify == []

    # 窓の外に古い試みが流れれば、また試みてよい（ID が変わっても記録は同じ論理コンソールで持つ）
    entry = _store()["0123abcd"]
    entry["attempts"] = [1160.0, 300.0, 310.0]
    locks._write({"locks": {"0123abcd": entry}})
    created.clear()
    events = locks.check(now=1600.0)
    assert len(created) == 1 and [e["event"] for e in events] == ["failed"]
    assert _store()["0123abcd"]["attempts"][-1] == 1600.0


def test_auto_restore_off_means_detection_only(admin_client, monkeypatch):
    """設定で自動で戻すを切ったら、検知（監査・通知）だけにする。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    created = _spy_create(monkeypatch)
    r = admin_client.put("/api/v1/commander/settings", headers=CSRF_HEADERS,
                         json={"auto_restore": False})
    assert r.status_code == 200

    fake.rows.clear()
    fake.exists["0123abcd"] = False
    events = locks.check(now=1000.0)
    assert [e["event"] for e in events] == ["vanished"]
    assert created == []
    assert _audit_rows("commander.console.vanished", "0123abcd")
    assert _store()["0123abcd"]["state"] == "vanished"
    assert _store()["0123abcd"]["attempts"] == [], "切れている間は回数を消費しない"


def test_restore_failure_is_audited_and_counted(admin_client, monkeypatch):
    """復旧の失敗（開けない・作業フォルダが消えた等）も監査に残し、回数に数える。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    created = _spy_create(monkeypatch, result={"opened": [], "not_opened": 1, "reason": "セッション数の上限"})
    fake.rows.clear()
    fake.exists["0123abcd"] = False

    events = locks.check(now=1000.0)
    assert [e["event"] for e in events] == ["vanished", "failed"]
    row = _audit_rows("commander.console.restore_failed", "0123abcd")
    assert row and "上限" in json.loads(row[-1].metadata_json or "{}").get("reason", "")
    assert _store()["0123abcd"]["attempts"] == [1000.0], "失敗も回数に数える"
    assert _store()["0123abcd"]["state"] == "restoring"


def test_resume_tick_runs_the_lock_watch(admin_client, monkeypatch):
    """既存の resume の見回り（60 秒ごとの thread）から検知が動く（監視を増やしすぎない）。"""
    from app.commander import resume

    fake = FakeManager([])
    _fake(monkeypatch, fake)
    monkeypatch.setattr(consoles, "list_consoles", lambda: [])
    checked: list[float] = []
    monkeypatch.setattr(locks, "check", lambda now=None: checked.append(now or 0) or [])
    resume.tick(now=5000.0)
    assert checked == [5000.0]


# ---------------------------------------------------------------- 診断レポート

def test_diagnostics_report_shows_lock_state_and_events(admin_client, monkeypatch):
    """診断レポートで外部消失・復旧失敗・上限が分かる（locks の今の状態＋監査の履歴）。"""
    from app.audit import service as audit
    from app.diagnostics import journal, report, selftest

    monkeypatch.setattr(journal, "summary",
                        lambda hours=72.0: {"hours": hours, "restarts": [], "unit_failures": [], "oom_kills": []})
    monkeypatch.setattr(selftest, "_failed_units", lambda: (True, "なし"))

    entry = dict(_row())
    entry.update({"session_id": "0123abcd", "cli": "claude", "account": "default", "cwd": "/data1tb/w",
                  "label": "診断ラベル", "owner": "admin", "state": "vanished", "attempts": [100.0, 200.0, 300.0],
                  "restored_count": 2, "gave_up_at": 350.0, "locked_at": 50.0})
    locks._write({"locks": {"0123abcd": entry}})
    with SessionLocal() as db:
        audit.record(db, "commander.console.restore_gave_up", user=None, resource_type="commander_console",
                     resource_id="0123abcd", metadata={"label": "診断ラベル", "session_id": "bb000009"})

    data = report.build(hours=1.0)
    cl = data["console_locks"]
    assert cl["summary"]["vanished"] == 1 and cl["summary"]["locked"] == 0
    assert cl["summary"]["attempts_10min"] == 0, "窓の外の古い試みは数えない"
    assert any(e["action"] == "commander.console.restore_gave_up" and e["name"] == "診断ラベル"
               for e in cl["events"])

    md = admin_client.get("/api/v1/diagnostics/report?format=md").text
    assert "Nexarch のコンソールのロック" in md
    assert "診断ラベル" in md


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("tmux", 5), OSError("socket down"),
    subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied"),
    subprocess.CompletedProcess([], 2, stdout="", stderr="unknown failure"),
])
def test_unknown_lock_state_never_reaches_kill(monkeypatch, failure):
    """印を確認できない（timeout・OSError・権限・不明な非 0）ときは kill-session を打たない（fail-open にしない）。"""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(manager_module, "tmux_available", lambda: True)
    monkeypatch.setattr(manager_module.subprocess, "run", run)
    with pytest.raises(manager_module.SessionStateError):
        TerminalManager().kill_session("0123abcd")
    assert not any("kill-session" in a for a in calls)


@pytest.mark.parametrize("error,missing", [
    ("can't find session: cdterm-0123abcd", True),
    ("no server running on /tmp/dedicated/socket", True),
    ("error connecting to /tmp/dedicated/socket (No such file or directory)", True),
    ("error connecting to /tmp/dedicated/socket (Permission denied)", False),
    ("error connecting to /tmp/dedicated/socket (Connection refused)", False),
    ("permission denied", False), ("", False), ("unknown error", False),
])
def test_has_session_distinguishes_missing_from_unknown(monkeypatch, error, missing):
    """has-session の非 0 のうち、実測した欠落の文言だけを「無い」とし、権限・通信・不明は確認不能にする。"""
    monkeypatch.setattr(manager_module, "tmux_available", lambda: True)
    monkeypatch.setattr(manager_module.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 1, stdout="", stderr=error))
    if missing:
        assert TerminalManager().session_exists("0123abcd") is False
    else:
        with pytest.raises(manager_module.SessionStateError):
            TerminalManager().session_exists("0123abcd")


@pytest.mark.parametrize("operation", ["kill", "clear"])
def test_tmux_mutation_failure_is_not_success(monkeypatch, operation):
    """kill-session・印の解除の失敗を成功として扱わない。"""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "show-options" in argv:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="invalid option: @cd-commander-locked")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="permission denied")

    monkeypatch.setattr(manager_module, "tmux_available", lambda: True)
    monkeypatch.setattr(manager_module.subprocess, "run", run)
    mgr = TerminalManager()
    with pytest.raises(manager_module.SessionStateError):
        if operation == "kill":
            mgr.kill_session("0123abcd")
        else:
            mgr.clear_marks("0123abcd", ("locked",))
    assert any(("kill-session" if operation == "kill" else "set-option") in a for a in calls)


def test_unlock_store_failure_relocks_and_returns_failure(admin_client, monkeypatch):
    """解除の保存に失敗したら印を戻して 503。保存の記録が残る限り中央 guard も閉じさせない。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    before = _store_path().read_bytes()
    before_audit = len(_audit_rows("commander.console.unlock", "0123abcd"))
    monkeypatch.setattr(locks, "_write", lambda data: (_ for _ in ()).throw(OSError("disk full")))
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": False})
    assert r.status_code == 503
    assert _store_path().read_bytes() == before
    assert fake.is_locked("0123abcd")
    assert len(_audit_rows("commander.console.unlock", "0123abcd")) == before_audit
    # 印の rollback 通信が失敗しても durable guard が終了を拒否する。
    mgr = TerminalManager()
    monkeypatch.setattr(mgr, "is_locked", lambda sid: False)
    with pytest.raises(SessionLockedError):
        mgr.kill_session("0123abcd")


def test_unlock_clear_failure_keeps_record_and_returns_failure(admin_client, monkeypatch):
    """印の解除が不確か（返信が失われた）なら記録を消さず 503。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    original = fake.set_locked

    def uncertain_clear(sid, value):
        original(sid, value)
        if not value:
            raise manager_module.SessionStateError("返信が失われた")

    monkeypatch.setattr(fake, "set_locked", uncertain_clear)
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": False})
    assert r.status_code == 503
    assert fake.is_locked("0123abcd") and "0123abcd" in _store()


def test_unlock_origin_resolves_current_and_pending_marks(admin_client, monkeypatch):
    """由来の古い ID で解除しても、今の ID と復旧先の印を両方外す。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    data = locks._read()
    data["locks"]["0123abcd"].update(session_id="aa000001", pending_sid="aa000002", state="restoring")
    locks._write(data)
    fake.rows.clear()
    for sid in ("aa000001", "aa000002"):
        fake.rows[sid] = _row(sid)
        fake.set_locked(sid, True)
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS,
                         json={"locked": False})
    assert r.status_code == 200
    assert ("aa000001", False) in fake.set_locked_calls
    assert ("aa000002", False) in fake.set_locked_calls
    assert _store() == {}


def test_lock_unlock_and_kill_share_one_guard(monkeypatch):
    """付ける・外す・閉じるが同じ排他を通り、保存と印の途中へ割り込まない（Event で順序を固定）。"""
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)
    marked = threading.Event()
    release = threading.Event()
    original = fake.set_locked

    def pause(sid, locked):
        if locked:
            marked.set()
            assert release.wait(3)
        original(sid, locked)

    monkeypatch.setattr(fake, "set_locked", pause)
    mgr = TerminalManager()
    monkeypatch.setattr(mgr, "is_locked", fake.is_locked)
    # kill は API と独立して呼ばれても同じ排他で印・保存を確認する。
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(locks.lock_console, _row(), user=SimpleNamespace(username="admin", id=1))
        assert marked.wait(3)
        second_started = threading.Event()

        def unlock():
            second_started.set()
            return locks.unlock_console("0123abcd")

        second = pool.submit(unlock)
        assert second_started.wait(3)
        assert not second.done(), "印・保存の途中へ解除が割り込んだ"
        # guard が解放されるまで durable guard の読取りにも入れない。
        killer = pool.submit(mgr.kill_session, "0123abcd")
        assert not killer.done()
        # kill 自体は fake に限定。lock/unlock のどちらの後でも整合した状態を見る。
        monkeypatch.setattr(manager_module, "tmux_available", lambda: False)
        release.set()
        first.result(3)
        assert second.result(3)
        try:
            killer.result(3)
        except SessionLockedError:
            pass
    assert not fake.is_locked("0123abcd") and _store() == {}


@pytest.mark.parametrize("path", ["/commander/consoles/0123abcd", "/commander/consoles", "/terminals/0123abcd"])
@pytest.mark.parametrize("error", [SessionLockedError(LOCK_DETAIL), manager_module.SessionStateError("確認不能")])
def test_late_delete_refusal_preserves_streams(admin_client, monkeypatch, path, error):
    """一覧を見た後に中央 guard が断った・確認できないときは、接続（stream）を閉じない。"""
    from app.commander import router as cr
    from app.terminals import router as tr

    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)
    monkeypatch.setattr(tr, "manager", fake)
    closed = []
    monkeypatch.setattr(cr.streams, "close_session", lambda sid: closed.append(sid))
    monkeypatch.setattr(tr.streams, "close_session", lambda sid: closed.append(sid))
    monkeypatch.setattr(fake, "kill_session", lambda sid: (_ for _ in ()).throw(error))
    r = admin_client.delete("/api/v1" + path, headers=CSRF_HEADERS)
    if path == "/commander/consoles" and isinstance(error, SessionLockedError):
        assert r.status_code == 200 and r.json() == {"closed": 0, "skipped_locked": 1}
    else:
        assert r.status_code == (409 if isinstance(error, SessionLockedError) else 503)
    assert closed == []


def test_stream_close_from_worker_runs_on_owner_loop():
    """threadpool の DELETE から閉じても、Task.cancel などは所有 loop の上で行う。"""
    from app.terminals.stream import TerminalClientStream, TerminalStreamRegistry

    async def exercise():
        loop = asyncio.get_running_loop()
        loop.set_debug(True)  # 誤った thread での Task.cancel を検出する
        owner_thread = threading.get_ident()
        closed = []

        class Connection:
            async def read_loop(self, callback):
                await asyncio.Event().wait()

            def close(self):
                closed.append(threading.get_ident())

        registry = TerminalStreamRegistry(TerminalManager())
        registry._loop = loop
        stream = TerminalClientStream("0123abcd", "browser", Connection())
        registry._streams[("0123abcd", "browser")] = stream
        stream.start(lambda: None)
        stream.cleanup_handle = loop.call_later(60, lambda: None)
        await asyncio.to_thread(registry.close_session, "0123abcd")
        await asyncio.sleep(0)
        assert closed == [owner_thread]
        assert stream.closed and registry.stream_count() == 0
        assert stream.reader_task.cancelled()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure_at", ["mark", "adopt", "create_reply"])
def test_restore_partial_failure_is_tracked_across_restart(admin_client, monkeypatch, failure_at):
    """復旧の途中（印・保存・作成の返信）で失敗しても、同じ ID を追跡して再起動後も複製を作らない。"""
    import importlib

    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    _spy_notify(monkeypatch)
    calls = _spy_create(monkeypatch)
    fake.rows.clear()
    original_write = locks._write
    original_mark = fake.set_locked
    original_create = consoles.create_consoles

    def write(data):
        if failure_at == "adopt" and data["locks"]["0123abcd"]["state"] == "locked":
            raise OSError("adopt disk full")
        original_write(data)

    def mark(sid, locked):
        if failure_at == "mark":
            raise RuntimeError("mark failed")
        original_mark(sid, locked)

    def create(**kw):
        result = original_create(**kw)
        if failure_at == "create_reply":
            raise subprocess.TimeoutExpired("new-session", 5)
        return result

    with monkeypatch.context() as fault:
        fault.setattr(locks, "_write", write)
        fault.setattr(fake, "set_locked", mark)
        fault.setattr(consoles, "create_consoles", create)
        assert locks.check(now=1000)[-1]["event"] == "failed"
        entry = _store()["0123abcd"]
        pending = entry["pending_sid"]
        assert pending in fake.rows and entry["attempts"] == [1000]
        assert locks.find_entry(pending) is not None
        mgr = TerminalManager()
        with pytest.raises(SessionLockedError):
            mgr.kill_session(pending)  # 未ロックの暫定 session も中央 guard で守る
        locks.check(now=1001)
        assert len(calls) == 1, "途中の失敗で複製を作ってしまった"
    importlib.reload(locks)  # メモリだけの追跡では再起動で失われる
    locks.check(now=1002)
    assert len(calls) == 1
    assert _store()["0123abcd"]["session_id"] == pending
    assert _store()["0123abcd"]["attempts"] == [1000]
    assert _store()["0123abcd"]["state"] == "locked"
    assert fake.is_locked(pending)
    assert not fake.killed
    locks.unlock_console("0123abcd")
    assert not fake.is_locked(pending) and _store() == {}


def test_restore_budget_survives_failures_id_changes_and_restart(admin_client, monkeypatch):
    """10 分 3 回の上限は、失敗・ID の変更・再起動をまたいで保たれる。"""
    import importlib

    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    calls = _spy_create(monkeypatch)
    for now in (1000, 1010, 1020):
        fake.rows.clear()
        if now == 1010:
            fake.fail_set_locked = True
        locks.check(now=now)
        fake.fail_set_locked = False
        importlib.reload(locks)
        locks.check(now=now + 1)
    fake.rows.clear()
    assert locks.check(now=1030)[-1]["event"] == "gave_up"
    assert len(calls) == 3
    assert _store()["0123abcd"]["attempts"] == [1000, 1010, 1020]


def test_two_watchers_and_unlock_do_not_duplicate(admin_client, monkeypatch):
    """見回りが 2 つ重なり、解除も重なっても、作るのは 1 つだけで解除は確実に効く。"""
    from concurrent.futures import ThreadPoolExecutor

    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    fake.rows.clear()
    calls = _spy_create(monkeypatch)
    create = consoles.create_consoles
    entered, release = threading.Event(), threading.Event()

    def pause(**kw):
        entered.set()
        assert release.wait(3)
        return create(**kw)

    monkeypatch.setattr(consoles, "create_consoles", pause)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(locks.check, 1000)
        assert entered.wait(3)
        second = pool.submit(locks.check, 1001)
        unlock = pool.submit(locks.unlock_console, "0123abcd")
        assert not second.done() and not unlock.done()
        release.set()
        first.result(3)
        second.result(3)
        assert unlock.result(3)
    assert len(calls) == 1 and not fake.killed
    assert _store() == {} and not any(sid in fake.locked for sid in fake.rows)


@pytest.mark.parametrize("raw", ["{", "[]", '{"locks": []}', '{"locks": {"0123abcd": null}}',
                                     '{"locks": {"0123abcd": {"session_id": "invalid"}}}'])
def test_corrupt_store_stops_watch_and_kill_and_is_diagnostic(admin_client, monkeypatch, raw):
    """保存が壊れたら空として扱わず、見回りと閉じるを止め、診断（JSON・md）に異常を出す。"""
    from app.diagnostics import report

    _store_path().parent.mkdir(parents=True, exist_ok=True)
    _store_path().write_text(raw)
    with pytest.raises(locks.LockStoreError):
        locks.check(1000)
    monkeypatch.setattr(manager_module, "tmux_available", lambda: False)
    with pytest.raises(locks.LockStoreError):
        TerminalManager().kill_session("0123abcd")
    summary = report._console_locks()["summary"]
    assert summary["stopped"] and summary["error"]
    data = {"console_locks": report._console_locks()}
    # 実 API の JSON レポートからも同じ異常が読める。
    r = admin_client.get("/api/v1/diagnostics/report?format=json")
    assert r.status_code == 200
    assert r.json()["console_locks"]["summary"]["error"]
    md = admin_client.get("/api/v1/diagnostics/report?format=md").text
    assert "異常:" in md and "保存" in md
    assert _store_path().read_text() == raw


def test_unreadable_store_is_not_empty(monkeypatch):
    """保存を読めない（権限）ときも空として扱わない。"""
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == _store_path():
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(locks.LockStoreError):
        locks.check(1000)
    assert locks.summary()["stopped"]


def test_all_raw_tmux_calls_use_explicit_private_socket():
    """全 backend の試験の tmux は PATH の wrapper で専用 -L へ通り、既定ソケット・kill-server へ抜けない。"""
    import shutil

    wrapper = Path(shutil.which("tmux"))
    source = wrapper.read_text()
    assert " -L nexarch-test-" in source
    assert "unset TMUX TMUX_PANE" in source
    assert not os.environ.get("TMUX") and not os.environ.get("TMUX_PANE")
    # 迂回は実 tmux に到達する前に wrapper が拒否する（大域の -L/-S・kill-server・; で続けた kill-server）。
    for argv in (["-L", "default", "list-sessions"], ["-Ldefault", "ls"], ["-S", "/tmp/x", "ls"],
                 ["-f", "/dev/null", "kill-server"], ["kill-ser"],
                 ["-uL", "default", "ls"], ["-2S", "/tmp/x", "ls"], ["-uf", "/dev/null", "-L", "x", "ls"],
                 ["has-session", "-t", "cdterm-zz", ";", "kill-server"]):
        assert subprocess.run(["tmux", *argv], capture_output=True).returncode == 97, argv
    # 命令の後の -S（capture-pane の開始行）は別の意味なので通し、専用ソケットへ届く。
    probe = subprocess.run(["tmux", "capture-pane", "-p", "-S", "-1", "-t", "cdterm-zzzzzzzz"],
                           capture_output=True, text=True, env={**os.environ, "LC_ALL": "C"})
    assert probe.returncode not in (0, 97)
    socket = os.environ["CONTROL_DECK_TEST_TMUX_SOCKET"]
    assert socket in probe.stderr or "can't find" in probe.stderr or "no such session" in probe.stderr
    assert "/default" not in probe.stderr, "本番の既定ソケットへ届いてはいけない"


# ---------------------------------------------------------------- #5 追加: 失敗時の保護・整合性
# 根拠: Nexarch #5（2026-11-28）。先行の試験は正常系が中心で、次の穴を守れていなかった。
# - 既に無いセッションの終了は「閉じた」と同じ（Terminal の DELETE の従来の振る舞い）。
#   権限・通信の失敗は成功にしない（実終了の失敗の偽装をしない）。
# - ロックの付与が途中で失敗したとき: 印が無いと確かめられたら記録を消して失敗、
#   確かめられなければ記録を残して保護を続ける（弱める方向へ倒さない）。
# - 見回りは 1 件の失敗で他のロックを見落とさない。復旧先へは一覧用の印も付け直す。
# - 一括で閉じる途中の失敗: 閉じた分は監査へ残し、閉じていない接続は保つ。


def test_kill_missing_session_is_idempotent_but_permission_error_is_not(monkeypatch):
    """既に無いセッションの終了は成功（従来どおり）。権限エラーは成功にしない。"""
    target_errors = {"kill": "can't find session: cdterm-0123abcd"}
    calls: list[list[str]] = []

    def run(argv, **kwargs):  # noqa: ANN001, ANN003
        calls.append(argv)
        if "show-options" in argv:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no such session: cdterm-0123abcd")
        if "has-session" in argv:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="can't find session: cdterm-0123abcd")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=target_errors["kill"])

    monkeypatch.setattr(manager_module, "tmux_available", lambda: True)
    monkeypatch.setattr(manager_module.subprocess, "run", run)
    TerminalManager().kill_session("0123abcd")  # 既に無い → 閉じたのと同じ
    assert any("kill-session" in a for a in calls)
    target_errors["kill"] = "error connecting to /tmp/x/sock (Permission denied)"
    with pytest.raises(manager_module.SessionStateError):
        TerminalManager().kill_session("0123abcd")


def test_lock_mark_failure_rolls_back_only_when_mark_is_known_absent(admin_client, monkeypatch):
    """印の付与の失敗: 無いと確かめられたら記録を消して 503、不明なら記録を残して保護を続ける。"""
    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)
    fake.fail_set_locked = True
    before = len(_audit_rows("commander.console.lock", "0123abcd"))
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS, json={"locked": True})
    assert r.status_code == 503 and _store() == {}, "印が無いのに記録だけ残すと、画面と保護が食い違う"
    assert len(_audit_rows("commander.console.lock", "0123abcd")) == before, "失敗を lock として残さない"

    # 確かめられない（tmux 不調）: 記録は locking のまま残り、中央 guard が閉じさせない
    monkeypatch.setattr(fake, "is_locked", lambda sid: (_ for _ in ()).throw(
        manager_module.SessionStateError("確認不能")))
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS, json={"locked": True})
    assert r.status_code == 503
    assert _store()["0123abcd"]["state"] == "locking"
    with pytest.raises(SessionLockedError):
        TerminalManager().kill_session("0123abcd")
    # tmux が戻れば見回りが同じセッションへ印を付けて確定する（新しいコンソールは作らない）
    fake.fail_set_locked = False
    calls = _spy_create(monkeypatch)
    assert locks.check(now=1000) == []
    assert _store()["0123abcd"]["state"] == "locked" and fake.locked == {"0123abcd"} and calls == []


def test_lock_mark_reply_lost_but_applied_is_success(admin_client, monkeypatch):
    """印は付いたが返信が失われたときは、確かめて成功として確定する。"""
    fake = FakeManager([_row()])
    _fake(monkeypatch, fake)
    original = fake.set_locked

    def applied_then_timeout(sid, locked):
        original(sid, locked)
        raise manager_module.SessionStateError("返信が失われた")

    monkeypatch.setattr(fake, "set_locked", applied_then_timeout)
    r = admin_client.put("/api/v1/commander/consoles/0123abcd/lock", headers=CSRF_HEADERS, json={"locked": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "locked": True}
    assert _store()["0123abcd"]["state"] == "locked"


def test_lock_on_pending_restore_session_confirms_it(admin_client, monkeypatch):
    """復旧の暫定セッションへ利用者が鍵をかけたら、それを今の ID として確定する。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    data = locks._read()
    data["locks"]["0123abcd"].update(pending_sid="aa000002", state="restoring")
    locks._write(data)
    fake.rows["aa000002"] = _row("aa000002")
    locks.lock_console(_row("aa000002"), user=None)
    entry = _store()["0123abcd"]
    assert entry["session_id"] == "aa000002" and "pending_sid" not in entry and entry["state"] == "locked"


def test_watch_isolates_one_failing_entry_from_the_others(admin_client, monkeypatch):
    """1 件の tmux・ID の失敗で、ほかのロックしたコンソールの検知を止めない。"""
    fake = FakeManager([_row(), _row("0123abce")])
    _lock_now(admin_client, fake, monkeypatch)
    _lock_now(admin_client, fake, monkeypatch, sid="0123abce")
    _spy_notify(monkeypatch)
    calls = _spy_create(monkeypatch)
    fake.rows.clear()
    original = fake.session_exists

    def exists(sid):
        if sid == "0123abcd":
            raise manager_module.SessionStateError("permission denied")
        if sid not in ("0123abce",):
            raise RuntimeError("復旧先 ID の確認に失敗")  # 新しい ID の確認も失敗させる
        return original(sid)

    monkeypatch.setattr(fake, "session_exists", exists)
    events = locks.check(now=1000)
    assert [e["event"] for e in events] == ["vanished"], "2 件目の消失は検知する"
    assert _store()["0123abcd"]["state"] == "locked", "確認できない 1 件は消失にしない"
    assert _store()["0123abce"]["state"] == "vanished" and calls == []
    assert _store()["0123abce"]["attempts"] == [], "作る前に止まった試みは数えない"


def test_restore_adopt_retags_console_marks(admin_client, monkeypatch):
    """作成直後の印の付与で止まった復旧先も、見回りで一覧用の印と鍵を付けて確定する。"""
    fake = FakeManager([_row()])
    _lock_now(admin_client, fake, monkeypatch)
    _spy_notify(monkeypatch)
    calls: list[dict] = []

    def create_without_marks(**kw):
        calls.append(kw)
        fake.rows[kw["session_id"]] = {"id": kw["session_id"]}
        raise RuntimeError("セッション属性を保存できません")

    monkeypatch.setattr(consoles, "create_consoles", create_without_marks)
    fake.rows.clear()
    assert locks.check(now=1000)[-1]["event"] == "failed"
    pending = _store()["0123abcd"]["pending_sid"]
    assert [e["event"] for e in locks.check(now=1001)] == []
    assert fake.tags[pending]["cli"] == "claude" and fake.tags[pending]["label"] == "検証ラベル"
    assert fake.is_locked(pending) and len(calls) == 1
    assert _store()["0123abcd"]["session_id"] == pending


def test_bulk_close_failure_keeps_unclosed_streams_and_audits_closed(admin_client, monkeypatch):
    """一括で閉じる途中の失敗: 閉じた分は監査へ残して 503、閉じていない接続は保つ。"""
    from app.commander import router as cr

    fake = FakeManager([_row("0123abc1"), _row("0123abc2"), _row("0123abc3")])
    _fake(monkeypatch, fake)
    closed_streams: list[str] = []
    monkeypatch.setattr(cr.streams, "close_session", closed_streams.append)
    original = fake.kill_session

    def kill(sid):
        if sid == "0123abc2":
            raise manager_module.SessionStateError("permission denied")
        original(sid)

    monkeypatch.setattr(fake, "kill_session", kill)
    r = admin_client.delete("/api/v1/commander/consoles", headers=CSRF_HEADERS)
    assert r.status_code == 503
    assert closed_streams == ["0123abc1"] and fake.killed == ["0123abc1"], "失敗後は閉じない"
    meta = json.loads(_audit_rows("commander.console.close_all")[-1].metadata_json)
    assert meta == {"closed": 1, "skipped_locked": 0, "failed": True}


def test_stream_close_after_owner_loop_closed_does_not_fail():
    """所有 loop が閉じた後の DELETE（再起動・試験の TestClient の入れ替え）でも 500 にしない。"""
    from app.terminals.stream import TerminalClientStream, TerminalStreamRegistry

    closed: list[str] = []

    class Connection:
        async def read_loop(self, callback):
            await asyncio.Event().wait()

        def close(self):
            closed.append("x")

    registry = TerminalStreamRegistry(TerminalManager())
    loop = asyncio.new_event_loop()
    stream = TerminalClientStream("0123abcd", "browser", Connection())

    async def start():
        registry._loop = asyncio.get_running_loop()
        registry._streams[("0123abcd", "browser")] = stream
        stream.start(lambda: None)

    loop.run_until_complete(start())
    # 閉じた loop に task が残るのはこの試験の想定（再起動の模擬）。破棄時の警告だけ黙らせる
    stream.reader_task._log_destroy_pending = False
    loop.close()
    registry.close_session("0123abcd")
    assert registry.stream_count() == 0 and closed == ["x"]


def test_real_tmux_permission_denied_is_unknown_not_missing():
    """実 tmux（試験専用の -L）で、ソケットへ入れないときは消失でも終了成功でもない。

    根拠: has-session の非 0 を全部「無い」にすると、権限エラーで復旧が二重に作られ、
    kill の失敗を成功と装う。ローカル tmux 3.4 の文言で分類する（_tmux_missing）。
    """
    socket_dir = Path(os.environ["TMUX_TMPDIR"]) / f"tmux-{os.getuid()}"
    mgr = TerminalManager()
    sid = mgr.create_session(cwd=_root(), command="bash")["id"]
    sock = socket_dir / os.environ["CONTROL_DECK_TEST_TMUX_SOCKET"]
    try:
        assert mgr.session_exists("ffffffff") is False
        assert mgr.is_locked(sid) is False
        sock.chmod(0)
        try:
            with pytest.raises(manager_module.SessionStateError):
                mgr.session_exists(sid)
            with pytest.raises(manager_module.SessionStateError):
                mgr.kill_session(sid)
        finally:
            sock.chmod(0o700)
        assert mgr.session_exists(sid) is True, "確認できない間に閉じてはいけない"
        mgr.kill_session(sid)
        mgr.kill_session(sid)  # 2 度目は既に無い → 閉じたのと同じ
        assert mgr.session_exists(sid) is False
    finally:
        sock.chmod(0o700)
        if mgr.session_exists(sid):
            mgr.kill_session(sid)
