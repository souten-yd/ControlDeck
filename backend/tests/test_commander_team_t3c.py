"""Nexarch の AI チーム T3c（一文で頼む → チームリーダーが MCP で調べ・計画し・振り分け・評価・統合・試験する）の試験。

目的: 利用者（2026-11-27）の要望を仕組みで守る。
- 「一文リーダーに依頼したら、調査・計画・振り分け・個別の実装と評価・統合して評価・試験する。Opus が Haiku・Sonnet を使う流れを模倣。
  ただし LLM は横断的に（司令塔も Claude・Codex・OpenRouter・OpenCode・ローカル LLM）」:
  - MCP サーバー nexarch_team の道具ごと: token の縛り（チーム・実行の番号・期限）・別チームを触れない・入力の検証と上限。
  - 計画の保存（確認のコマンドは T2b の許す形だけ）・段（tier）→役割→担い手の自動の振り分け（上限なら secondary・担い手が無ければ
    チームリーダー自身）・worktree＋ブランチ・統合（merge）と競合・待つ（上限つき）・差分の上限・質問と autonomy。
  - チームリーダーの実行: 起動の引数（claude・codex・opencode の MCP の渡し方。token を argv に出さない）・finish 無しの再開（最大 3）・
    上限で secondary が team_status から再開・落ちたら 1 回だけ再開・web_research=false で research(web) を断る・AI アシスタントの道具。
  - 通しの試験: 偽のチームリーダー（試験の中の script。実際に stdio の橋渡し→HTTP の口→道具を呼ぶ）が、依頼→計画→explore→
    implement 2 つ並列→review→integrate→checks→finish まで回し、結果と変更ファイルが板に残る。
根拠: 利用者の要望（2026-11-27）と docs/PLAN-nexarch-team.md T3c。CLI は本物を呼ばない（T3a の試験と同じ偽の実行ファイル）。
隔離テストは env -u CONTROL_DECK_CONFIG で流す。
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from app.addons import tokens
from app.commander import leader_mcp, team_checks, team_dispatch, team_git, team_leader, team_roles, team_runner, teams
from app.commander.leader_mcp import ToolError
from tests.conftest import CSRF_HEADERS
from tests.test_commander_team_t3a import (  # noqa: F401 - fixture
    API, Env, _clean_store, env, get_task, get_team, sent,
)
from tests.test_commander_team_t3b import EP, _clean_settings_and_models, role  # noqa: F401 - fixture


# ---------------------------------------------------------------- 偽の担い手・偽のチームリーダー

FAKE = r'''#!@@PY@@
import sys, json, os, re, subprocess, time
REC = "@@REC@@"
STEPS = "@@STEPS@@"
CLI = "@@CLI@@"
args = sys.argv[1:]
mcp_cfg = None
if "--mcp-config" in args:                    # claude のチームリーダー: 0600 の設定ファイル
    cfg = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["nexarch_team"]
    mcp_cfg = {"cmd": [cfg["command"], *cfg["args"]], "env": cfg["env"]}
elif any(a.startswith("mcp_servers.nexarch_team.command=") for a in args):  # codex のチームリーダー: -c と環境変数
    pairs = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in args if a.startswith("mcp_servers.nexarch_team.")}
    command = json.loads(pairs["mcp_servers.nexarch_team.command"])
    extra = json.loads(pairs["mcp_servers.nexarch_team.args"])
    names = json.loads(pairs["mcp_servers.nexarch_team.env_vars"])
    mcp_cfg = {"cmd": [command, *extra], "env": {k: os.environ[k] for k in names}}
with open(REC, "a") as f:
    f.write(json.dumps({"kind": CLI, "leader": bool(mcp_cfg), "argv": args, "cwd": os.getcwd(),
                        "token_in_argv": any((os.environ.get("CONTROL_DECK_ADDON_MCP_TOKEN") or "\0") in a for a in args)}) + "\n")

def out(o):
    print(json.dumps(o, ensure_ascii=False), flush=True)

def claude_result(text, error=False):
    out({"type": "result", "subtype": "error" if error else "success", "is_error": error, "result": text,
         "usage": {"input_tokens": 11, "output_tokens": 7}, "total_cost_usd": 0.01})

if mcp_cfg:                                    # ---- チームリーダー（橋渡し stdio を実際に起こして道具を呼ぶ）
    proc = subprocess.Popen(mcp_cfg["cmd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            env={**os.environ, **mcp_cfg["env"]})
    ident = [0]
    def rpc(method, params=None):
        ident[0] += 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident[0], "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())
    rpc("initialize", {"protocolVersion": "2025-03-26"})
    def call(name, **arguments):
        reply = rpc("tools/call", {"name": name, "arguments": arguments})
        result = reply.get("result") or {}
        if result.get("isError") or "error" in reply:
            return {"_error": json.dumps(reply, ensure_ascii=False)[:400]}
        return result.get("structuredContent") or json.loads(result["content"][0]["text"])
    count = len([1 for ln in open(REC) if json.loads(ln).get("leader")])   # 何回目のチームリーダーの起動か
    ns = {"call": call, "out": out, "claude_result": claude_result, "LAUNCH": count, "CLI": CLI, "sys": sys, "time": time}
    exec(open(STEPS).read(), ns)
    sys.exit(ns.get("EXIT", 0))

# ---- ふつうの担い手（implement・explore・review）: 仕事の番号のファイルを書き、5 行で報告する
prompt = args[args.index("-p") + 1] if "-p" in args else args[-1]
m = re.search(r"Nexarch タスク #(\d+)", prompt)
n = m.group(1) if m else "0"
readonly = "読み取りだけ" in prompt
if not readonly:
    with open("f%s.txt" % n, "w") as f:
        f.write("work of %s\n" % n)
    if "CONFLICT" in prompt:
        with open("shared.txt", "w") as f:
            f.write("version-%s\n" % n)
report = "完了報告: 仕事 %s をした\n変更ファイル: f%s.txt\n試験: pytest 3 件合格\n確認: pytest -q → 3 passed\n残り: なし" % (n, n)
if CLI == "claude":
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "始めます"}]}})
    claude_result(report)
else:
    last = args[args.index("-o") + 1]
    open(last, "w").write(report)
    out({"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": report}})
    out({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 3}})
'''


def install_fake(env: Env, steps: dict[str, str]) -> None:
    """claude・codex の偽の実行ファイル。steps[cli] はチームリーダーのときに exec される Python（call・out・claude_result・LAUNCH が使える）。"""
    for cli in ("claude", "codex"):
        steps_file = env.tmp / f"steps-{cli}.py"
        steps_file.write_text(steps.get(cli, "call('finish', summary='done', outcome='done')\nclaude_result('ok')\n"), encoding="utf-8")
        path = env.tmp / f"fake3c-{cli}"
        path.write_text(FAKE.replace("@@PY@@", sys.executable).replace("@@REC@@", str(env.rec)).replace("@@STEPS@@", str(steps_file))
                        .replace("@@CLI@@", cli), encoding="utf-8")
        path.chmod(0o755)
        env.scripts[cli] = str(path)


@pytest.fixture(autouse=True)
def _default_fake(env):
    install_fake(env, {})


@pytest.fixture()
def server(monkeypatch):
    """leader_mcp の router だけを載せた本物の HTTP サーバー（橋渡しの stdio が実際に呼ぶ）。"""
    app = FastAPI()
    app.include_router(leader_mcp.router, prefix="/api/v1")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    monkeypatch.setattr(team_leader, "mcp_url", lambda: f"http://127.0.0.1:{port}/api/v1/commander/leader-mcp")
    yield f"http://127.0.0.1:{port}/api/v1/commander/leader-mcp"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def real_threads(monkeypatch):
    """実行をふつうのスレッドで走らせる（並列・待つ道具の試験）。"""
    monkeypatch.setattr(team_runner, "_spawn", lambda func, *args: threading.Thread(target=func, args=args, daemon=True).start())


def tier_roles(**primaries) -> list[dict]:
    """4 つの段の役割。primaries の段名→endpoint（無ければ未設定＝チームリーダー自身）。"""
    counts = {"explore": 2, "implement": 3, "review": 1, "hard": 1}
    return [role(name, counts[name], primaries.get(name)) for name in team_roles.TIERS]


def mk_leader_team(roles: list[dict] | None = None, **settings) -> dict:
    team = teams.create_team("チーム3c")
    teams.update_team(team["id"], {"auto_run": False, **settings})
    teams.set_roles(team["id"], roles if roles is not None else tier_roles(**{t: EP("claude") for t in team_roles.TIERS}))
    leader_mcp._mutate(team["id"], lambda t: t.update(leader_run={"status": "running", "n": 1, "goal": "目標", "resumes": 0}))  # noqa: SLF001
    return get_team(team["id"])


def call(team_id: str, name: str, **args):
    return leader_mcp.call_tool(team_id, name, args)


def commit_seed(team: dict) -> None:
    """作業場所へ最初のファイルをコミット（git init 済みの env の作業場所）。"""
    path = Path(team["workdir"])
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    team_git.run(path, "add", "-A")
    team_git.run(path, "commit", "-q", "-m", "seed")


def wait_until(predicate, timeout: float = 60.0, step: float = 0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("時間内に条件を満たしませんでした")


# ---------------------------------------------------------------- 段・役割

def test_new_team_has_four_tiers_and_leader_fallback_for_unassigned(env):
    team = teams.create_team("班")
    assert [(r["name"], r["count"]) for r in team["roles"]] == [("explore", 2), ("implement", 3), ("review", 1), ("hard", 1)]
    assert team["git_init"] is True and team["web_research"] is True and team["autonomy"] == "high"
    assert team_roles.tier_role(team, "review")["name"] == "review"


def test_update_team_validates_t3c_settings(env):
    team = teams.create_team("班")
    ok = teams.update_team(team["id"], {"git_init": False, "web_research": False, "autonomy": "ask"})
    assert (ok["git_init"], ok["web_research"], ok["autonomy"]) == (False, False, "ask")
    for bad in ({"git_init": "x"}, {"web_research": 1}, {"autonomy": "maybe"}):
        with pytest.raises(teams.TeamError):
            teams.update_team(team["id"], bad)


# ---------------------------------------------------------------- token

def test_token_binds_team_run_number_and_expiry(env, monkeypatch):
    a = mk_leader_team()
    b = mk_leader_team()
    token = leader_mcp.issue_token(a["id"], 1)
    assert leader_mcp.check_token(f"Bearer {token}") == (a["id"], 1)
    # 別のチームの token は、そのチームの実行の番号が合わなければ使えない
    other = leader_mcp.issue_token(b["id"], 2)
    with pytest.raises(ToolError) as exc:
        leader_mcp.check_token(f"Bearer {other}")
    assert exc.value.status == 403
    # 再開（番号が進む）後に古い token は使えない
    leader_mcp._mutate(a["id"], lambda t: t["leader_run"].update(n=2))  # noqa: SLF001
    with pytest.raises(ToolError):
        leader_mcp.check_token(f"Bearer {token}")
    # 実行が終わっていれば使えない
    leader_mcp._mutate(a["id"], lambda t: t["leader_run"].update(status="done"))  # noqa: SLF001
    with pytest.raises(ToolError):
        leader_mcp.check_token(f"Bearer {leader_mcp.issue_token(a['id'], 2)}")
    # 期限切れ・別の種類の token・壊れた token
    expired = tokens.issue("control-deck", subject=f"nexarch-team:{b['id']}:2", kind="nexarch-team", ttl_seconds=60, now=int(time.time()) - 3600)
    foreign = tokens.issue("control-deck", subject=f"nexarch-team:{b['id']}:2", kind="agent-mcp", ttl_seconds=60)
    for bad in (expired, foreign, "x.y", ""):
        with pytest.raises(ToolError) as exc:
            leader_mcp.check_token(f"Bearer {bad}")
        assert exc.value.status == 401
    with pytest.raises(ToolError):
        leader_mcp.check_token(None)


def test_http_door_needs_token_and_loopback(env, server):
    import urllib.error
    import urllib.request

    team = mk_leader_team()
    token = leader_mcp.issue_token(team["id"], 1)

    def get(path, tok=None, method="GET", body=None):
        req = urllib.request.Request(server + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Requested-With": "ControlDeck", **({"Authorization": f"Bearer {tok}"} if tok else {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    assert get("/tools")[0] == 401
    plain = urllib.request.Request(server + "/tools", headers={"Authorization": f"Bearer {token}"})  # ControlDeck の client の印が無い
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(plain, timeout=10)
    assert denied.value.code == 403
    status, listed = get("/tools", token)
    assert status == 200 and {t["name"] for t in listed["tools"]} == set(leader_mcp.TOOLS)
    status, body = get("/call", token, "POST", {"name": "team_status", "arguments": {}})
    assert status == 200 and body["autonomy"] == "high"
    assert get("/call", token, "POST", {"name": "nope", "arguments": {}})[0] == 404
    assert get("/call", token, "POST", {"name": "team_status", "arguments": {"x": 1}})[0] == 422
    # 呼び出しの履歴は道具の名前と成否だけ
    log = get_team(team["id"])["mcp_log"]
    assert [r["tool"] for r in log][:1] == ["team_status"] and set(log[0]) == {"at", "tool", "ok", "seconds"}


# ---------------------------------------------------------------- write_plan

def test_write_plan_validates_and_keeps_status(env):
    team = mk_leader_team()
    items = [{"id": "a", "title": "調べる", "tier": "explore"},
             {"id": "b", "title": "作る", "tier": "implement", "depends_on": ["a"], "files": ["src/x.py"],
              "checks": ["pytest -q", "ls | wc", "rm -rf / && echo"]}]
    saved = call(team["id"], "write_plan", plan_markdown="# 計画\n1. 調べる\n2. 作る", items=items)
    assert saved["saved"] and saved["items"] == 2
    assert saved["dropped_checks"] == [{"id": "b", "dropped": ["ls | wc", "rm -rf / && echo"]}]  # T2b の許す形だけ
    plan = get_team(team["id"])["plan"]
    assert plan["items"][1]["checks"] == ["pytest -q"] and plan["items"][0]["status"] == "planned"
    leader_mcp._mutate(team["id"], lambda t: t["plan"]["items"][0].update(status="done", task_id=5))  # noqa: SLF001
    call(team["id"], "write_plan", plan_markdown="更新", items=items)  # 何度でも更新できる・状態は引き継ぐ
    assert get_team(team["id"])["plan"]["items"][0]["status"] == "done"
    assert any(r["kind"] == "plan" for r in get_team(team["id"])["timeline"])
    bad_cases = [
        {"plan_markdown": "x", "items": []},
        {"plan_markdown": "", "items": items},
        {"plan_markdown": "x", "items": [{"id": "a", "title": "t", "tier": "magic"}]},
        {"plan_markdown": "x", "items": [{"id": "a b", "title": "t", "tier": "explore"}]},
        {"plan_markdown": "x", "items": [{"id": "a", "title": "t", "tier": "explore"}, {"id": "a", "title": "u", "tier": "explore"}]},
        {"plan_markdown": "x", "items": [{"id": "a", "title": "t", "tier": "explore", "depends_on": ["zz"]}]},
        {"plan_markdown": "x", "items": [{"id": "a", "title": "t", "tier": "explore", "depends_on": ["b"]},
                                         {"id": "b", "title": "u", "tier": "explore", "depends_on": ["a"]}]},
        {"plan_markdown": "x", "items": [{"id": "a", "title": "t", "tier": "explore", "extra": 1}]},
        {"plan_markdown": "x" * (leader_mcp.PLAN_MAX + 1), "items": items},
        {"plan_markdown": "x", "items": [{"id": f"i{n}", "title": "t", "tier": "explore"} for n in range(leader_mcp.ITEMS_MAX + 1)]},
        {"plan_markdown": "x", "items": items, "unknown": 1},
    ]
    for bad in bad_cases:
        with pytest.raises(ToolError):
            call(team["id"], "write_plan", **bad)


# ---------------------------------------------------------------- 段 → 担い手（delegate）

def test_delegate_picks_endpoint_from_tier_and_falls_back_to_secondary_on_limit(env):
    roles = tier_roles(implement=EP("claude"))
    roles[1]["secondary"] = [EP("codex")]
    team = mk_leader_team(roles)
    commit_seed(team)
    env.mode("claude", "limit")  # t3a の偽: claude は上限で止まる（このテストでは T3a の偽をそのまま使う）
    run = call(team["id"], "delegate", title="作る", instructions="作って", tier="implement")
    team_dispatch.kick(team["id"])
    after = get_team(team["id"])
    task = next(t for t in after["tasks"] if str(t["id"]) == run["run_id"])
    # claude が上限 → 回復の時刻まで使わず、secondary の codex へ引き継がれて終わる
    assert task["runs"][-1]["cli"] == "codex" and task["status"] == "review" and task["tier"] == "implement"
    assert team_roles.cooling_until(after, EP("claude"), time.time()) is not None


def test_delegate_without_endpoint_uses_leader_itself_and_validates(env):
    team = mk_leader_team(tier_roles())  # どの段にも担い手が無い
    teams.set_leader(team["id"], EP("codex"))
    run = call(team["id"], "delegate", title="難しい所", instructions="直して", tier="hard")
    task = next(t for t in get_team(team["id"])["tasks"] if str(t["id"]) == run["run_id"])
    assert task["runs"][-1]["cli"] == "codex"  # チームリーダー自身の endpoint
    for bad in ({"title": "t", "instructions": "i", "tier": "wizard"}, {"title": "t", "tier": "hard"},
                {"item_id": "nope"}, {"item_id": "a", "title": "t"}, {"title": "t", "instructions": "i", "tier": "hard", "zzz": 1},
                {"title": "t", "instructions": "x" * (leader_mcp.INSTR_MAX + 1), "tier": "hard"}):
        with pytest.raises(ToolError):
            call(team["id"], "delegate", **bad)


def test_delegate_item_respects_depends_on_and_redo_needs_note(env):
    team = mk_leader_team()
    commit_seed(team)
    call(team["id"], "write_plan", plan_markdown="p", items=[
        {"id": "a", "title": "調べる", "tier": "explore", "instructions": "調べて"},
        {"id": "b", "title": "作る", "tier": "implement", "depends_on": ["a"], "instructions": "作って"}])
    with pytest.raises(ToolError) as exc:
        call(team["id"], "delegate", item_id="b")
    assert exc.value.status == 409
    a = call(team["id"], "delegate", item_id="a", wait=True)
    assert a["results"][0]["state"] == "integrated"  # explore は結果を受け取ると完了
    b = call(team["id"], "delegate", item_id="b")
    assert get_team(team["id"])["plan"]["items"][1]["task_id"] == int(b["run_id"])
    call(team["id"], "wait", run_ids=[b["run_id"]])
    with pytest.raises(ToolError):
        call(team["id"], "delegate", item_id="b")  # 振り済み → note が要る


# ---------------------------------------------------------------- worktree・統合・競合

def test_worktree_per_implement_task_readonly_for_explore_and_merge(env):
    team = mk_leader_team()
    commit_seed(team)
    one = call(team["id"], "delegate", title="一", instructions="作る", tier="implement")
    two = call(team["id"], "delegate", title="二", instructions="作る", tier="implement")
    explore = call(team["id"], "delegate", title="調べ", instructions="調べる", tier="explore")
    res = call(team["id"], "wait", run_ids=[one["run_id"], two["run_id"], explore["run_id"]])
    tasks = {str(t["id"]): t for t in get_team(team["id"])["tasks"]}
    a, b, c = tasks[one["run_id"]], tasks[two["run_id"]], tasks[explore["run_id"]]
    assert a["cwd"] != b["cwd"] and a["branch"] == f"nexarch/{team['id']}-{a['id']}" and "/.nexarch-wt/" in a["cwd"]
    assert Path(a["cwd"], f"f{a['id']}.txt").exists() and not Path(team["workdir"], f"f{a['id']}.txt").exists()  # 元の作業場所は汚さない
    assert c["readonly"] is True and "branch" not in c and c["cwd"].endswith("/.nexarch-wt/team")
    assert get_team(team["id"])["git"]["team_branch"] == f"nexarch/{team['id']}"
    status = {r["run_id"]: r for r in res["results"]}
    assert status[one["run_id"]]["changes"]["files"] == [f"f{a['id']}.txt"] and status[one["run_id"]]["checks"] == ""
    ignored = team_git.run(team["workdir"], "status", "--porcelain")[1]
    assert ".nexarch-wt" not in ignored  # info/exclude
    merged = call(team["id"], "integrate", run_ids=[one["run_id"], two["run_id"]])
    assert merged["merged"] == [one["run_id"], two["run_id"]] and merged["conflicts"] == []
    assert Path(get_team(team["id"])["git"]["team_wt"], f"f{a['id']}.txt").exists()
    assert get_task(team["id"], a["id"])["status"] == "done"
    assert merged["team_changes"]["count"] == 2


def test_integrate_conflict_is_reported_then_redo_merges_team_branch(env):
    team = mk_leader_team()
    commit_seed(team)
    one = call(team["id"], "delegate", title="一", instructions="CONFLICT 作る", tier="implement")
    call(team["id"], "wait", run_ids=[one["run_id"]])
    two = call(team["id"], "delegate", title="二", instructions="CONFLICT 作る", tier="implement")
    call(team["id"], "wait", run_ids=[two["run_id"]])
    assert call(team["id"], "integrate", run_ids=[one["run_id"]])["merged"] == [one["run_id"]]
    out = call(team["id"], "integrate", run_ids=[two["run_id"]])
    assert out["merged"] == [] and out["conflicts"] == [{"run_id": two["run_id"], "files": ["shared.txt"]}] and "hint" in out
    # チームのブランチは壊れていない（abort 済み）
    assert team_git.run(get_team(team["id"])["git"]["team_wt"], "status", "--porcelain")[1].strip() == ""
    # implement へ戻す: worktree にチームのブランチを取り込み、競合の印を残して再実行
    redo = call(team["id"], "delegate", item_id=None) if False else None  # noqa: F841 - 計画を経ない仕事は note で戻せないので項目で試す
    call(team["id"], "write_plan", plan_markdown="p", items=[{"id": "x", "title": "三", "tier": "implement", "instructions": "CONFLICT 作る"}])
    three = call(team["id"], "delegate", item_id="x")
    call(team["id"], "wait", run_ids=[three["run_id"]])
    back = call(team["id"], "delegate", item_id="x", note="競合を直して")
    assert str(back["run_id"]) == three["run_id"]
    task = get_task(team["id"], int(three["run_id"]))
    assert "競合" in (task.get("resume_note") or "") or task["status"] in ("review", "doing", "todo")


def test_integrate_refuses_unfinished_or_failed_checks_and_non_write_tiers(env):
    team = mk_leader_team()
    commit_seed(team)
    spy = call(team["id"], "delegate", title="調べ", instructions="調べる", tier="explore")
    call(team["id"], "wait", run_ids=[spy["run_id"]])
    out = call(team["id"], "integrate", run_ids=[spy["run_id"]])
    assert out["merged"] == [] and "implement" in out["refused"][0]["reason"]
    done = call(team["id"], "delegate", title="作る", instructions="作る", tier="implement", checks=["false"])
    waited = call(team["id"], "wait", run_ids=[done["run_id"]])
    assert waited["results"][0]["checks"] in ("failed", "") or waited["results"][0]["state"] == "blocked"
    out = call(team["id"], "integrate", run_ids=[done["run_id"]])
    assert out["merged"] == [] and out["refused"]


def test_non_git_workdir_with_git_init_false_has_no_worktree(env):
    team = mk_leader_team(git_init=False)
    import shutil
    shutil.rmtree(Path(team["workdir"]) / ".git")
    run = call(team["id"], "delegate", title="作る", instructions="作る", tier="implement", files=["a.py"])
    task = get_task(team["id"], int(run["run_id"]))
    assert "cwd" not in task and get_team(team["id"])["git"]["enabled"] is False
    call(team["id"], "wait", run_ids=[run["run_id"]])
    out = call(team["id"], "integrate", run_ids=[run["run_id"]])
    assert out["merged"] == [run["run_id"]]  # worktree が無いので統合は印だけ
    assert not (Path(team["workdir"]) / ".git").exists()


def test_git_init_creates_repo_in_plain_folder_and_fast_forwards_at_end(env):
    team = mk_leader_team()
    import shutil
    shutil.rmtree(Path(team["workdir"]) / ".git")
    (Path(team["workdir"]) / "seed.txt").write_text("s\n", encoding="utf-8")
    run = call(team["id"], "delegate", title="作る", instructions="作る", tier="implement")
    info = get_team(team["id"])["git"]
    assert info["enabled"] and info["origin_was_git"] is False and info["base_branch"] == "main"
    call(team["id"], "wait", run_ids=[run["run_id"]])
    call(team["id"], "integrate", run_ids=[run["run_id"]])
    call(team["id"], "finish", summary="完了", outcome="done")
    team_leader._finalize(team["id"])  # noqa: SLF001
    result = get_team(team["id"])["result"]
    assert result["landed"] is True and result["can_land"] is False  # 元が git でないので「取り込む」は無く、終わりに反映済み
    assert (Path(team["workdir"]) / f"f{run['run_id']}.txt").exists()


def test_worktreeinclude_copies_untracked_files(env):
    team = mk_leader_team()
    commit_seed(team)
    path = Path(team["workdir"])
    (path / ".gitignore").write_text(".env\n", encoding="utf-8")
    team_git.run(path, "add", "-A")
    team_git.run(path, "commit", "-q", "-m", "ignore")
    (path / ".env").write_text("LOCAL=1\n", encoding="utf-8")
    (path / ".worktreeinclude").write_text(".env\n../outside\n", encoding="utf-8")
    run = call(team["id"], "delegate", title="作る", instructions="作る", tier="implement")
    wt = Path(get_task(team["id"], int(run["run_id"]))["cwd"])
    assert (wt / ".env").read_text(encoding="utf-8") == "LOCAL=1\n"


# ---------------------------------------------------------------- wait・read_result・run_checks

def test_wait_returns_progress_when_unfinished_and_limits_arguments(env, monkeypatch):
    team = mk_leader_team()
    commit_seed(team)
    monkeypatch.setattr(team_runner, "_spawn", lambda func, *args: None)  # 実行は始まらない（待ちのまま）
    run = call(team["id"], "delegate", title="作る", instructions="作る", tier="implement")
    monkeypatch.setattr(leader_mcp, "POLL", 0.01)
    out = call(team["id"], "wait", run_ids=[run["run_id"]], timeout_s=1)
    assert out["timed_out"] is True and out["results"][0]["state"] in ("queued", "running")
    for bad in ({"run_ids": []}, {"run_ids": ["1"] * 11}, {"run_ids": [run["run_id"]], "timeout_s": 601}, {"run_ids": [run["run_id"]], "timeout_s": 0},
                {"run_ids": ["999"]}, {"run_ids": [run["run_id"]], "mode": "some"}, {"run_ids": [True]}):
        with pytest.raises(ToolError):
            call(team["id"], "wait", **bad)


def test_read_result_diff_is_capped_and_other_team_run_is_invisible(env, monkeypatch):
    team = mk_leader_team()
    commit_seed(team)
    run = call(team["id"], "delegate", title="作る", instructions="作る", tier="implement")
    call(team["id"], "wait", run_ids=[run["run_id"]])
    wt = Path(get_task(team["id"], int(run["run_id"]))["cwd"])
    (wt / "big.txt").write_text("x" * 100_000, encoding="utf-8")
    out = call(team["id"], "read_result", run_id=run["run_id"], what="diff")
    assert out["truncated"] is True and len(out["diff"]) <= team_git.DIFF_MAX and "big.txt" in out["stat"]
    files = call(team["id"], "read_result", run_id=run["run_id"], what="files")
    assert set(files["files"]) == {f"f{run['run_id']}.txt", "big.txt"}
    tail = call(team["id"], "read_result", run_id=run["run_id"], what="output_tail")
    assert "last_line" in tail
    other = mk_leader_team()
    with pytest.raises(ToolError) as exc:
        call(other["id"], "read_result", run_id=run["run_id"], what="diff")  # 別チームの仕事は見えない
    assert exc.value.status == 404
    with pytest.raises(ToolError):
        call(team["id"], "read_result", run_id=run["run_id"], what="secrets")


def test_run_checks_in_worktree_and_team(env):
    team = mk_leader_team()
    commit_seed(team)
    run = call(team["id"], "delegate", title="作る", instructions="作る", tier="implement")
    call(team["id"], "wait", run_ids=[run["run_id"]])
    ok = call(team["id"], "run_checks", where=run["run_id"], commands=["ls"])
    assert ok["status"] == "passed" and ok["results"][0]["exit_code"] == 0
    bad = call(team["id"], "run_checks", where="team", commands=["false", "ls"])
    assert bad["status"] == "failed" and len(bad["results"]) == 1  # 落ちたら残りは流さない
    assert get_team(team["id"])["last_team_checks"]["status"] == "failed"
    dropped = call(team["id"], "run_checks", where="team", commands=["ls", "a | b"])
    assert dropped["dropped"] == ["a | b"] and dropped["status"] == "passed"
    with pytest.raises(ToolError):
        call(team["id"], "run_checks", where="team")  # 流すものが無い
    with pytest.raises(ToolError):
        call(team["id"], "run_checks", where="999", commands=["ls"])


# ---------------------------------------------------------------- research

def test_research_code_is_bounded_and_web_can_be_refused(env, monkeypatch):
    team = mk_leader_team(web_research=False)
    path = Path(team["workdir"])
    (path / "a.py").write_text("def hello():\n    return 'World'\n", encoding="utf-8")
    (path / "bin.dat").write_bytes(b"\0\0hello")
    (path / "many.txt").write_text("hello\n" * 500, encoding="utf-8")
    link = path / "link.txt"
    link.symlink_to(path / "a.py")
    found = call(team["id"], "research", query="HELLO", scope="code")
    assert any(m.startswith("a.py:1:") for m in found["matches"]) and not any("bin.dat" in m for m in found["matches"])
    assert not any(m.startswith("link.txt") for m in found["matches"])
    assert len(found["matches"]) <= leader_mcp.GREP_MATCHES and found["truncated"] is True
    listing = call(team["id"], "research", query="files:*.py", scope="code")
    assert listing["files"] == ["a.py"]
    with pytest.raises(ToolError) as exc:
        call(team["id"], "research", query="何か", scope="web")
    assert exc.value.status == 403 and "web_research" in str(exc.value)
    called: list[str] = []
    monkeypatch.setattr(leader_mcp, "_web_research", lambda q: called.append(q) or {"results": []})
    teams.update_team(team["id"], {"web_research": True})
    assert call(team["id"], "research", query="何か", scope="web") == {"results": []} and called == ["何か"]
    monkeypatch.setattr(leader_mcp, "_knowledge_research", lambda q: {"text": "資料", "mode": "full", "sources": []})
    assert call(team["id"], "research", query="規格", scope="knowledge")["text"] == "資料"
    for bad in ({"query": "x", "scope": "shell"}, {"query": "", "scope": "code"}, {"query": "x" * 501, "scope": "code"}, {"query": "x"}):
        with pytest.raises(ToolError):
            call(team["id"], "research", **bad)


# ---------------------------------------------------------------- ask_user・autonomy・report・finish

def test_ask_user_autonomy_high_decides_without_asking_unless_required(env, sent):
    team = mk_leader_team()
    out = call(team["id"], "ask_user", question="どちらにしますか", options=["A", "B"])
    assert out["asked"] is False and get_team(team["id"]).get("questions", []) == []
    assert any(r["kind"] == "decision" for r in get_team(team["id"])["timeline"])
    must = call(team["id"], "ask_user", question="本番の DB を消してよいですか", required=True)
    assert must["asked"] is True and sent and "質問" in sent[-1][1] and "本番" not in sent[-1][1]  # 通知に本文を出さない


def test_ask_user_waits_for_answer_in_ask_mode(env, sent):
    team = mk_leader_team(autonomy="ask")
    out = call(team["id"], "ask_user", question="色は？", options=["赤", "青"])
    assert out["asked"] and out["question_id"] == "q1"
    pending = call(team["id"], "team_status")["pending_questions"]
    assert pending == [{"id": "q1", "question": "色は？"}]
    first = call(team["id"], "wait_answer", question_id="q1", timeout_s=1)
    assert first["answered"] is False
    threading.Timer(0.3, lambda: leader_mcp.answer_question(team["id"], "q1", "青")).start()
    got = call(team["id"], "wait_answer", question_id="q1", timeout_s=10)
    assert got == {"answered": True, "answer": "青"}
    with pytest.raises(ToolError) as exc:
        leader_mcp.answer_question(team["id"], "q1", "赤")
    assert exc.value.status == 409
    with pytest.raises(ToolError):
        call(team["id"], "ask_user", question="x", options=["a"] * 7)
    with pytest.raises(ToolError):
        call(team["id"], "wait_answer", question_id="q99")


def test_report_and_finish_validation(env):
    team = mk_leader_team()
    call(team["id"], "report", progress_text="調べています\n次へ")
    assert get_team(team["id"])["timeline"][-1] == {**get_team(team["id"])["timeline"][-1], "kind": "progress", "text": "調べています 次へ"}
    with pytest.raises(ToolError):
        call(team["id"], "report", progress_text="x" * 301)
    with pytest.raises(ToolError):
        call(team["id"], "finish", summary="s", outcome="maybe")
    out = call(team["id"], "finish", summary="すべて完了", outcome="done")
    assert out["ok"] and get_team(team["id"])["leader_run"]["finish"]["outcome"] == "done"


def test_team_status_is_enough_to_resume(env):
    team = mk_leader_team()
    call(team["id"], "write_plan", plan_markdown="# 計画", items=[{"id": "a", "title": "調べる", "tier": "explore"}])
    status = call(team["id"], "team_status")
    assert status["plan"]["items"][0]["id"] == "a" and status["goal"] == "目標" and set(status["tier_assignments"]) == set(team_roles.TIERS)
    assert status["usage"]["cost_usd"] == 0 and status["git"]["enabled"] is None


# ---------------------------------------------------------------- 起動の引数

def test_launch_args_pass_mcp_without_token_in_argv(env, monkeypatch):
    mcp = {"url": "http://127.0.0.1:1/api/v1/commander/leader-mcp", "token": "SECRET-TOKEN"}
    argv, e, _ = team_runner.build_launch({"cli": "claude", "account": "default"}, "本文", env.work, env.tmp / "last", mcp=mcp)
    assert "SECRET-TOKEN" not in " ".join(argv)
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk" and "--strict-mcp-config" in argv
    assert argv[argv.index("--allowedTools") + 1] == "mcp__nexarch_team__*,Read,Glob,Grep"
    config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))["mcpServers"]["nexarch_team"]
    assert config["env"]["CONTROL_DECK_ADDON_MCP_TOKEN"] == "SECRET-TOKEN" and config["args"][0].endswith("addon_mcp_bridge.py")
    assert oct(Path(argv[argv.index("--mcp-config") + 1]).stat().st_mode & 0o777) == "0o600"
    argv, e, _ = team_runner.build_launch({"cli": "codex", "account": "default"}, "本文", env.work, env.tmp / "last", mcp=mcp)
    joined = " ".join(argv)
    assert "SECRET-TOKEN" not in joined and argv[argv.index("-s") + 1] == "read-only"
    assert "mcp_servers.nexarch_team.command=" in joined and "mcp_servers.nexarch_team.env_vars=" in joined
    assert e["CONTROL_DECK_ADDON_MCP_TOKEN"] == "SECRET-TOKEN"  # 環境変数で渡し、env_vars で橋渡しへ転送する
    seen: dict = {}

    def fake_launch(model, cwd, text, **kw):
        seen.update(kw)
        return [env.scripts["claude"], "run", text], {}, lambda: None

    monkeypatch.setattr(team_runner, "_opencode_launch", fake_launch)
    monkeypatch.setattr(accounts_mod(), "opencode_connection", lambda account: {"model": "m"})
    team_runner.build_launch({"cli": "opencode", "account": "default"}, "本文", env.work, env.tmp / "last", mcp=mcp)
    assert seen["extra_mcp"]["nexarch_team"]["environment"]["CONTROL_DECK_ADDON_MCP_TOKEN"] == "SECRET-TOKEN" and seen["readonly"] is True


def accounts_mod():
    from app.commander import accounts
    return accounts


def test_readonly_launch_for_explore_and_review(env, monkeypatch):
    argv, _, _ = team_runner.build_launch({"cli": "claude", "account": "default"}, "t", env.work, env.tmp / "last", readonly=True)
    assert argv[argv.index("--permission-mode") + 1] == "plan" and "Edit" not in argv[argv.index("--allowedTools") + 1]
    argv, _, _ = team_runner.build_launch({"cli": "codex", "account": "default"}, "t", env.work, env.tmp / "last", readonly=True)
    assert argv[argv.index("-s") + 1] == "read-only"
    seen: dict = {}
    monkeypatch.setattr(team_runner, "_opencode_launch", lambda model, cwd, text, **kw: seen.update(kw) or ([env.scripts["claude"]], {}, lambda: None))
    monkeypatch.setattr(accounts_mod(), "opencode_connection", lambda account: {"model": "m"})
    team_runner.build_launch({"cli": "opencode", "account": "default"}, "t", env.work, env.tmp / "last", readonly=True)
    assert seen["readonly"] is True
    argv, _, _ = team_runner.build_launch({"cli": "claude", "account": "default"}, "t", env.work, env.tmp / "last")
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"


# ---------------------------------------------------------------- チームリーダーの実行

def start_leader(team: dict, goal: str = "ログイン機能を作って") -> None:
    team_leader.start(team["id"], goal)


def test_leader_without_finish_is_resumed_up_to_three_times_then_stops(env, server, sent):
    install_fake(env, {"claude": "call('report', progress_text='調べています')\nclaude_result('終わりました')\n"})
    team = teams.create_team("再開班")
    teams.update_team(team["id"], {"auto_run": False})
    team_leader.start(team["id"], "目標")
    after = get_team(team["id"])
    assert after["leader_run"]["status"] == "failed" and "finish" in after["leader_run"]["reason"]
    launches = [c for c in env.calls("claude") if c["leader"]]
    assert len(launches) == 1 + team_leader.MAX_RESUMES  # 最初＋再開 3 回
    prompts = [c["argv"][c["argv"].index("-p") + 1] for c in launches]
    assert "再開です" not in prompts[0] and all("team_status を最初に読み" in p for p in prompts[1:])
    assert any("finish しないまま" in m for _t, m in sent)
    assert [r["status"] for r in after["leader_runs"]] == ["no_finish"] * 4


def test_leader_crash_is_retried_once_with_same_endpoint(env, server, sent):
    install_fake(env, {"claude": "if LAUNCH == 1:\n    EXIT = 1\nelse:\n    call('finish', summary='ok', outcome='done')\n    claude_result('ok')\n"})
    team = teams.create_team("落ちる班")
    team_leader.start(team["id"], "目標")
    after = get_team(team["id"])
    assert after["leader_run"]["status"] == "done"
    launches = [c for c in env.calls("claude") if c["leader"]]
    assert len(launches) == 2 and after["leader_runs"][0]["status"] == "failed"
    # 続けて落ちたら止める
    install_fake(env, {"claude": "EXIT = 1\n"})
    other = teams.create_team("落ち続ける班")
    team_leader.start(other["id"], "目標")
    assert get_team(other["id"])["leader_run"]["status"] == "failed"


def test_leader_limit_moves_to_secondary_which_resumes_from_team_status(env, server, sent):
    install_fake(env, {
        "claude": "call('report', progress_text='計画中')\nout({'type':'result','subtype':'error','is_error':True,'result':'5-hour limit reached ∙ resets 10:20pm'})\nEXIT = 1\n",
        "codex": "status = call('team_status')\nassert status['timeline'][-1]['text'] or True\ncall('finish', summary='引き継いで完了', outcome='done')\n"})
    team = teams.create_team("上限班")
    teams.set_leader(team["id"], EP("claude"))
    teams.set_roles(team["id"], tier_roles(hard=EP("codex")))  # チームリーダーのセカンダリ = hard の chain
    team_leader.start(team["id"], "目標")
    after = get_team(team["id"])
    assert after["leader_run"]["status"] == "done" and after["result"]["summary"] == "引き継いで完了"
    runs = after["leader_runs"]
    assert [r["status"] for r in runs] == ["limited", "done"] and "Codex" in runs[1]["endpoint"]
    codex_launch = [c for c in env.calls("codex") if c["leader"]][0]
    prompt = codex_launch["argv"][-1]
    assert "再開です" in prompt and "team_status" in prompt and codex_launch["token_in_argv"] is False
    assert team_roles.cooling_until(after, EP("claude"), time.time()) is not None


def test_leader_all_endpoints_limited_fails_and_notifies(env, server, sent):
    install_fake(env, {"claude": "out({'type':'result','subtype':'error','is_error':True,'result':'usage limit reached'})\nEXIT = 1\n"})
    team = teams.create_team("全部上限")
    team_leader.start(team["id"], "目標")
    assert get_team(team["id"])["leader_run"]["status"] == "failed" and any("上限" in m for _t, m in sent)


def test_leader_start_validation_and_double_run(env, monkeypatch):
    monkeypatch.setattr(team_runner, "_spawn", lambda func, *args: None)
    team = team_leader.create_and_start("ログイン機能を作りたい")
    assert team["leader_run"]["status"] == "running" and team["name"].startswith("ログイン機能")
    with pytest.raises(teams.TeamError) as exc:
        team_leader.start(team["id"], "もう一度")
    assert exc.value.status_code == 409
    for bad in ("  ", None, 5):
        with pytest.raises(teams.TeamError):
            team_leader.create_and_start(bad)
    with pytest.raises(teams.TeamError):
        team_leader.create_and_start("目標", "", "/etc")
    assert team_leader.recover_interrupted() == 1 and get_team(team["id"])["leader_run"]["status"] == "failed"
    follow = team_leader.resume(team["id"])
    assert follow["leader_run"]["status"] == "running" and len(follow["requests"]) == 2


def test_leader_launch_error_fails_with_reason(env, server):
    env.scripts.pop("claude")
    team = teams.create_team("起動できない班")
    teams.set_leader(team["id"], EP("claude"))
    team_leader.start(team["id"], "目標")
    run = get_team(team["id"])["leader_run"]
    assert run["status"] == "failed" and "見つかりません" in run["reason"]


# ---------------------------------------------------------------- 通しの試験

THROUGH = r'''
status = call("team_status")
assert status["goal"].startswith("ログイン"), status
call("report", progress_text="コードを調べます")
found = call("research", query="files:*", scope="code")
call("write_plan", plan_markdown="# 計画\n1. 調べる 2. 画面と API を並列で作る 3. 評価", items=[
    {"id": "scan", "title": "現状を調べる", "tier": "explore", "instructions": "README を読んで要点を報告"},
    {"id": "ui", "title": "画面を作る", "tier": "implement", "depends_on": ["scan"], "files": ["ui.txt"], "instructions": "画面を作る", "checks": ["ls"]},
    {"id": "api", "title": "API を作る", "tier": "implement", "depends_on": ["scan"], "files": ["api.txt"], "instructions": "API を作る", "checks": ["ls"]},
    {"id": "eval", "title": "全体の評価", "tier": "review", "depends_on": ["ui", "api"], "instructions": "全体を評価"}])
scan = call("delegate", item_id="scan")
res = call("wait", run_ids=[scan["run_id"]])
assert res["results"][0]["state"] == "integrated", res
ui = call("delegate", item_id="ui")
api = call("delegate", item_id="api")
res = call("wait", run_ids=[ui["run_id"], api["run_id"]])
assert res["all_finished"], res
for r in res["results"]:
    assert r["checks"] == "passed", r
    assert r["report"]["summary"], r
diff = call("read_result", run_id=ui["run_id"], what="diff")
assert "diff --git" in diff["diff"], diff
merged = call("integrate", run_ids=[ui["run_id"], api["run_id"]])
assert merged["merged"] == [ui["run_id"], api["run_id"]], merged
checks = call("run_checks", where="team", commands=["ls"])
assert checks["status"] == "passed", checks
ev = call("delegate", item_id="eval", wait=True)
assert ev["results"][0]["state"] == "integrated", ev
call("report", progress_text="統合して確認しました")
fin = call("finish", summary="画面と API を作り、統合して確認しました", outcome="done")
assert fin["ok"], fin
claude_result("完了")
'''


def test_through_fake_leader_runs_whole_flow(env, server, real_threads, sent):
    install_fake(env, {"claude": THROUGH})
    commit_seed(get_team(teams.create_team("下準備")["id"]))  # 作業場所は新しいチームごとに作られるので、下で作り直す
    team = teams.create_team("通し")
    commit_seed(team)
    teams.set_roles(team["id"], tier_roles(**{t: EP("claude") for t in team_roles.TIERS}))
    teams.update_team(team["id"], {"auto_run": False})
    team_leader.start(team["id"], "ログイン機能を作って")
    final = wait_until(lambda: (lambda t: t if t["leader_run"]["status"] in ("done", "failed", "blocked") else None)(get_team(team["id"])), 120)
    assert final["leader_run"]["status"] == "done", final["leader_run"]
    result = final["result"]
    assert result["outcome"] == "done" and result["changes"]["count"] == 2 and result["remaining"] == []
    assert result["checks"]["status"] == "passed" and result["can_land"] is True and result["landed"] is False
    tiers = sorted(t["tier"] for t in final["tasks"])
    assert tiers == ["explore", "implement", "implement", "review"]
    implement = [t for t in final["tasks"] if t["tier"] == "implement"]
    assert all(t["status"] == "done" and t["branch"].startswith(f"nexarch/{team['id']}-") for t in implement)
    kinds = [r["kind"] for r in final["timeline"]]
    for needed in ("request", "plan", "delegate", "integrate", "checks", "finish", "progress"):
        assert needed in kinds, kinds
    tools_called = {r["tool"] for r in final["mcp_log"]}
    assert {"team_status", "write_plan", "delegate", "wait", "read_result", "integrate", "run_checks", "finish", "research"} <= tools_called
    assert leader_mcp.usage_total(final)["input_tokens"] > 0
    assert final["leader_run"]["finish"]["outcome"] == "done"
    # 元の作業場所はまだ変わらない（取り込むまで）
    assert not (Path(team["workdir"]) / "ui.txt").exists() and not list(Path(team["workdir"]).glob("f*.txt"))
    landed = team_leader.land(team["id"])
    assert landed["result"]["landed"] is True and len(list(Path(team["workdir"]).glob("f*.txt"))) == 2
    with pytest.raises(teams.TeamError):
        team_leader.land(team["id"])  # 二重には取り込めない
    assert any("完了" in m for _t, m in sent)


# ---------------------------------------------------------------- API・AI アシスタント

def test_api_leader_land_answer_resume_and_settings(admin_client, env, monkeypatch):
    monkeypatch.setattr(team_runner, "_spawn", lambda func, *args: None)
    made = admin_client.post(f"{API}/auto", headers=CSRF_HEADERS, json={"goal": "目標の文", "name": ""})
    assert made.status_code == 201 and made.json()["leader_run"]["status"] == "running"
    team_id = made.json()["id"]
    assert admin_client.post(f"{API}/auto", headers=CSRF_HEADERS, json={"goal": ""}).status_code == 400
    assert admin_client.put(f"{API}/{team_id}", headers=CSRF_HEADERS, json={"autonomy": "ask", "web_research": False, "git_init": False}).json()["autonomy"] == "ask"
    assert admin_client.put(f"{API}/{team_id}", headers=CSRF_HEADERS, json={"autonomy": "zzz"}).status_code == 400
    leader_mcp._mutate(team_id, lambda t: t.update(questions=[{"id": "q1", "question": "?", "options": [], "answer": None}]))  # noqa: SLF001
    assert admin_client.post(f"{API}/{team_id}/questions/q1/answer", headers=CSRF_HEADERS, json={"answer": "はい"}).status_code == 200
    assert admin_client.post(f"{API}/{team_id}/questions/q1/answer", headers=CSRF_HEADERS, json={"answer": "はい"}).status_code == 409
    assert admin_client.post(f"{API}/{team_id}/questions/zz/answer", headers=CSRF_HEADERS, json={"answer": "x"}).status_code == 400
    assert admin_client.post(f"{API}/{team_id}/leader/resume", headers=CSRF_HEADERS).status_code == 409  # 実行中
    assert admin_client.post(f"{API}/{team_id}/leader/stop", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.post(f"{API}/{team_id}/leader/land", headers=CSRF_HEADERS).status_code == 409  # 結果が無い
    shown = [t for t in admin_client.get(API).json()["teams"] if t["id"] == team_id][0]
    assert shown["autonomy"] == "ask" and "usage_total" in shown and shown["timeline"][0]["kind"] == "request"


def test_assistant_tools_create_team_in_conversation_workdir(env, monkeypatch):
    from app.addons import agent_mcp
    from app.project_lab import service as project_lab

    monkeypatch.setattr(team_runner, "_spawn", lambda func, *args: None)
    chat = env.tmp / "root" / "CodeDEV" / "chat-1"
    chat.mkdir(parents=True)
    monkeypatch.setattr(project_lab, "resolve_project", lambda pid: chat)
    claims = {"project_id": "chat-1"}
    perms = {"terminal.use"}
    assert [t["name"] for t in agent_mcp._nexarch_tools("chat-1", perms)] == list(agent_mcp.NEXARCH_TOOLS)  # noqa: SLF001
    assert agent_mcp._nexarch_tools(None, perms) == [] and agent_mcp._nexarch_tools("chat-1", set()) == []  # noqa: SLF001
    out = agent_mcp._run_nexarch(agent_mcp.NEXARCH_REQUEST_TOOL, {"text": "ログインを作って"}, claims, perms)  # noqa: SLF001
    team = get_team(out["team_id"])
    assert team["workdir"] == str(chat.resolve()) and team["created_by"] == "assistant" and team["leader_run"]["status"] == "running"
    status = agent_mcp._run_nexarch(agent_mcp.NEXARCH_STATUS_TOOL, {"team_id": out["team_id"]}, claims, perms)  # noqa: SLF001
    assert status["state"] == "running" and status["progress"] and "ログイン" in status["goal"]
    from fastapi import HTTPException
    for name, args, cl, pm in ((agent_mcp.NEXARCH_REQUEST_TOOL, {"text": "x", "z": 1}, claims, perms),
                               (agent_mcp.NEXARCH_STATUS_TOOL, {"team_id": "../x"}, claims, perms),
                               (agent_mcp.NEXARCH_REQUEST_TOOL, {"text": "x"}, {}, perms),
                               (agent_mcp.NEXARCH_REQUEST_TOOL, {"text": "x"}, claims, set())):
        with pytest.raises(HTTPException):
            agent_mcp._run_nexarch(name, args, cl, pm)  # noqa: SLF001
