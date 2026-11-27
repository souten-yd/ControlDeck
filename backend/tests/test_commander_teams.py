"""Nexarch の AI チーム（app/commander/teams.py・/commander/teams API）の試験。

目的: 「タスク板（ControlDeck が正本）＋担当のコンソールへの構造化した送り込み＋画面の
「完了報告:」の取り込み」を守る。保存（0600・壊れた JSON）・検証・メンバーは Nexarch の
コンソールだけ・リーダー 1 人・担当ファイルの重なりの警告・dispatch の文面と断り・collect の正規表現・
手入力の報告・監査ログ・権限。

根拠: 利用者の依頼（2026-11-27）「AI チームを作りたい。情報の橋渡し、リーダーはローカル LLM も
フロンティアも自由に。使いやすい形で」と docs/PLAN-nexarch-team.md の T1。弱いモデルでも動くよう
状態の判断と取り込みは LLM でなくコードが持つので、文面の固定と正規表現を機械的に確かめる。
Terminal は FakeManager で置き換える（tmux は使わない）。
"""
from __future__ import annotations

import json
import os
import stat

import pytest
from sqlalchemy import select

from app.commander import consoles, teams
from app.database import SessionLocal
from app.models import AuditLog
from tests.conftest import CSRF_HEADERS

API = "/api/v1/commander/teams"


class FakeManager:
    def __init__(self) -> None:
        self.sessions = {
            "aa000001": {"id": "aa000001", "alive": True, "commander_cli": "claude", "program": "claude"},
            "aa000002": {"id": "aa000002", "alive": True, "commander_cli": "codex", "program": "codex"},
            "aa000003": {"id": "aa000003", "alive": True, "commander_cli": "opencode", "program": "opencode"},
        }
        self.screens: dict[str, str] = {}
        self.injected: list[tuple[str, str, bool]] = []
        self.marks: dict[str, dict] = {}
        self.cleared: list[str] = []
        self.captures = 0

    def list_sessions(self):
        return [dict(s) for s in self.sessions.values()]

    def capture_text(self, sid, lines=60):  # noqa: ARG002
        self.captures += 1
        return self.screens.get(sid, "")

    def inject_input(self, sid, text, *, submit=True):
        self.injected.append((sid, text, submit))
        return len(text)

    def tag_session(self, sid, marks):
        self.marks.setdefault(sid, {}).update(marks)

    def clear_marks(self, sid, keys):  # noqa: ARG002
        self.cleared.append(sid)


@pytest.fixture()
def fake(monkeypatch):
    fm = FakeManager()
    monkeypatch.setattr(consoles, "manager", fm)
    monkeypatch.setattr(teams, "manager", fm)
    return fm


@pytest.fixture(autouse=True)
def _clean_store():
    path = teams._path()
    if path.exists():
        path.unlink()
    yield
    if path.exists():
        path.unlink()


def _post(client, url, body=None):
    return client.post(f"{API}{url}", headers=CSRF_HEADERS, json=body or {})


def _put(client, url, body):
    return client.put(f"{API}{url}", headers=CSRF_HEADERS, json=body)


def _team(client, with_members=True):
    team = _post(client, "", {"name": "開発班"}).json()
    if with_members:
        r = _put(client, f"/{team['id']}/members", {"members": [
            {"session_id": "aa000001", "role": "developer"},
            {"session_id": "aa000002", "role": "reviewer"},
        ]})
        assert r.status_code == 200, r.text
    return team["id"]


def _task(client, tid, **kw):
    body = {"title": "ログイン画面", "goal": "作る", "acceptance": "表示される", "files": ["src/a.py"],
            "assignee": "aa000001", **kw}
    r = _post(client, f"/{tid}/tasks", body)
    assert r.status_code == 201, r.text
    return r.json()


REPORT = ("完了報告: 画面を作った\n変更ファイル: src/a.py, src/b.py\n試験: pytest 12 件合格\n"
          "確認: pytest -q → 12 passed\n残り: なし")


def test_requires_login(client):
    client.cookies.clear()  # 他の試験のログインが残っていても未ログインで確かめる
    assert client.get(API).status_code == 401
    assert client.post(API, headers=CSRF_HEADERS, json={"name": "x"}).status_code == 401


def test_storage_mode_and_broken_json(admin_client, fake):
    _team(admin_client)
    path = teams._path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    path.write_text("{broken", encoding="utf-8")
    r = admin_client.get(API)
    assert r.status_code == 200 and r.json()["teams"] == []
    assert _post(admin_client, "", {"name": "新"}).status_code == 201
    assert json.loads(path.read_text())["teams"][0]["name"] == "新"


def test_name_and_text_validation(admin_client, fake):
    for bad in ("", "  ", "x" * 41, "a\nb", "a\x00b"):
        assert _post(admin_client, "", {"name": bad}).status_code in (400, 422), bad
    tid = _team(admin_client)
    assert _put(admin_client, f"/{tid}", {"name": "改名"}).json()["name"] == "改名"
    for body in ({"title": ""}, {"title": "x" * 121}, {"title": "a\tb"},
                 {"title": "ok", "goal": "a\x01"}, {"title": "ok", "goal": "x" * 4001},
                 {"title": "ok", "files": ["a"] * 51}, {"title": "ok", "files": ["a\nb"]}):
        assert _post(admin_client, f"/{tid}/tasks", body).status_code in (400, 422), body
    ok = _post(admin_client, f"/{tid}/tasks", {"title": "ok", "goal": "1行目\n\t2行目"})
    assert ok.status_code == 201
    assert ok.json()["id"] == 1
    assert _post(admin_client, f"/{tid}/tasks", {"title": "二つ目"}).json()["id"] == 2
    assert _post(admin_client, f"/{tid}/tasks", {"title": "x", "assignee": "zzzz"}).status_code == 400
    assert _put(admin_client, "/tnone/members", {"members": []}).status_code == 404


def test_members_only_consoles_one_leader_and_role_marks(admin_client, fake):
    tid = _team(admin_client, with_members=False)
    url = f"/{tid}/members"
    assert _put(admin_client, url, {"members": [{"session_id": "ffffffff", "role": "developer"}]}).status_code == 400
    assert _put(admin_client, url, {"members": [{"session_id": "aa000001", "role": "boss"}]}).status_code == 400
    two = {"members": [{"session_id": "aa000001", "role": "leader"}, {"session_id": "aa000002", "role": "leader"}]}
    assert _put(admin_client, url, two).status_code == 400
    dup = {"members": [{"session_id": "aa000001", "role": "leader"}, {"session_id": "aa000001", "role": "member"}]}
    assert _put(admin_client, url, dup).status_code == 400
    ok = {"members": [{"session_id": "aa000001", "role": "leader"}, {"session_id": "aa000002", "role": "researcher"}]}
    assert _put(admin_client, url, ok).status_code == 200
    assert fake.marks["aa000001"]["role"] == "leader"
    assert fake.marks["aa000002"]["role"] == "researcher"
    assert _put(admin_client, url, {"members": [{"session_id": "aa000001", "role": "leader"}]}).status_code == 200
    assert "aa000002" in fake.cleared


def test_overlap_warnings(admin_client, fake):
    tid = _team(admin_client)
    _task(admin_client, tid, files=["src/x.py", "docs/"])
    t2 = _task(admin_client, tid, title="二つ目", files=["src/x.py", "docs/a.md", "other.py"])
    assert "#1 と src/x.py が重なっています" in t2["warnings"]
    assert "#1 と docs/a.md が重なっています" in t2["warnings"]
    assert len(t2["warnings"]) == 2
    _put(admin_client, f"/{tid}/tasks/1", {"status": "done"})
    r = _put(admin_client, f"/{tid}/tasks/2", {"title": "二つ目"})
    assert r.json()["warnings"] == []
    _put(admin_client, f"/{tid}/tasks/1", {"status": "todo"})
    assert _put(admin_client, f"/{tid}/tasks/2", {"files": ["src/x.py"]}).json()["warnings"]


def test_dispatch_text_transitions_and_refusals(admin_client, fake):
    tid = _team(admin_client)
    task = _task(admin_client, tid, files=["src/a.py", "src/b.py"])
    r = _post(admin_client, f"/{tid}/tasks/{task['id']}/dispatch")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "doing"
    sid, text, submit = fake.injected[0]
    assert sid == "aa000001" and submit is True
    assert text.splitlines()[0] == "【Nexarch タスク #1】ログイン画面"
    assert "目的: 作る" in text
    assert "受け入れ条件: 表示される" in text
    assert "担当ファイル: src/a.py, src/b.py" in text
    assert text.splitlines()[-5:] == [
        "完了報告: <何をしたか 1 行>", "変更ファイル: <パスを , で>",
        "試験: <実行した試験と結果>", "確認: <実行したコマンドと、その結果の要点>",
        "残り: <残っていること。無ければ なし>"]
    assert r.json()["history"][-1]["event"] == "担当へ送信"
    t2 = _task(admin_client, tid, title="二", files=[])
    _post(admin_client, f"/{tid}/tasks/{t2['id']}/dispatch")
    assert "担当ファイル: 指定なし" in fake.injected[-1][1]
    # 作業中は 409（送らない）
    fake.injected.clear()
    fake.screens["aa000001"] = "· Drizzling… (2m 39s · ↓ 16k tokens)\nesc to interrupt\n"
    r = _post(admin_client, f"/{tid}/tasks/{t2['id']}/dispatch")
    assert r.status_code == 409 and "作業中です" in r.json()["detail"]
    assert not fake.injected
    # 終了は 409
    fake.screens["aa000001"] = ""
    fake.sessions["aa000001"]["alive"] = False
    assert _post(admin_client, f"/{tid}/tasks/{t2['id']}/dispatch").status_code == 409
    # コンソールを閉じても担当は残り、送れない
    del fake.sessions["aa000001"]
    assert _post(admin_client, f"/{tid}/tasks/{t2['id']}/dispatch").status_code == 409
    tasks = admin_client.get(API).json()["teams"][0]["tasks"]
    assert tasks[0]["assignee"] == "aa000001" and tasks[0]["assignee_state"] == "closed"
    t3 = _task(admin_client, tid, title="三", assignee="")
    assert _post(admin_client, f"/{tid}/tasks/{t3['id']}/dispatch").status_code == 400


def test_collect_parses_last_block_and_ignores_template(admin_client, fake):
    tid = _team(admin_client)
    task = _task(admin_client, tid)
    url = f"/{tid}/tasks/{task['id']}/collect"
    assert _post(admin_client, url).status_code == 404
    template = ("終わったら最後に次の 5 行で報告してください:\n完了報告: <何をしたか 1 行>\n"
                "変更ファイル: <パスを , で>\n試験: <実行した試験と結果>\n確認: <実行したコマンドと、その結果の要点>\n残り: <残っていること。無ければ なし>\n")
    fake.screens["aa000001"] = template
    r = _post(admin_client, url)
    assert r.status_code == 404 and "まだ報告がありません" in r.json()["detail"]
    fake.screens["aa000001"] = (
        template + "完了報告: 古い\n変更ファイル: old.py\n試験: なし\n残り: 多い\n\n"
        "● 作業しました\n│ 完了報告: 画面を作った\n│ 変更ファイル: src/a.py, src/b.py\n"
        "│ 試験: pytest 12 件合格\n│ 確認: pytest -q → 12 passed\n│ 残り: なし\n\n> \n? for shortcuts\n")
    r = _post(admin_client, url)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "review"
    assert body["report"] == {"summary": "画面を作った", "changed_files": ["src/a.py", "src/b.py"],
                              "tests": "pytest 12 件合格", "verify": "pytest -q → 12 passed", "remaining": "なし"}
    fake.screens["aa000001"] = "完了報告: 途中\n変更ファイル: a.py\n"
    assert _post(admin_client, url).status_code == 404
    assert teams.parse_report("完了報告：調べた\n変更ファイル：なし\n試験：なし\n残り：なし")["changed_files"] == []


def test_manual_report_review_and_report_ready(admin_client, fake):
    tid = _team(admin_client)
    task = _task(admin_client, tid)
    url = f"/{tid}/tasks/{task['id']}"
    r = _put(admin_client, f"{url}/report", {"summary": "手で入れた", "changed_files": ["a.py"], "tests": "ok", "remaining": "なし"})
    assert r.status_code == 200 and r.json()["status"] == "review"
    assert r.json()["report"]["summary"] == "手で入れた"
    assert _put(admin_client, f"{url}/report", {"summary": "a\nb"}).status_code == 400
    # doing の担当の画面に報告があれば report_ready（capture は担当ごとに 1 回）
    _put(admin_client, url, {"status": "doing"})
    t2 = _task(admin_client, tid, title="二")
    _put(admin_client, f"/{tid}/tasks/{t2['id']}", {"status": "doing"})
    fake.screens["aa000001"] = REPORT
    fake.captures = 0
    tasks = admin_client.get(API).json()["teams"][0]["tasks"]
    assert [t["report_ready"] for t in tasks] == [True, True]
    assert fake.captures == 3 + 1  # 一覧のための 3 コンソール分 + 担当 1 回
    assert _put(admin_client, url, {"status": "done"}).json()["status"] == "done"
    assert _put(admin_client, url, {"status": "rejected"}).json()["status"] == "rejected"
    assert _put(admin_client, url, {"status": "bogus"}).status_code == 400
    assert admin_client.delete(f"{API}/{tid}/tasks/{task['id']}", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.delete(f"{API}/{tid}", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.get(API).json()["teams"] == []


def test_history_is_capped(admin_client, fake):
    tid = _team(admin_client)
    task = _task(admin_client, tid)
    for i in range(60):
        _put(admin_client, f"/{tid}/tasks/{task['id']}", {"title": f"t{i}"})
    t = admin_client.get(API).json()["teams"][0]["tasks"][0]
    assert len(t["history"]) == 50


def test_audit_log(admin_client, fake):
    tid = _team(admin_client)
    task = _task(admin_client, tid)
    _post(admin_client, f"/{tid}/tasks/{task['id']}/dispatch")
    with SessionLocal() as db:
        actions = {a for (a,) in db.execute(select(AuditLog.action).where(AuditLog.action.like("commander.team.%")))}
    assert {"commander.team.create", "commander.team.members", "commander.team.task.create",
            "commander.team.task.dispatch"} <= actions
