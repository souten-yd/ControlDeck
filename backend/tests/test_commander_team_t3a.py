"""Nexarch の AI チーム T3a（ヘッドレスの担い手・上限の方針・同時並列と配り手・チームの作業場所）の試験。

目的: 利用者（2026-11-27）の要望を仕組みで守る。
- 「勝手に完成している状態を目指して」: ヘッドレスの実行が報告を板へ取り込み、確認・再挑戦へつながり、待ちのタスクを配り手が自動で割り当てる。
- 「チームがトークンが尽きて止まった場合、作業内容を引き継いで自動でルーターが別の CLI を開くか、待つかを設定で選択できるように」:
  on_limit = handoff（前の報告・進み具合・git の変更を本文に付けて別の担い手へ。同じ担い手は選ばない。候補の既定の順）／
  wait（回復の時刻まで待って同じ担当で再実行。読めなければ 30 分）。実際にやった担い手を runs に残す。
- 「同時並列処理の場合はどうするのか」: 同時の上限・担い手ごとに 1 つ・担当ファイルの重なりで待つ・ローカルの上限。
根拠: 利用者の要望（2026-11-27）と docs/PLAN-nexarch-team.md T3。
codex のサンドボックス（bwrap）がこの機械で使えない問題（Ubuntu の apparmor_restrict_unprivileged_userns=1。2026-11-27 実機で
書き込みも shell も全部 failed なのに終了コード 0 で「完了」）: 使えるかを一度だけ調べて覚え、使えなければ danger-full-access＋印、
codex_without_sandbox=false なら止める。codex の `status: failed` の item を失敗として数え、書き込みが全部失敗した実行は
未検証・review へ進めず、同じ担い手で 1 回再挑戦 → 続けて同じなら引き継ぎの方針へ。実際の codex の出力の形を試験に使う。
CLI は本物を呼ばない。`accounts.executable_path` を小さな偽の実行ファイル（stream-json・codex の json・opencode の json を出す script）
へ差し替える。Terminal は FakeManager（tmux は使わない）。隔離テストは env -u CONTROL_DECK_CONFIG で流す。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.commander import accounts, consoles, team_checks, team_dispatch, team_runner, team_watch, teams
from app.config import get_config
from app.database import SessionLocal
from app.models import AuditLog
from tests.conftest import CSRF_HEADERS

API = "/api/v1/commander/teams"
REPORT5 = ("完了報告: 画面を作った\n変更ファイル: src/a.py\n試験: pytest 12 件合格\n"
           "確認: pytest -q → 12 passed\n残り: なし")

SCRIPT = r'''#!@@PY@@
import sys, json, os, time
REC = "@@REC@@"
KIND = "@@KIND@@"
MODE = "@@MODE@@"
args = sys.argv[1:]
with open(REC, "a") as f:
    f.write(json.dumps({"kind": KIND, "argv": args, "cwd": os.getcwd(), "env": {
        k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "SECRET_FROM_PARENT")}}) + "\n")
REPORT = "完了報告: 画面を作った\n変更ファイル: src/a.py\n試験: pytest 12 件合格\n確認: pytest -q → 12 passed\n残り: なし"
def out(o):
    print(json.dumps(o, ensure_ascii=False), flush=True)
text = "終わりました" if MODE == "noreport" else REPORT
if KIND == "claude":
    out({"type": "system", "subtype": "init"})
    out({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "作業を始めます\n次にテストを回します"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]}})
    if MODE == "slow":
        time.sleep(60)
    if MODE == "fail":
        sys.stderr.write("boom-stderr\n"); sys.exit(1)
    if MODE in ("limit", "limit_unknown"):
        msg = "5-hour limit reached ∙ resets 10:20pm (Asia/Tokyo)" if MODE == "limit" else "Claude AI usage limit reached"
        out({"type": "result", "subtype": "error", "is_error": True, "result": msg}); sys.exit(1)
    out({"type": "result", "subtype": "success", "is_error": False, "result": text, "total_cost_usd": 0.25,
         "usage": {"input_tokens": 100, "output_tokens": 50}})
elif KIND == "codex":
    out({"type": "thread.started", "thread_id": "t1"})
    out({"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "ls"}})
    if MODE == "writefail":
        out({"type": "item.completed", "item": {"id": "item_1", "type": "file_change",
             "changes": [{"path": "/x/hello.txt", "kind": "add"}], "status": "failed"}})
        open(args[args.index("-o") + 1], "w").write(text)
        out({"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": text}})
        out({"type": "turn.completed", "usage": {"input_tokens": 200, "output_tokens": 80}})
        sys.exit(0)
    if MODE == "fail":
        sys.stderr.write("boom-stderr\n"); sys.exit(1)
    if MODE == "limit":
        out({"type": "turn.failed", "error": {"message": "You've hit your usage limit. try again in 2 hours 5 minutes"}}); sys.exit(1)
    open(args[args.index("-o") + 1], "w").write(text)
    out({"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": text}})
    out({"type": "turn.completed", "usage": {"input_tokens": 200, "output_tokens": 80}})
else:
    out({"type": "tool_use", "part": {"tool": "bash", "state": {"input": {"command": "ls"}}}})
    if MODE == "fail":
        sys.stderr.write("boom-stderr\n"); sys.exit(1)
    if MODE == "limit":
        out({"type": "error", "error": {"name": "APIError", "message": "429 rate limit exceeded"}}); sys.exit(1)
    out({"type": "text", "part": {"text": text}})
    out({"type": "step_finish", "part": {"tokens": {"input": 10, "output": 5}, "cost": 0.01}})
'''


class FakeManager:
    def __init__(self, cwd: str = "") -> None:
        self.sessions = {
            "aa000001": {"id": "aa000001", "alive": True, "commander_cli": "claude", "program": "claude"},
            "aa000002": {"id": "aa000002", "alive": True, "commander_cli": "codex", "program": "codex"},
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


class Env:
    """偽の CLI の置き場と、実行の記録の読み出し。"""

    def __init__(self, tmp: Path, work: Path, manager: FakeManager) -> None:
        self.tmp = tmp
        self.work = work
        self.manager = manager
        self.rec = tmp / "calls.jsonl"
        self.scripts: dict[str, str] = {}
        for cli in ("claude", "codex", "opencode"):
            self.mode(cli, "ok")

    def mode(self, cli: str, mode: str) -> None:
        path = self.tmp / f"fake-{cli}"
        path.write_text(SCRIPT.replace("@@PY@@", sys.executable).replace("@@REC@@", str(self.rec))
                        .replace("@@KIND@@", cli).replace("@@MODE@@", mode), encoding="utf-8")
        path.chmod(0o755)
        self.scripts[cli] = str(path)

    def calls(self, kind: str = "") -> list[dict]:
        if not self.rec.exists():
            return []
        rows = [json.loads(ln) for ln in self.rec.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [r for r in rows if not kind or r["kind"] == kind]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "console").mkdir(parents=True)
    monkeypatch.setattr(get_config().commander, "workdir_roots", [str(root)])
    manager = FakeManager(str(root / "console"))
    for module in (consoles, teams, team_watch, team_checks, team_dispatch):
        monkeypatch.setattr(module, "manager", manager)
    monkeypatch.setattr(teams, "RESET_WAIT", 0)
    count = {"n": 0}

    def make_workdir(name):  # CodeDEV の代わりに root の下へ。git init もする
        count["n"] += 1
        path = root / f"team-{count['n']}"
        path.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        return {"workdir": str(path), "workdir_folder": path.name, "workdir_name": name}

    monkeypatch.setattr(teams, "_make_workdir", make_workdir)
    e = Env(tmp_path, root / "team-1", manager)
    e.real_opencode_launch = team_runner._opencode_launch
    monkeypatch.setattr(accounts, "executable_path", lambda cli: e.scripts.get(cli.id))
    monkeypatch.setattr(team_runner, "_opencode_launch",
                        lambda model, cwd, text, **kw: ([e.scripts["opencode"], "run", text, "--model", model], {}, lambda: None))
    # 実行・確認はその場で走らせる（スレッドを待たない）
    monkeypatch.setattr(team_runner, "_spawn", lambda func, *args: func(*args))
    monkeypatch.setattr(team_checks, "_spawn", lambda func, *args: func(*args))
    monkeypatch.setattr(team_runner, "POLL_INTERVAL", 0.02)
    monkeypatch.setattr(team_runner, "codex_sandbox_ok", lambda: True)
    yield e
    with team_runner._active_lock:
        team_runner._active.clear()


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


def prompt_of(call: dict) -> str:
    argv = call["argv"]
    return argv[argv.index("-p") + 1] if "-p" in argv else argv[-1]


def H(cli: str, account: str = "default", role: str = "developer", label: str = "") -> dict:
    return {"kind": "headless", "cli": cli, "account": account, "role": role, "label": label}


def mk_team(members: list[dict], **settings) -> dict:
    team = teams.create_team("開発班")
    if settings:
        teams.update_team(team["id"], settings)
    teams.set_members(team["id"], members)
    return next(t for t in teams._load() if t["id"] == team["id"])


def keys(team: dict) -> list[str]:
    return [teams.member_key(m) for m in team["members"]]


def mk_task(team: dict, assignee: str = "", **fields) -> int:
    return teams.create_task(team["id"], {"title": "ログイン画面", "goal": "作る", "assignee": assignee, **fields}, "t")["id"]


def get_task(team_id: str, task_id: int) -> dict:
    team = next(t for t in teams._load() if t["id"] == team_id)
    return teams._find_task(team, task_id)


def get_team(team_id: str) -> dict:
    return next(t for t in teams._load() if t["id"] == team_id)


# ---------------------------------------------------------------- チームの作業場所・メンバー・設定

def test_create_team_makes_workdir_and_defaults(env):
    team = teams.create_team("開発班")
    assert team["workdir"] == str(env.work) and env.work.is_dir()
    assert (env.work / ".git").is_dir()
    assert (team["on_limit"], team["max_parallel"], team["local_parallel"], team["auto_run"]) == ("handoff", 3, 1, True)
    shown = next(t for t in teams.list_teams() if t["id"] == team["id"])
    assert shown["workdir_name"] == "開発班" and shown["parallel"] == {"running": 0, "max": 3, "waiting": 0, "local_max": 1}


def test_workdir_is_recreated_when_missing(env):
    team = teams.create_team("開発班")
    import shutil
    shutil.rmtree(env.work, ignore_errors=True)
    again = teams.ensure_workdir(team["id"])
    assert Path(again).is_dir() and again != str(env.work)


def test_headless_member_validation_and_id_kept(env):
    team = teams.create_team("班")
    done = teams.set_members(team["id"], [{"session_id": "aa000001", "role": "leader"}, H("claude", label="Claude 係")])
    hid = done["members"][1]["id"]
    assert done["members"][1]["kind"] == "headless" and hid.startswith("h") and len(hid) == 9
    again = teams.set_members(team["id"], [{**done["members"][1]}])
    assert again["members"][0]["id"] == hid  # 引き継ぐ
    for bad in (H("gemini"), H("claude", role="boss"), H("claude", account="nope")):
        with pytest.raises(teams.TeamError):
            teams.set_members(team["id"], [bad])


def test_update_team_policy_validation(env):
    team = teams.create_team("班")
    ok = teams.update_team(team["id"], {"on_limit": "wait", "max_parallel": 5, "local_parallel": 2, "auto_run": False})
    assert (ok["on_limit"], ok["max_parallel"], ok["local_parallel"], ok["auto_run"]) == ("wait", 5, 2, False)
    for bad in ({"on_limit": "x"}, {"max_parallel": 0}, {"max_parallel": 9}, {"max_parallel": True}, {"local_parallel": "2"},
                {"fallback": ["nobody"]}):
        with pytest.raises(teams.TeamError):
            teams.update_team(team["id"], bad)


def test_fallback_order_saved_and_pruned_when_member_removed(env):
    team = mk_team([H("claude"), H("codex")])
    a, b = keys(team)
    teams.update_team(team["id"], {"fallback": [b, a]})
    assert get_team(team["id"])["fallback"] == [b, a]
    teams.set_members(team["id"], [team["members"][0]])
    assert get_team(team["id"])["fallback"] == [a]


# ---------------------------------------------------------------- 引数の組み立て

def test_claude_argv_and_account_env(env):
    accounts.create_account(accounts.CLIS["claude"], "work")
    member = {"cli": "claude", "account": "work"}
    argv, e, _ = team_runner.build_launch(member, "【本文】", env.work, env.tmp / "last")
    assert argv[:3] == [env.scripts["claude"], "-p", "【本文】"]
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    tools = argv[argv.index("--allowedTools") + 1].split(",")
    assert {"Bash", "Edit", "Read", "Write"} <= set(tools)
    assert e["CLAUDE_CONFIG_DIR"].endswith("/claude/work")
    default, e2, _ = team_runner.build_launch({"cli": "claude", "account": "default"}, "x", env.work, env.tmp / "last")
    assert "CLAUDE_CONFIG_DIR" not in e2


def test_codex_argv_and_account_env(env):
    accounts.create_account(accounts.CLIS["codex"], "sub")
    argv, e, _ = team_runner.build_launch({"cli": "codex", "account": "sub"}, "【本文】", env.work, env.tmp / "last")
    assert argv[1:3] == ["exec", "--json"]
    assert argv[argv.index("-C") + 1] == str(env.work)
    assert argv[argv.index("-s") + 1] == "workspace-write"
    assert argv[argv.index("-o") + 1] == str(env.tmp / "last")
    assert argv[-1] == "【本文】"
    assert e["CODEX_HOME"].endswith("/codex/sub")


def test_opencode_argv_uses_provider_config(env, monkeypatch, tmp_path):
    from app.integrations.opencode import provider
    from app.models_mgmt import local_llm

    seen: dict = {}
    config = tmp_path / "ocfg"
    config.mkdir()
    monkeypatch.setattr(provider, "active_binary", lambda preferred="": ("v2", Path(env.scripts["opencode"])))
    monkeypatch.setattr(provider, "get_settings", lambda: {"base_url": "http://gw/v1", "model": "auto"})
    monkeypatch.setattr(provider, "_runtime_config", lambda job, url, model, **kw: seen.update(url=url, model=model) or config)
    monkeypatch.setattr(provider, "_managed_project_id", lambda p: None)
    monkeypatch.setattr(provider, "_launch", lambda rt, argv, project, cfg, interactive, **kw: (argv, {"XDG_CONFIG_HOME": str(cfg)}))
    monkeypatch.setattr(accounts, "opencode_connection", lambda account: {"name": account, "model": "qwen-local"})

    async def ready(url):
        seen["ready"] = url

    monkeypatch.setattr(local_llm, "ensure_ready_by_base_url", ready)
    func = env.real_opencode_launch
    argv, e, cleanup = func("qwen-local", env.work, "【本文】")
    assert argv[1] == "run" and argv[2] == "【本文】"
    assert argv[argv.index("--format") + 1] == "json" and "--auto" in argv and "--standalone" in argv
    assert argv[argv.index("--model") + 1] == "controldeck/qwen-local"
    assert seen["url"] == "http://gw/v1" and seen["model"] == "qwen-local" and seen["ready"] == "http://gw/v1"
    assert e["XDG_CONFIG_HOME"] == str(config)


def test_missing_executable_blocks_without_retry(env):
    env.scripts.pop("claude")
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "blocked" and task["run"]["status"] == "failed"
    assert "見つかりません" in task["run"]["tail"] and not env.calls()


# ---------------------------------------------------------------- 実行・報告・使用量

def test_claude_run_reports_progress_usage_and_runs(env):
    team = mk_team([H("claude", label="Claude 係")])
    tid = mk_task(team, keys(team)[0], goal="目的の文", files=["src/a.py"])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "review", ([h["event"] for h in task["history"]], task["run"].get("tail"))
    assert task["report"]["summary"] == "画面を作った" and task["report"]["verify"].startswith("pytest -q")
    assert task["run"]["status"] == "finished" and task["run"]["exit_code"] == 0
    assert task["run"]["usage"] == {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.25}
    assert task["run"]["progress"] == ["Bash: pytest -q"] and task["run"]["last_line"] == "Bash: pytest -q"
    assert "作業を始めます" in task["run"]["tail"] or "画面を作った" in task["run"]["tail"]
    [run] = task["runs"]
    assert (run["result"], run["kind"], run["cli"]) == ("ok", "headless", "claude")
    assert run["seconds"] is not None and run["usage"]["output_tokens"] == 50
    call = env.calls("claude")[0]
    assert call["cwd"] == str(env.work)
    prompt = call["argv"][call["argv"].index("-p") + 1]
    assert "目的の文" in prompt and "完了報告:" in prompt and "src/a.py" in prompt
    assert any("実行を開始" in h["event"] for h in task["history"])


def test_brief_is_in_prompt_each_run(env):
    team = mk_team([H("claude")], brief="前提の文")
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    assert "前提の文" in prompt_of(env.calls("claude")[0])


def test_codex_run_takes_report_from_last_message_file(env):
    team = mk_team([H("codex")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "review" and task["report"]["summary"] == "画面を作った"
    assert task["run"]["usage"]["input_tokens"] == 200 and task["run"]["progress"] == ["shell: ls"]


def test_opencode_run(env):
    team = mk_team([H("opencode")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "review" and task["report"]["tests"] == "pytest 12 件合格"
    assert task["run"]["usage"] == {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}


def test_no_report_makes_unverified_report(env):
    env.mode("claude", "noreport")
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "review" and task["report"]["summary"] == "終わりました"
    assert teams.is_unverified(task)


def test_secrets_not_logged_and_env_is_minimal(env, caplog, monkeypatch):
    monkeypatch.setenv("SECRET_FROM_PARENT", "TOPSECRET")
    caplog.set_level("DEBUG")
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0], goal="SECRETWORD-GOAL")
    teams.dispatch_task(team["id"], tid, "t")
    assert "SECRETWORD-GOAL" not in caplog.text and "TOPSECRET" not in caplog.text
    assert env.calls("claude")[0]["env"]["SECRET_FROM_PARENT"] is None


def test_audit_log_has_no_body(admin_client, env):
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0], goal="SECRETWORD-GOAL")
    teams.dispatch_task(team["id"], tid, "t")
    with SessionLocal() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.action == "commander.team.run")).all()
    assert rows
    meta = json.dumps(rows[-1].metadata_json if hasattr(rows[-1], "metadata_json") else rows[-1].__dict__, default=str)
    assert "SECRETWORD-GOAL" not in meta and "claude" in meta


# ---------------------------------------------------------------- 失敗・再挑戦

def test_failed_run_retries_twice_then_blocks_with_notification(env, sent):
    env.mode("claude", "fail")
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "blocked" and task["attempts"] == 2
    calls = env.calls("claude")
    assert len(calls) == 3
    assert "再挑戦 1/2" in prompt_of(calls[1]) and "再挑戦 2/2" in prompt_of(calls[2])
    assert "【Nexarch タスク" in prompt_of(calls[1])  # 新しい会話なので全文を渡す
    assert [r["result"] for r in task["runs"]] == ["failed", "failed", "failed"]
    assert any("自動で直せませんでした" in m for _t, m in sent)


def test_checks_failure_reruns_headless_then_blocks(env, sent):
    ng = f"{shlex_q(sys.executable)} -c \"import sys; sys.exit(1)\""
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0], checks=[ng])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "blocked" and len(env.calls("claude")) == 3, ([h["event"] for h in task["history"]], task.get("checks_result"))
    assert "確認のコマンドが失敗" in prompt_of(env.calls("claude")[1])
    assert "前回の報告: 画面を作った" in prompt_of(env.calls("claude")[1])
    assert any("確認が通らず" in m for _t, m in sent)


def shlex_q(value: str) -> str:
    import shlex
    return shlex.quote(value)


def test_checks_pass_in_team_workdir(env):
    marker = f"{shlex_q(sys.executable)} -c \"import os,sys; sys.exit(0 if os.path.basename(os.getcwd()).startswith('team-') else 1)\""
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0], checks=[marker])
    teams.dispatch_task(team["id"], tid, "t")
    assert get_task(team["id"], tid)["checks_result"]["status"] == "passed"


# ---------------------------------------------------------------- 止める・中断

def test_stop_kills_running_process(env, monkeypatch):
    monkeypatch.setattr(team_runner, "_spawn", team_runner_thread)
    env.mode("claude", "slow")
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    deadline = time.time() + 10
    while time.time() < deadline:
        live = [v for v in teams.list_teams() if v["id"] == team["id"]][0]["tasks"][0]["run"]
        if live.get("last_line"):
            break
        time.sleep(0.1)
    assert live["status"] == "running" and live["last_line"] == "Bash: pytest -q" and live["elapsed"] >= 0
    assert team_runner.stop_run(team["id"], tid) is True
    deadline = time.time() + 15
    while time.time() < deadline and team_runner.is_active(team["id"], tid):
        time.sleep(0.1)
    task = get_task(team["id"], tid)
    assert task["run"]["status"] == "stopped" and task["status"] == "blocked"
    assert task["runs"][-1]["result"] == "stopped"
    assert team_runner.stop_run(team["id"], tid) is False


def team_runner_thread(func, *args):
    import threading
    threading.Thread(target=func, args=args, daemon=True).start()


def test_recover_interrupted_marks_and_waits_for_dispatcher(env, monkeypatch):
    monkeypatch.setattr(team_runner, "_spawn", lambda *a: None)  # 走ったままの状態にする
    team = mk_team([H("claude")], auto_run=False)
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    assert get_task(team["id"], tid)["run"]["status"] == "running"
    with team_runner._active_lock:  # 再起動したのでプロセスは無い
        team_runner._active.clear()
    assert team_runner.recover_interrupted() == 1
    task = get_task(team["id"], tid)
    assert task["run"]["status"] == "interrupted" and task["status"] == "todo"
    assert task["runs"][-1]["result"] == "interrupted" and "中断" in task["resume_note"]
    team_dispatch.tick()
    assert get_task(team["id"], tid)["status"] == "todo"  # auto_run が切なら自動で続けない
    teams.update_team(team["id"], {"auto_run": True})
    team_dispatch.tick()
    assert get_task(team["id"], tid)["run"]["status"] == "running"  # 方針に従って再実行


# ---------------------------------------------------------------- 上限の方針

def test_limit_detection_and_reset_time():
    assert team_runner.detect_limit(["5-hour limit reached ∙ resets 10:20pm (Asia/Tokyo)"])[0] is True
    ok, reset = team_runner.detect_limit(["Claude AI usage limit reached|1900000000"])
    assert ok and reset == 1900000000.0
    ok, reset = team_runner.detect_limit(["try again in 2 hours 5 minutes, usage limit"])
    assert ok and reset is not None and reset > time.time() + 7000
    assert team_runner.detect_limit(["Claude AI usage limit reached"]) == (True, None)
    assert team_runner.detect_limit(["syntax error in a.py"]) == (False, None)
    assert team_runner.detect_limit(["429 Too Many Requests"])[0] is True


def test_stream_state_tolerates_garbage_and_each_cli():
    state = team_runner.StreamState("claude")
    for raw in (b"not json", b"[1,2]", b'{"type":"assistant","message":null}', b'{"type":"result","usage":5}'):
        state.feed(raw)
    for cli, line in (("codex", b'{"type":"item.completed","item":5}'), ("opencode", b'{"type":"step_finish","part":[]}')):
        team_runner.StreamState(cli).feed(line)


def test_candidates_default_order_never_same_member(env):
    team = mk_team([H("claude", label="A"), H("claude", label="B"), H("codex", label="C"),
                    H("opencode", label="D（ローカル）"), H("claude", role="reviewer", label="R")])
    a, b, c, d, r = keys(team)
    order = [teams.member_key(m) for m in team_dispatch.candidates(team, a, set(), {})]
    assert order == [c, b, d]  # 別の CLI → 同じ CLI の別アカウント → ローカル。レビュー役は入れない・自分は入れない
    assert a not in order
    assert [teams.member_key(m) for m in team_dispatch.candidates(team, a, {c}, {})] == [b, d]
    teams.update_team(team["id"], {"fallback": [d, c]})
    team = get_team(team["id"])
    assert [teams.member_key(m) for m in team_dispatch.candidates(team, a, set(), {})][:2] == [d, c]


def test_handoff_to_other_cli_carries_work_and_git(env, sent):
    env.mode("claude", "limit")
    team = mk_team([H("claude", label="主担当"), H("codex", label="控え")])
    (env.work / "a.txt").write_text("途中", encoding="utf-8")
    first, second = keys(team)
    tid = mk_task(team, first)
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["assignee"] == second and task["status"] == "review"
    codex_prompt = env.calls("codex")[0]["argv"][-1]
    assert "【引き継ぎ】前の担当（主担当" in codex_prompt and "上限で止まりました" in codex_prompt
    assert "進み具合の最後の行: Bash: pytest -q" in codex_prompt
    assert "?? a.txt" in codex_prompt and "git status --short" in codex_prompt and "git diff --stat" in codex_prompt
    assert "同じ 5 行で報告" in codex_prompt
    assert [(r["cli"], r["result"]) for r in task["runs"]] == [("claude", "limited"), ("codex", "ok")]
    assert [r["n"] for r in task["runs"]] == [1, 2]
    assert any("上限で停止 → " in h["event"] and "引き継ぎ" in h["event"] for h in task["history"])
    assert any("引き継ぎました" in m for _t, m in sent)


def test_handoff_skips_busy_and_members_that_already_limited(env):
    env.mode("claude", "limit")
    env.mode("codex", "limit")
    team = mk_team([H("claude", label="A"), H("codex", label="B")])
    a, b = keys(team)
    tid = mk_task(team, a)
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    # A が上限 → B へ → B も上限 → 残りの候補が無い（A は選び直さない）→ 待つ
    assert [r["result"] for r in task["runs"]] == ["limited", "limited"]
    assert task["assignee"] == b and task.get("wait_until")
    assert len(env.calls("claude")) == 1 and len(env.calls("codex")) == 1
    assert any("引き継ぎ先がない" in h["event"] for h in task["history"])


def test_handoff_waits_when_candidates_busy(env, monkeypatch):
    env.mode("claude", "limit")
    monkeypatch.setattr(team_runner, "_spawn", lambda *a: None)
    team = mk_team([H("claude"), H("codex")])
    a, b = keys(team)
    busy = mk_task(team, b)
    teams.dispatch_task(team["id"], busy, "t")  # 控えは作業中
    tid = mk_task(team, a)
    teams.dispatch_task(team["id"], tid, "t")
    # A の実行が上限で終わったことにする
    monkeypatch.setattr(team_runner, "_spawn", lambda func, *args: func(*args))
    task = get_task(team["id"], tid)
    team_runner._complete(team["id"], tid, task["run"]["run_id"], team["members"][0],
                          {"exit": 1, "state": _limit_state(), "final": "", "stderr": "", "seconds": 1}, False)
    assert get_task(team["id"], tid)["limited_at"]
    assert team_dispatch.apply_limit_policy(team["id"], tid) == []
    assert get_task(team["id"], tid)["assignee"] == a  # 空いていないので待つ
    assert any("空くのを待つ" in h["event"] for h in get_task(team["id"], tid)["history"])


def _limit_state():
    state = team_runner.StreamState("claude")
    state._error("5-hour limit reached ∙ resets 10:20pm (Asia/Tokyo)")
    return state


def test_wait_policy_headless_waits_until_reset_then_reruns_same_member(env, sent):
    env.mode("claude", "limit")
    team = mk_team([H("claude"), H("codex")], on_limit="wait")
    a, _b = keys(team)
    tid = mk_task(team, a)
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["assignee"] == a and task["status"] == "doing" and task["run"]["status"] == "limited"
    assert task["wait_until"] > time.time() - 1 and any("回復を待つ" in h["event"] for h in task["history"])
    assert len(env.calls()) == 1 and any("上限で止まりました" in m for _t, m in sent)
    team_dispatch.tick()  # まだ時刻になっていない
    assert len(env.calls()) == 1
    env.mode("claude", "ok")
    with teams._lock:
        all_teams = teams._load()
        teams._find_task(teams._find_team(all_teams, team["id"]), tid)["wait_until"] = time.time() - 5
        teams._save(all_teams)
    team_dispatch.tick()
    task = get_task(team["id"], tid)
    assert task["status"] == "review" and len(env.calls("claude")) == 2
    assert "【引き継ぎ】回復したので同じ担当で再開します" in prompt_of(env.calls("claude")[1])
    assert [r["result"] for r in task["runs"]] == ["limited", "ok"] and {r["member"] for r in task["runs"]} == {a}


def test_wait_policy_unknown_reset_waits_30_minutes(env):
    env.mode("claude", "limit_unknown")
    team = mk_team([H("claude")], on_limit="wait")
    tid = mk_task(team, keys(team)[0])
    before = time.time()
    teams.dispatch_task(team["id"], tid, "t")
    wait = get_task(team["id"], tid)["wait_until"]
    assert before + 29 * 60 < wait < time.time() + 31 * 60


def test_codex_limit_reads_relative_reset(env):
    env.mode("codex", "limit")
    team = mk_team([H("codex")], on_limit="wait")
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    wait = get_task(team["id"], tid)["wait_until"]
    assert time.time() + 2 * 3600 < wait < time.time() + 2 * 3600 + 600


def test_opencode_rate_limit_json_error_is_limit(env):
    env.mode("opencode", "limit")
    team = mk_team([H("opencode", account="default")], on_limit="wait")
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    assert get_task(team["id"], tid)["run"]["status"] == "limited"


def test_console_limit_wait_leaves_to_auto_resume(env):
    team = mk_team([{"session_id": "aa000001", "role": "developer"}, H("codex")], on_limit="wait")
    tid = mk_task(team, "aa000001")
    with teams._lock:
        all_teams = teams._load()
        task = teams._find_task(teams._find_team(all_teams, team["id"]), tid)
        task.update(status="doing", limited_at=time.time())
        teams._save(all_teams)
    rows = [{"id": "aa000001", "alive": True, "commander_cli": "claude", "commander_state": "limited"}]
    import unittest.mock as mock
    with mock.patch.object(consoles, "list_consoles", return_value=rows):
        team_dispatch.tick()
        team_dispatch.tick()
    task = get_task(team["id"], tid)
    assert task["assignee"] == "aa000001" and not env.calls()
    assert sum("回復を待つ" in h["event"] for h in task["history"]) == 1  # 1 回だけ記録


def test_console_limit_handoff_to_headless(env, sent):
    team = mk_team([{"session_id": "aa000001", "role": "developer"}, H("codex", label="控え")])
    tid = mk_task(team, "aa000001")
    with teams._lock:
        all_teams = teams._load()
        task = teams._find_task(teams._find_team(all_teams, team["id"]), tid)
        task.update(status="doing", limited_at=time.time())
        teams._save(all_teams)
    rows = [{"id": "aa000001", "alive": True, "commander_cli": "claude", "commander_state": "limited",
             "commander_last_line": "Editing a.py"}]
    import unittest.mock as mock
    with mock.patch.object(consoles, "list_consoles", return_value=rows):
        events = team_dispatch.tick()
    task = get_task(team["id"], tid)
    assert task["assignee"] == keys(team)[1] and task["status"] == "review"
    prompt = env.calls("codex")[0]["argv"][-1]
    assert "【引き継ぎ】" in prompt and "画面の最後の行: Editing a.py" in prompt
    assert any(e["kind"] == "handoff" for e in events)


def test_handoff_to_console_injects_handoff_text(env):
    env.mode("claude", "limit")
    team = mk_team([H("claude", label="主"), {"session_id": "aa000002", "role": "developer"}])
    tid = mk_task(team, keys(team)[0])
    import unittest.mock as mock
    rows = [{"id": "aa000002", "alive": True, "commander_cli": "codex", "commander_state": "waiting", "commander_account": "default"}]
    with mock.patch.object(consoles, "list_consoles", return_value=rows), \
            mock.patch.object(teams.consoles, "list_consoles", return_value=rows):
        teams.dispatch_task(team["id"], tid, "t")
        team_dispatch.tick()
    task = get_task(team["id"], tid)
    assert task["assignee"] == "aa000002" and task["status"] == "doing"
    texts = [t for sid, t, _ in env.manager.injected if sid == "aa000002" and "【Nexarch タスク" in t]
    assert texts and "【引き継ぎ】前の担当（主" in texts[-1]
    assert [r["kind"] for r in task["runs"]] == ["headless", "console"]


# ---------------------------------------------------------------- 並列・配り手

def hold(monkeypatch):
    """実行を始めた状態のまま止める（プロセスは起こさない）。"""
    monkeypatch.setattr(team_runner, "_spawn", lambda *a: None)


def running_ids(team_id: str) -> list[int]:
    return [t["id"] for t in get_team(team_id)["tasks"] if t["status"] == "doing"]


def test_max_parallel_limits_and_board_counts(env, monkeypatch):
    hold(monkeypatch)
    team = mk_team([H("claude"), H("codex"), H("opencode", account="default")], max_parallel=2, local_parallel=2)
    ids = [mk_task(team, k) for k in keys(team)]
    assert team_dispatch.dispatch_team(team["id"]) == 2
    assert running_ids(team["id"]) == ids[:2]
    shown = [t for t in teams.list_teams() if t["id"] == team["id"]][0]
    assert shown["parallel"] == {"running": 2, "max": 2, "waiting": 1, "local_max": 2}
    assert team_dispatch.dispatch_team(team["id"]) == 0


def test_one_task_per_member(env, monkeypatch):
    hold(monkeypatch)
    team = mk_team([H("claude"), H("codex")])
    a, b = keys(team)
    t1, t2, t3 = mk_task(team, a), mk_task(team, a), mk_task(team, b)
    team_dispatch.dispatch_team(team["id"])
    assert running_ids(team["id"]) == [t1, t3]
    assert get_task(team["id"], t2)["status"] == "todo"
    with pytest.raises(teams.TeamError):  # 手で送っても断る
        teams.dispatch_task(team["id"], t2, "t")


def test_overlapping_files_wait(env, monkeypatch):
    hold(monkeypatch)
    team = mk_team([H("claude"), H("codex"), H("opencode", account="default")], local_parallel=3)
    a, b, c = keys(team)
    t1 = mk_task(team, a, files=["src/app"])
    t2 = mk_task(team, b, files=["src/app/x.py"])      # 重なる
    t3 = mk_task(team, c, files=["docs"])               # 重ならない
    team_dispatch.dispatch_team(team["id"])
    assert running_ids(team["id"]) == [t1, t3]
    assert get_task(team["id"], t2)["status"] == "todo"


def test_local_parallel_limit(env, monkeypatch):
    hold(monkeypatch)
    team = mk_team([H("opencode"), H("opencode", label="2 つ目")], local_parallel=1)
    a, b = keys(team)
    t1, t2 = mk_task(team, a), mk_task(team, b)
    team_dispatch.dispatch_team(team["id"])
    assert running_ids(team["id"]) == [t1]
    teams.update_team(team["id"], {"local_parallel": 2})
    team_dispatch.dispatch_team(team["id"])
    assert running_ids(team["id"]) == [t1, t2]


def test_unassigned_task_goes_to_first_free_member_in_fallback_order(env, monkeypatch):
    hold(monkeypatch)
    team = mk_team([H("claude"), H("codex"), H("opencode", account="default")])
    a, b, c = keys(team)
    teams.update_team(team["id"], {"fallback": [b, a, c]})
    tid = mk_task(team)
    team_dispatch.dispatch_team(team["id"])
    task = get_task(team["id"], tid)
    assert task["assignee"] == b and task["status"] == "doing"
    t2 = mk_task(team)
    team_dispatch.dispatch_team(team["id"])
    assert get_task(team["id"], t2)["assignee"] == a  # b は作業中


def test_auto_run_off_does_not_start(env, monkeypatch):
    hold(monkeypatch)
    team = mk_team([H("claude")], auto_run=False)
    tid = mk_task(team, keys(team)[0])
    assert team_dispatch.dispatch_team(team["id"]) == 0 and team_dispatch.tick() == []
    assert get_task(team["id"], tid)["status"] == "todo"


def test_finishing_a_task_starts_the_next_waiting_one(env):
    team = mk_team([H("claude")])
    t1 = mk_task(team, keys(team)[0])
    t2 = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], t1, "t")  # 終わると配り手が t2 を始める
    assert get_task(team["id"], t1)["status"] == "review" and get_task(team["id"], t2)["status"] == "review"
    assert len(env.calls("claude")) == 2


def test_console_task_auto_dispatched_when_ready(env):
    team = mk_team([{"session_id": "aa000001", "role": "developer"}])
    tid = mk_task(team, "aa000001")
    import unittest.mock as mock
    rows = [{"id": "aa000001", "alive": True, "commander_cli": "claude", "commander_state": "waiting"}]
    with mock.patch.object(consoles, "list_consoles", return_value=rows), \
            mock.patch.object(teams.consoles, "list_consoles", return_value=rows):
        team_dispatch.tick()
    assert get_task(team["id"], tid)["status"] == "doing"
    assert any("【Nexarch タスク" in t for _s, t, _ in env.manager.injected)


def test_watch_ignores_headless_tasks(env, monkeypatch, sent):
    hold(monkeypatch)
    team = mk_team([H("claude")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    assert team_watch.tick(time.time() + 1000) == []
    assert not any("閉じた" in h["event"] for h in get_task(team["id"], tid)["history"])


# ---------------------------------------------------------------- API

def test_api_policy_run_and_stop(admin_client, env, monkeypatch):
    hold(monkeypatch)
    team = admin_client.post(API, headers=CSRF_HEADERS, json={"name": "班"}).json()
    assert team["on_limit"] == "handoff" and team["workdir"]
    r = admin_client.put(f"{API}/{team['id']}", headers=CSRF_HEADERS,
                         json={"on_limit": "wait", "max_parallel": 4, "local_parallel": 2, "auto_run": False})
    assert r.status_code == 200 and r.json()["on_limit"] == "wait"
    assert admin_client.put(f"{API}/{team['id']}", headers=CSRF_HEADERS, json={"on_limit": "zzz"}).status_code == 400
    r = admin_client.put(f"{API}/{team['id']}/members", headers=CSRF_HEADERS, json={"members": [H("claude", label="自動")]})
    assert r.status_code == 200
    member = r.json()["members"][0]
    task = admin_client.post(f"{API}/{team['id']}/tasks", headers=CSRF_HEADERS,
                             json={"title": "実装", "assignee": member["id"]}).json()
    assert admin_client.post(f"{API}/{team['id']}/tasks/{task['id']}/stop", headers=CSRF_HEADERS).status_code == 409
    run = admin_client.post(f"{API}/{team['id']}/tasks/{task['id']}/run", headers=CSRF_HEADERS)
    assert run.status_code == 200 and run.json()["run"]["status"] == "running"
    listed = [t for t in admin_client.get(API).json()["teams"] if t["id"] == team["id"]][0]
    assert listed["members"][0]["kind"] == "headless" and listed["members"][0]["key"] == member["id"]
    assert listed["tasks"][0]["assignee_state"] == "working" and listed["parallel"]["running"] == 1
    assert admin_client.post(f"{API}/{team['id']}/tasks/{task['id']}/run", headers=CSRF_HEADERS).status_code == 409
    assert admin_client.post(f"{API}/{team['id']}/tasks/{task['id']}/stop", headers=CSRF_HEADERS).json() == {"ok": True}
    assert admin_client.post(f"{API}/{team['id']}/tasks/{task['id']}/collect", headers=CSRF_HEADERS).status_code == 409


# ---------------------------------------------------------------- codex のサンドボックス・失敗した操作

REAL_CODEX_LINES = [
    b'{"type":"thread.started","thread_id":"t1"}',
    b'{"type":"item.started","item":{"id":"item_1","type":"file_change","changes":[{"path":"/x/hello.txt","kind":"add"}],"status":"in_progress"}}',
    b'{"type":"item.completed","item":{"id":"item_1","type":"file_change","changes":[{"path":"/x/hello.txt","kind":"add"}],"status":"failed"}}',
    b'{"type":"item.started","item":{"id":"item_3","type":"command_execution","command":"cat hello.txt","status":"in_progress"}}',
    b'{"type":"item.completed","item":{"id":"item_3","type":"command_execution","command":"cat hello.txt","exit_code":1,"status":"failed"}}',
    b'{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"done"}}',
    b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}',
]


def test_stream_state_codex_counts_failed_ops_and_fills_progress():
    state = team_runner.StreamState("codex")
    for line in REAL_CODEX_LINES:
        state.feed(line)
    assert list(state.tools) == ["edit: /x/hello.txt (add)", "shell: cat hello.txt"]  # 同じ item は 1 回だけ
    assert state.failed_ops == 2 and state.writes_failed == 1 and state.writes_ok == 0 and state.writes_all_failed
    assert state.final_text == "done"
    ok = team_runner.StreamState("codex")
    ok.feed(b'{"type":"item.completed","item":{"id":"a","type":"file_change","changes":[{"path":"a","kind":"add"}],"status":"completed"}}')
    assert ok.writes_ok == 1 and not ok.writes_all_failed and ok.failed_ops == 0


def test_codex_sandbox_probe_runs_once_and_remembers(monkeypatch, tmp_path):
    counter = tmp_path / "count"
    for code, expected in ((0, True), (1, False)):
        fake = tmp_path / f"bwrap{code}"
        fake.write_text(f"#!{sys.executable}\nimport sys\nopen({str(counter)!r}, 'a').write('x')\nsys.exit({code})\n")
        fake.chmod(0o755)
        team_runner._sandbox_state.clear()
        import shutil
        monkeypatch.setattr(shutil, "which", lambda name, f=fake: str(f) if name == "bwrap" else None)
        counter.write_text("")
        assert team_runner.codex_sandbox_ok() is expected and team_runner.codex_sandbox_ok() is expected
        assert counter.read_text() == "x"  # 1 回だけ
    team_runner._sandbox_state.clear()


def test_codex_without_sandbox_uses_danger_full_access_and_marks(env, monkeypatch):
    monkeypatch.setattr(team_runner, "codex_sandbox_ok", lambda: False)
    team = mk_team([H("codex", label="夜")])
    argv, _e, _c = team_runner.build_launch({"cli": "codex", "account": "default"}, "x", env.work, env.tmp / "last")
    assert argv[argv.index("-s") + 1] == "danger-full-access"
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "review" and task["run"]["sandbox"] == "none" and task["runs"][0]["sandbox"] == "none"
    assert env.calls("codex")[0]["argv"][env.calls("codex")[0]["argv"].index("-s") + 1] == "danger-full-access"
    shown = [t for t in teams.list_teams() if t["id"] == team["id"]][0]
    assert shown["members"][0]["no_sandbox"] is True and shown["codex_without_sandbox"] is True


def test_codex_without_sandbox_off_stops_with_launch_error(env, monkeypatch):
    monkeypatch.setattr(team_runner, "codex_sandbox_ok", lambda: False)
    team = mk_team([H("codex")], codex_without_sandbox=False)
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "blocked" and "サンドボックスがこの機械で使えません" in task["run"]["tail"] and not env.calls()
    with pytest.raises(teams.TeamError):
        teams.update_team(team["id"], {"codex_without_sandbox": "no"})


def test_sandbox_ok_machine_keeps_workspace_write(env):
    argv, _e, _c = team_runner.build_launch({"cli": "codex", "account": "default"}, "x", env.work, env.tmp / "last")
    assert argv[argv.index("-s") + 1] == "workspace-write"
    shown = teams.create_team("x")
    assert shown["codex_without_sandbox"] is True


def test_all_writes_failed_retries_same_member_once_then_hands_off(env, sent):
    env.mode("codex", "writefail")
    team = mk_team([H("codex", label="主"), H("claude", label="控え")])
    first, second = keys(team)
    tid = mk_task(team, first, checks=[])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert len(env.calls("codex")) == 2  # 同じ担い手で 1 回だけ再挑戦
    assert "書き込みがすべて失敗" in prompt_of(env.calls("codex")[1])
    handoff = prompt_of(env.calls("claude")[0])
    assert "【引き継ぎ】前の担当（" in handoff and "書き込みがすべて失敗しました" in handoff
    assert task["assignee"] == second and task["status"] == "review"
    assert [r["result"] for r in task["runs"]] == ["write_failed", "write_failed", "ok"]
    assert any("書き込みがすべて失敗" in h["event"] for h in task["history"])


def test_all_writes_failed_does_not_reach_review_and_report_is_unverified(env, monkeypatch):
    env.mode("codex", "writefail")
    monkeypatch.setattr(team_runner, "_spawn", lambda *a: None)
    team = mk_team([H("codex")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    monkeypatch.setattr(team_runner, "_restart", lambda *a: None)  # 再挑戦は始めさせない
    monkeypatch.setattr(team_dispatch, "kick", lambda *a: 0)
    # run を 1 回だけ実行して結果を見る（再挑戦は始めさせない）
    team_runner._worker(team["id"], tid, task["run"]["run_id"], team["members"][0], "本文", env.work)
    task = get_task(team["id"], tid)
    assert task["status"] != "review" and task["status"] in ("todo", "doing"), task
    assert teams.is_unverified({"report": task["report"]}), task
    assert task["run"].get("failed_ops") == 1, task["run"]
    assert task["run"]["failed_note"] == "失敗した操作 1 件"


def test_all_writes_failed_single_member_blocks(env, sent):
    env.mode("codex", "writefail")
    team = mk_team([H("codex")])
    tid = mk_task(team, keys(team)[0])
    teams.dispatch_task(team["id"], tid, "t")
    task = get_task(team["id"], tid)
    assert task["status"] == "blocked" and len(env.calls("codex")) == 2
    assert any("自動で直せませんでした" in m for _t, m in sent)
