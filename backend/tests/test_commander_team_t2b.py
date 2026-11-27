"""Nexarch の AI チーム T2b（チームの前提・会話のリセット・確認のコマンドの自動実行と自動の再挑戦）の試験。

目的: 「合否はコードが確かめる・タスクごとに新しい会話・チームの前提を毎回渡す・落ちたら自動で直させる」を守る。
- brief が送り込みの文面の先頭へ付く・リセットのコマンドが CLI ごとに正しく、オフなら送らず、CLI 不明なら飛ばす。
- checks の検証（個数・長さ・制御文字）。
- 実行は本物の小さなコマンド（passed/failed/timeout）。shell を使わない（&& が効かない）・環境変数は最小限・
  作業フォルダが分からない／許可の外なら実行せず error。
- 落ちたら 2 回まで自動で再送（リセットしない）、3 回目も落ちたら blocked と通知。監査ログにはコマンドと終了コードだけ。
根拠: 利用者の方針（2026-11-27）「勝手に完成している状態を目指して欲しい。ユーザーの負担はない形」と
docs/PLAN-nexarch-team.md（検証できる完了報告・落ちたら自動で直させる）。
Terminal は FakeManager（tmux は使わない）。隔離テストは env -u CONTROL_DECK_CONFIG で流す。
"""
from __future__ import annotations

import shlex
import sys
import time

import pytest
from sqlalchemy import select

from app.commander import consoles, team_checks, team_watch, teams
from app.config import get_config
from app.database import SessionLocal
from app.models import AuditLog
from tests.conftest import CSRF_HEADERS

API = "/api/v1/commander/teams"
REPORT5 = ("完了報告: 画面を作った\n変更ファイル: src/a.py\n試験: pytest 12 件合格\n"
           "確認: pytest -q → 12 passed\n残り: なし")
PY = shlex.quote(sys.executable)
OK = f"{PY} -c \"import sys; sys.exit(0)\""
NG = f"{PY} -c \"import sys; print('boom-' + 'output'); sys.exit(1)\""
ENV_DUMP = f"{PY} -c \"import os; print(sorted(os.environ))\""


class FakeManager:
    def __init__(self, cwd: str = "") -> None:
        self.sessions = {
            "aa000001": {"id": "aa000001", "alive": True, "commander_cli": "claude", "program": "claude"},
            "aa000002": {"id": "aa000002", "alive": True, "commander_cli": "codex", "program": "codex"},
            "aa000003": {"id": "aa000003", "alive": True, "commander_cli": "opencode", "program": "opencode"},
            "aa000004": {"id": "aa000004", "alive": True, "commander_cli": "gemini", "program": "gemini"},
            "aa000005": {"id": "aa000005", "alive": True, "commander_cli": "mystery", "program": "mystery"},
        }
        self.screens: dict[str, str] = {}
        self.injected: list[tuple[str, str, bool]] = []
        self.cwd = cwd

    def list_sessions(self):
        return [dict(s) for s in self.sessions.values()]

    def capture_text(self, sid, lines=60):  # noqa: ARG002
        return self.screens.get(sid, "")

    def inject_input(self, sid, text, *, submit=True):
        self.injected.append((sid, text, submit))
        return len(text)

    def tag_session(self, sid, marks):  # noqa: ARG002
        pass

    def clear_marks(self, sid, keys):  # noqa: ARG002
        pass

    def session_cwd(self, sid):  # noqa: ARG002
        return self.cwd


@pytest.fixture()
def work(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "proj").mkdir(parents=True)
    monkeypatch.setattr(get_config().commander, "workdir_roots", [str(root)])
    return root / "proj"


@pytest.fixture()
def fake(monkeypatch, work):
    fm = FakeManager(str(work))
    for module in (consoles, teams, team_watch, team_checks):
        monkeypatch.setattr(module, "manager", fm)
    monkeypatch.setattr(teams, "RESET_WAIT", 0)
    # 確認の実行はスレッドを使わず、その場で走らせる
    monkeypatch.setattr(team_checks, "_spawn", lambda func, *args: func(*args))
    return fm


@pytest.fixture()
def sent(monkeypatch):
    out: list[tuple[str, str]] = []

    async def fake_send(title, message):
        out.append((title, message))

    monkeypatch.setattr(team_watch, "send", fake_send)
    return out


@pytest.fixture(autouse=True)
def _clean_store():
    path = teams._path()
    if path.exists():
        path.unlink()
    yield
    if path.exists():
        path.unlink()


def _team(client, sid="aa000001", **task_fields) -> tuple[str, int]:
    team = client.post(API, headers=CSRF_HEADERS, json={"name": "開発班"}).json()
    client.put(f"{API}/{team['id']}/members", headers=CSRF_HEADERS, json={"members": [{"session_id": sid, "role": "developer"}]})
    task = client.post(f"{API}/{team['id']}/tasks", headers=CSRF_HEADERS,
                       json={"title": "ログイン画面", "assignee": sid, **task_fields})
    assert task.status_code == 201, task.text
    return team["id"], task.json()["id"]


def _put(client, tid, **body):
    return client.put(f"{API}/{tid}", headers=CSRF_HEADERS, json=body)


def _task(client, tid, num=1) -> dict:
    return next(t for t in client.get(API).json()["teams"] if t["id"] == tid)["tasks"][num - 1]


def _dispatch(client, tid, num=1):
    return client.post(f"{API}/{tid}/tasks/{num}/dispatch", headers=CSRF_HEADERS)


def _report(client, tid, num=1):
    return client.put(f"{API}/{tid}/tasks/{num}/report", headers=CSRF_HEADERS,
                      json={"summary": "直した", "tests": "ok", "verify": "x"})


# ---------------------------------------------------------------- brief とリセット

def test_brief_is_prefixed_and_empty_brief_is_not(admin_client, fake):
    tid, num = _team(admin_client)
    _dispatch(admin_client, tid, num)
    assert fake.injected[-1][1].startswith("【Nexarch タスク #1】")
    r = _put(admin_client, tid, brief="このリポジトリは pytest で試験する。\n2 行目")
    assert r.status_code == 200 and r.json()["brief"].startswith("このリポジトリ")
    _dispatch(admin_client, tid, num)
    assert fake.injected[-1][1].startswith("【チームの前提】\nこのリポジトリは pytest で試験する。\n2 行目\n\n【Nexarch タスク #1】")


def test_brief_validation(admin_client, fake):
    tid, _ = _team(admin_client)
    assert _put(admin_client, tid, brief="あ" * 8001).status_code == 400
    assert _put(admin_client, tid, brief="a\x00b").status_code == 400
    assert _put(admin_client, tid, brief="a\x1bb").status_code == 400
    assert _put(admin_client, tid, brief="a\tb\nc").status_code == 200
    assert _put(admin_client, tid, reset_before_dispatch="maybe").status_code == 422


@pytest.mark.parametrize("sid,command", [("aa000001", "/clear"), ("aa000002", "/new"), ("aa000003", "/new"), ("aa000004", "/clear")])
def test_reset_command_per_cli_then_wait_then_body(admin_client, fake, monkeypatch, sid, command):
    tid, num = _team(admin_client, sid=sid)
    sleeps: list[float] = []
    monkeypatch.setattr(teams, "RESET_WAIT", 3.0)

    def fake_sleep(seconds):
        sleeps.append(seconds)
        fake.injected.append(("sleep", "", True))

    monkeypatch.setattr(teams.time, "sleep", fake_sleep)
    assert _dispatch(admin_client, tid, num).status_code == 200
    assert [i[1] for i in fake.injected[:2]] == [command, ""] and fake.injected[0][0] == sid
    assert fake.injected[1][0] == "sleep"
    assert fake.injected[2][1].startswith("【Nexarch タスク #1】")
    assert sleeps == [3.0]


def test_reset_off_and_unknown_cli_do_not_send_reset(admin_client, fake):
    tid, num = _team(admin_client)
    assert _put(admin_client, tid, reset_before_dispatch=False).json()["reset_before_dispatch"] is False
    _dispatch(admin_client, tid, num)
    assert len(fake.injected) == 1 and fake.injected[0][1].startswith("【Nexarch")
    tid2, num2 = _team(admin_client, sid="aa000005")
    fake.injected.clear()
    _dispatch(admin_client, tid2, num2)
    assert len(fake.injected) == 1  # CLI が分からなければリセットは飛ばす


# ---------------------------------------------------------------- checks の検証

def test_checks_validation(admin_client, fake):
    tid, num = _team(admin_client, checks=["pytest -q", "  ", "ruff check ."])
    assert _task(admin_client, tid, num)["checks"] == ["pytest -q", "ruff check ."]
    url = f"{API}/{tid}/tasks"

    def post(checks):
        return admin_client.post(url, headers=CSRF_HEADERS, json={"title": "t", "checks": checks})

    assert post(["a"] * 11).status_code == 400
    assert post(["a" * 501]).status_code == 400
    assert post(["a\nb"]).status_code == 400
    assert post(["a\x00"]).status_code == 400
    assert post(["a"] * 10).status_code == 201
    r = admin_client.put(f"{url}/{num}", headers=CSRF_HEADERS, json={"checks": ["x" * 501]})
    assert r.status_code == 400
    r = admin_client.put(f"{url}/{num}", headers=CSRF_HEADERS, json={"checks": []})
    assert r.status_code == 200 and r.json()["checks"] == []


# ---------------------------------------------------------------- 実行

def test_passed_keeps_review_and_records_results(admin_client, fake, sent):
    tid, num = _team(admin_client, checks=[OK, OK])
    _dispatch(admin_client, tid, num)
    _report(admin_client, tid, num)
    task = _task(admin_client, tid, num)
    assert task["status"] == "review"
    result = task["checks_result"]
    assert result["status"] == "passed" and len(result["results"]) == 2
    assert all(r["exit_code"] == 0 and "seconds" in r for r in result["results"])
    assert task["history"][-1]["event"] == "確認: 合格"
    assert not sent


def test_failed_runs_stop_at_first_failure_and_keep_output_tail(admin_client, fake):
    tid, num = _team(admin_client, checks=[NG, OK])
    _report(admin_client, tid, num)
    result = _task(admin_client, tid, num)["checks_result"]
    assert result["status"] == "failed" and len(result["results"]) == 1
    assert result["results"][0]["exit_code"] == 1 and "boom-output" in result["results"][0]["tail"]


def test_output_tail_is_limited_to_2000(admin_client, fake):
    big = f"{PY} -c \"print('x' * 5000)\""
    tid, num = _team(admin_client, checks=[big])
    _report(admin_client, tid, num)
    assert len(_task(admin_client, tid, num)["checks_result"]["results"][0]["tail"]) <= 2000


def test_timeout_is_failed(admin_client, fake, monkeypatch):
    monkeypatch.setattr(team_checks, "CHECK_TIMEOUT", 1)
    slow = f"{PY} -c \"import time; time.sleep(30)\""
    tid, num = _team(admin_client, checks=[slow])
    started = time.monotonic()
    _report(admin_client, tid, num)
    assert time.monotonic() - started < 20
    row = _task(admin_client, tid, num)["checks_result"]["results"][0]
    assert row["exit_code"] == -1 and "タイムアウト" in row["tail"]


def test_no_shell(admin_client, fake):
    tid, num = _team(admin_client, checks=["echo hello && exit 1"])
    _report(admin_client, tid, num)
    first = _task(admin_client, tid, num)["checks_result"]["results"][0]
    assert first["exit_code"] == 0 and "hello && exit 1" in first["tail"]  # && はただの引数


def test_minimal_env(admin_client, fake, monkeypatch):
    monkeypatch.setenv("NEXARCH_SECRET_TEST", "leak")
    tid, num = _team(admin_client, checks=[ENV_DUMP])
    _report(admin_client, tid, num)
    tail = _task(admin_client, tid, num)["checks_result"]["results"][0]["tail"]
    assert "NEXARCH_SECRET_TEST" not in tail and "PATH" in tail


def test_runs_in_the_assignee_workdir(admin_client, fake, work):
    pwd = f"{PY} -c \"import os; print(os.getcwd())\""
    tid, num = _team(admin_client, checks=[pwd])
    _report(admin_client, tid, num)
    assert str(work.resolve()) in _task(admin_client, tid, num)["checks_result"]["results"][0]["tail"]


def test_unknown_or_forbidden_workdir_is_error_and_nothing_runs(admin_client, fake, tmp_path):
    marker = tmp_path / "ran"
    touch = f"{PY} -c \"open({str(marker)!r}, 'w')\""
    for cwd in ("", str(tmp_path), "relative/path"):
        fake.cwd = cwd
        tid, num = _team(admin_client, checks=[touch])
        _report(admin_client, tid, num)
        task = _task(admin_client, tid, num)
        assert task["checks_result"]["status"] == "error" and "作業フォルダ" in task["checks_result"]["message"]
        assert task["status"] == "review"  # 自動の再挑戦はしない
    assert not marker.exists()


def test_missing_executable_is_failed_not_crash(admin_client, fake):
    tid, num = _team(admin_client, checks=["definitely-not-a-command-xyz"])
    _report(admin_client, tid, num)
    result = _task(admin_client, tid, num)["checks_result"]
    assert result["status"] == "failed" and result["results"][0]["exit_code"] == 127


def test_task_without_checks_is_as_before(admin_client, fake):
    tid, num = _team(admin_client)
    _report(admin_client, tid, num)
    task = _task(admin_client, tid, num)
    assert task["status"] == "review" and "checks_result" not in task


# ---------------------------------------------------------------- 自動の再挑戦

def test_failure_retries_twice_then_blocks_and_notifies(admin_client, fake, sent):
    tid, num = _team(admin_client, checks=[NG])
    _dispatch(admin_client, tid, num)
    fake.injected.clear()
    for attempt in (1, 2):
        _report(admin_client, tid, num)
        task = _task(admin_client, tid, num)
        assert task["status"] == "doing" and task["attempts"] == attempt
        sid, text, _ = fake.injected[-1]
        assert sid == "aa000001"
        assert text.startswith(f"【Nexarch タスク #1 再挑戦 {attempt}/2】確認のコマンドが失敗しました。直して、同じ 5 行で報告してください。")
        assert "boom-output" in text and "終了コード 1" in text
    assert all(i[1] not in ("/clear", "/new") for i in fake.injected)  # 再挑戦はリセットしない
    _report(admin_client, tid, num)  # 3 回目も落ちる
    task = _task(admin_client, tid, num)
    assert task["status"] == "blocked" and task["attempts"] == 2
    assert len(fake.injected) == 2  # 3 回目の再送はしない
    assert len(sent) == 1 and "#1" in sent[0][1] and "ログイン画面" in sent[0][1] and "boom" not in sent[0][1]


def test_blocked_notification_respects_team_notify_off(admin_client, fake, sent):
    tid, num = _team(admin_client, checks=[NG])
    admin_client.put(f"{API}/{tid}/notify", headers=CSRF_HEADERS, json={"notify": False})
    for _ in range(3):
        _report(admin_client, tid, num)
    assert _task(admin_client, tid, num)["status"] == "blocked" and not sent


def test_manual_dispatch_resets_attempts_and_result(admin_client, fake, sent):
    tid, num = _team(admin_client, checks=[NG])
    for _ in range(3):
        _report(admin_client, tid, num)
    assert _task(admin_client, tid, num)["status"] == "blocked"
    assert _dispatch(admin_client, tid, num).status_code == 200  # blocked からも人が送り直せる
    task = _task(admin_client, tid, num)
    assert task["status"] == "doing" and task["attempts"] == 0 and "checks_result" not in task


def test_stale_result_is_dropped_when_task_left_review(admin_client, fake):
    tid, num = _team(admin_client, checks=[OK])
    admin_client.put(f"{API}/{tid}/tasks/{num}", headers=CSRF_HEADERS, json={"status": "done"})
    team_checks.run_checks(tid, num)
    assert "checks_result" not in _task(admin_client, tid, num)


def test_watch_auto_collect_runs_checks(admin_client, fake, sent):
    tid, num = _team(admin_client, checks=[OK])
    _dispatch(admin_client, tid, num)
    fake.screens["aa000001"] = "【Nexarch タスク #1】ログイン画面\n" + REPORT5
    events = team_watch.tick(time.time() + 1000)
    assert [e["kind"] for e in events] == ["report"]
    task = _task(admin_client, tid, num)
    assert task["status"] == "review" and task["checks_result"]["status"] == "passed"


def test_watch_does_not_pick_old_report_after_retry_marker(admin_client, fake, sent):
    tid, num = _team(admin_client, checks=[NG])
    _dispatch(admin_client, tid, num)
    fake.screens["aa000001"] = ("【Nexarch タスク #1】ログイン画面\n" + REPORT5
                                + "\n【Nexarch タスク #1 再挑戦 1/2】確認のコマンドが失敗しました。\n> \n")
    _report(admin_client, tid, num)  # 失敗 → doing
    assert _task(admin_client, tid, num)["status"] == "doing"
    kinds = [e["kind"] for e in team_watch.tick(time.time() + 1000)]
    assert kinds == ["stalled"]  # 古い報告は拾わない


# ---------------------------------------------------------------- 監査ログ

def test_audit_has_commands_and_exit_codes_but_no_output(admin_client, fake):
    tid, num = _team(admin_client, checks=[NG])
    _report(admin_client, tid, num)
    with SessionLocal() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.action == "commander.team.checks")).all()
        blobs = [str(vars(r)) for r in rows]
    assert blobs
    assert "exit_code" in blobs[-1] and "boom-output" not in blobs[-1] and "sys.exit(1)" in blobs[-1]
    assert _put(admin_client, tid, brief="秘密のメモ").status_code == 200
    with SessionLocal() as db:
        upd = [str(vars(r)) for r in db.scalars(select(AuditLog).where(AuditLog.action == "commander.team.update")).all()]
    assert upd and "秘密のメモ" not in upd[-1]
