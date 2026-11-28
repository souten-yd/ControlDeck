"""Nexarch の AI チームの見張り・通知・検証できる報告（T2a。commander/team_watch.py・teams.py）の試験。

目的: 「作業中のタスクの担当が入力待ちに戻ったら報告を自動で取り込む／報告なしで止まったら印を付ける／
上限で止まったら印を付ける／閉じたら記録する」を、コード（LLM なし）が 1 回だけ・正しい順に行うこと、
通知が同じ出来事で重ならずチームごとに切れること、5 行（確認つき）・古い 4 行の報告を読めて、確認の無い
報告に「未検証」が付くことを守る。
根拠: 利用者「ローカル LLM・Claude・Codex・OpenRouter・OpenCode Go を横断で協調して開発を進めたい」と
docs/PLAN-nexarch-team.md の「設計へ取り込むこと」1（検証できる完了報告）・6（通知）・段 T2。
実際の通知先へは送らず、`team_watch.send` を差し替える。Terminal は FakeManager（tmux は使わない）。
"""
from __future__ import annotations

import asyncio

import pytest

from app.commander import consoles, team_watch, teams
from tests.conftest import CSRF_HEADERS

API = "/api/v1/commander/teams"
REPORT5 = ("完了報告: 画面を作った\n変更ファイル: src/a.py\n試験: pytest 12 件合格\n"
           "確認: pytest -q → 12 passed\n残り: なし")
REPORT4 = "完了報告: 画面を作った\n変更ファイル: src/a.py\n試験: pytest 12 件合格\n残り: なし"
WORKING = "· Drizzling… (2m 39s · ↓ 16k tokens)\nesc to interrupt\n"
LIMITED = "You've hit your 5-hour limit · resets 10:20pm (Asia/Tokyo)\n"


class FakeManager:
    def __init__(self) -> None:
        self.sessions = {"aa000001": {"id": "aa000001", "alive": True, "commander_cli": "claude", "program": "claude"}}
        self.screens: dict[str, str] = {}
        self.captures = 0

    def list_sessions(self):
        return [dict(s) for s in self.sessions.values()]

    def capture_text(self, sid, lines=60):  # noqa: ARG002
        self.captures += 1
        return self.screens.get(sid, "")

    def inject_input(self, sid, text, *, submit=True):  # noqa: ARG002
        return len(text)

    def tag_session(self, sid, marks):  # noqa: ARG002
        pass

    def clear_marks(self, sid, keys):  # noqa: ARG002
        pass


@pytest.fixture()
def fake(monkeypatch):
    fm = FakeManager()
    monkeypatch.setattr(teams, "RESET_WAIT", 0)
    for module in (consoles, teams, team_watch):
        monkeypatch.setattr(module, "manager", fm)
    return fm


@pytest.fixture()
def sent(monkeypatch):
    out: list[tuple[str, str]] = []

    async def fake_send(title, message, kind="team_blocked"):
        out.append((title, message))

    monkeypatch.setattr(team_watch, "send", fake_send)
    return out


@pytest.fixture(autouse=True)
def _clean_store():
    path = teams._path()
    for target in (path, path.with_name("teams.json.bak")):
        if target.exists():
            target.unlink()
    yield
    for target in (path, path.with_name("teams.json.bak")):
        if target.exists():
            target.unlink()


def _setup(client) -> tuple[str, int]:
    team = client.post(API, headers=CSRF_HEADERS, json={"name": "開発班"}).json()
    client.put(f"{API}/{team['id']}/members", headers=CSRF_HEADERS,
               json={"members": [{"session_id": "aa000001", "role": "developer"}]})
    task = client.post(f"{API}/{team['id']}/tasks", headers=CSRF_HEADERS,
                       json={"title": "ログイン画面", "goal": "秘密の目的文", "assignee": "aa000001"}).json()
    client.post(f"{API}/{team['id']}/tasks/{task['id']}/dispatch", headers=CSRF_HEADERS)
    return team["id"], task["id"]


def _task(client, tid, num=1) -> dict:
    return next(t for t in client.get(API).json()["teams"] if t["id"] == tid)["tasks"][num - 1]


def _tick(offset: float = 1000.0) -> list[dict]:
    """送った直後の猶予を過ぎた時刻で見回る。"""
    import time

    return team_watch.tick(time.time() + offset)


# ---------------------------------------------------------------- 報告の形

def test_parse_five_line_and_legacy_four_line_report():
    five = teams.parse_report(REPORT5)
    assert five and five["verify"] == "pytest -q → 12 passed" and five["remaining"] == "なし"
    four = teams.parse_report(REPORT4)
    assert four and four["verify"] == "" and four["tests"] == "pytest 12 件合格"
    # 見本のまま（<…>）は報告でない
    assert teams.parse_report("完了報告: <何をしたか 1 行>\n変更ファイル: <x>\n試験: <y>\n確認: <z>\n残り: <w>") is None
    assert teams.parse_report("完了報告: 途中\n変更ファイル: a.py\n試験: ok\n確認: ok\n") is None


def test_unverified_judgement():
    base = {"summary": "s", "changed_files": [], "tests": "pytest 12 件合格", "verify": "pytest -q → ok", "remaining": ""}
    assert teams.is_unverified({"report": base}) is False
    assert teams.is_unverified({"report": {**base, "verify": ""}}) is True
    for none in ("", "なし", "未実施", "-"):
        assert teams.is_unverified({"report": {**base, "tests": none}}) is True, none
    assert teams.is_unverified({"report": None}) is False


def test_unverified_in_list_and_manual_report_verify(admin_client, fake):
    tid, num = _setup(admin_client)
    url = f"{API}/{tid}/tasks/{num}"
    r = admin_client.put(f"{url}/report", headers=CSRF_HEADERS, json={"summary": "手で", "tests": "ok"})
    assert r.status_code == 200 and r.json()["unverified"] is True
    r = admin_client.put(f"{url}/report", headers=CSRF_HEADERS, json={"summary": "手で", "tests": "ok", "verify": "pytest → ok"})
    assert r.json()["unverified"] is False
    assert _task(admin_client, tid)["unverified"] is False


# ---------------------------------------------------------------- 見張り

def test_waiting_with_report_is_collected_and_notified_once(admin_client, fake, sent):
    tid, num = _setup(admin_client)
    fake.screens["aa000001"] = "【Nexarch タスク #1】ログイン画面\n" + REPORT5
    events = _tick()
    assert [e["kind"] for e in events] == ["report"]
    task = _task(admin_client, tid, num)
    assert task["status"] == "review" and task["report"]["verify"] == "pytest -q → 12 passed"
    assert task["history"][-1]["event"].endswith("自動で取り込み）")
    assert _tick() == []  # review になったので二度と見ない
    # 通知: 日本語・タイトルだけ（目的文を含まない）
    asyncio.run(_send_all(events))
    assert len(sent) == 1 and "ログイン画面" in sent[0][1] and "#1" in sent[0][1] and "秘密の目的文" not in sent[0][1]


async def _send_all(events):
    for event in events:
        await team_watch.send(event["title"], event["message"])


def test_old_report_before_dispatch_marker_is_not_picked(admin_client, fake, sent):
    tid, num = _setup(admin_client)
    fake.screens["aa000001"] = REPORT5 + "\n【Nexarch タスク #1】ログイン画面\n目的: 秘密\n> \n"
    assert [e["kind"] for e in _tick()] == ["stalled"]
    assert _task(admin_client, tid, num)["status"] == "doing"


def test_stalled_once_then_cleared_when_working_returns(admin_client, fake, sent):
    tid, num = _setup(admin_client)
    fake.screens["aa000001"] = "何もしていない\n> \n"
    first = _tick()
    assert [e["kind"] for e in first] == ["stalled"]
    task = _task(admin_client, tid, num)
    assert task["stalled_at"] and task["history"][-1]["event"] == "報告なしで止まった"
    n = len(task["history"])
    assert _tick() == []  # 入力待ちが続く間は 1 回だけ
    assert len(_task(admin_client, tid, num)["history"]) == n
    fake.screens["aa000001"] = WORKING
    assert _tick() == []
    assert "stalled_at" not in _task(admin_client, tid, num)
    fake.screens["aa000001"] = "また止まった\n> \n"  # 次の止まりは新しい出来事
    assert [e["kind"] for e in _tick()] == ["stalled"]


def test_grace_after_dispatch(admin_client, fake, sent):
    tid, num = _setup(admin_client)
    fake.screens["aa000001"] = "> \n"
    assert team_watch.tick() == []  # 送った直後は見ない
    assert "stalled_at" not in _task(admin_client, tid, num)


def test_limited_marks_and_notifies_once(admin_client, fake, sent):
    tid, num = _setup(admin_client)
    fake.screens["aa000001"] = LIMITED
    assert [e["kind"] for e in _tick()] == ["limited"]
    task = _task(admin_client, tid, num)
    assert task["limited_at"] and task["status"] == "doing"
    assert _tick() == []
    fake.screens["aa000001"] = WORKING  # 再開した
    _tick()
    assert "limited_at" not in _task(admin_client, tid, num)
    fake.screens["aa000001"] = LIMITED
    assert [e["kind"] for e in _tick()] == ["limited"]


def test_console_closed_recorded_once(admin_client, fake, sent):
    tid, num = _setup(admin_client)
    del fake.sessions["aa000001"]
    assert _tick() == []
    assert _tick() == []
    events = [h["event"] for h in _task(admin_client, tid, num)["history"]]
    assert events.count("コンソールが閉じた") == 1


def test_capture_once_per_assignee(admin_client, fake, sent):
    tid, _ = _setup(admin_client)
    admin_client.post(f"{API}/{tid}/tasks", headers=CSRF_HEADERS, json={"title": "二", "assignee": "aa000001"})
    admin_client.put(f"{API}/{tid}/tasks/2", headers=CSRF_HEADERS, json={"status": "doing"})
    fake.screens["aa000001"] = "> \n"
    fake.captures = 0
    _tick()
    assert fake.captures == 2  # 一覧の状態判定 1 回 + 同じ担当の 2 タスクで 1 回


def test_notify_toggle_per_team(admin_client, fake, sent):
    tid, num = _setup(admin_client)
    assert admin_client.get(API).json()["teams"][0]["notify"] is True
    r = admin_client.put(f"{API}/{tid}/notify", headers=CSRF_HEADERS, json={"notify": False})
    assert r.status_code == 200 and r.json()["notify"] is False
    assert admin_client.put(f"{API}/{tid}/notify", headers=CSRF_HEADERS, json={"notify": "x"}).status_code == 422
    fake.screens["aa000001"] = REPORT5
    assert _tick() == []  # 通知は出さないが取り込みはする
    assert _task(admin_client, tid, num)["status"] == "review"


def test_run_once_sends_through_notification_channels(admin_client, fake, monkeypatch):
    """send は有効な通知先すべてへ send_notification で送る（宛先の取得と送信の呼び出し）。"""
    calls: list[tuple] = []

    async def fake_channels():
        return [("push", ""), ("discord", "https://example.invalid/hook")]

    async def fake_send_notification(channel_type, url, title, message, *, kind="alert", urgent=False):  # noqa: ARG001
        calls.append((channel_type, title, message, kind))
        return True

    monkeypatch.setattr(team_watch, "_channels", fake_channels)
    monkeypatch.setattr("app.alerts.notify.send_notification", fake_send_notification)
    _setup(admin_client)
    fake.screens["aa000001"] = REPORT5
    import time

    events = asyncio.run(team_watch.run_once(time.time() + 1000))
    assert len(events) == 1
    assert [c[0] for c in calls] == ["push", "discord"] and all(c[3] == "team_report" for c in calls)   # 報告の出来事は「担い手の報告」の種類（2026-11-28 に種類を分けた）

