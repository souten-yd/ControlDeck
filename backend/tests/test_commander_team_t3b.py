"""Nexarch の AI チーム T3b（ルーター・役割・プライマリ／セカンダリ・モデルと考えるレベル）の試験。

目的: 利用者（2026-11-27）の要望を仕組みで守る。
- 「チームはルーターが自動で作成して欲しい。ロールの設定をできるようにして、それに基づいて必要数を呼び出すイメージ」:
  目標からルーターが JSON の計画を返し、brief・役割の数・タスクが板に入る（形が違えば 1 回だけ再実行・だめなら止めて通知・
  役割の名前の外は捨てる・数は 1〜4 に丸める・確認のコマンドは許す形だけ）。役割の数まで担い手を呼び出す。
- 「プライマリとセカンダリー。期限切れでセカンダリに移行しても回復後にはプライマリに戻れると良い」:
  上限で止まったらセカンダリへ移り、回復の時刻の後の次のタスクからプライマリへ戻る（走っているタスクは止めない）。
- 「トークンが尽きた場合、プライマリ（引き継ぎ先）の設定が無い場合は止まるが、そのコンソールの設定を押すと引き継ぎ先を選べる」:
  chain が尽きたら止まる（blocked・no_handoff）＋通知、引き継ぎ先を選ぶ API で再開し、チェックでセカンダリに足す。
- 「ルーターを任命する感じで」「既定のルーター設定ができると良い」: チームのルーター・既定のルーターと既定の役割の写し。
- 「設定はコンソールとモデルを個別に設定できること。モデルは自動でリストアップし、考えるレベルも選べるように」:
  モデルの一覧（claude 固定・codex の models_cache・opencode のゲートウェイ）。鍵のファイル（auth.json）は読まない。
  model・effort を headless と console の引数へ渡し、不正な値（引数の取り違えになるもの）は断る。
根拠: 利用者の要望（2026-11-27）と docs/PLAN-nexarch-team.md T3。CLI は本物を呼ばない（T3a の試験と同じ偽の実行ファイル）。
隔離テストは env -u CONTROL_DECK_CONFIG で流す。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.commander import accounts, consoles, models, settings, team_dispatch, team_roles, team_router, team_runner, teams
from tests.conftest import CSRF_HEADERS
from tests.test_commander_team_t3a import (  # noqa: F401 - fixture
    API, Env, FakeManager, _clean_store, env, get_task, get_team, hold, keys, mk_task, mk_team, sent,
)


def EP(cli: str, kind: str = "headless", **kw) -> dict:
    return {"kind": kind, "cli": cli, "account": "default", "model": "", "effort": "", **kw}


def role(name: str, count: int = 1, primary: dict | None = None, secondary: list[dict] | None = None, **kw) -> dict:
    return {"name": name, "count": count, "primary": primary, "secondary": secondary or [], "note": "", **kw}


def team_with_roles(*roles: dict, **settings_) -> dict:
    team = teams.create_team("役割班")
    if settings_:
        teams.update_team(team["id"], settings_)
    teams.set_roles(team["id"], list(roles))
    return get_team(team["id"])


@pytest.fixture(autouse=True)
def _clean_settings_and_models(tmp_path):
    models.clear_cache()
    path = settings._path()
    existed = path.read_text(encoding="utf-8") if path.exists() else None
    if path.exists():
        path.unlink()
    yield
    models.clear_cache()
    if existed is None:
        if path.exists():
            path.unlink()
    else:
        path.write_text(existed, encoding="utf-8")


def task_in(team_id: str, role_id: str, title: str = "題") -> int:
    return teams.create_task(team_id, {"title": title, "goal": "作る", "role": role_id}, "t")["id"]


# ---------------------------------------------------------------- モデルの一覧

def test_models_claude_fixed_aliases_and_efforts():
    out = models.list_models("claude")
    assert [m["id"] for m in out["models"]] == ["fable", "opus", "sonnet", "haiku"]
    assert out["efforts"] == ["low", "medium", "high", "xhigh", "max"] and out["free_input"] is True


def test_models_codex_reads_cache_only_never_key_files(tmp_path, monkeypatch):
    home = tmp_path / "codexhome"
    home.mkdir()
    (home / "models_cache.json").write_text(json.dumps({"identity": {"mail": "x@y"}, "models": [
        {"slug": "gpt-a", "display_name": "GPT A", "visibility": "list", "default_reasoning_level": "low",
         "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
        {"slug": "gpt-hidden", "visibility": "hide", "supported_reasoning_levels": [{"effort": "low"}]},
        {"slug": "bad slug;rm", "supported_reasoning_levels": []},
    ]}), encoding="utf-8")
    (home / "auth.json").write_text("SECRET-TOKEN", encoding="utf-8")
    (home / ".credentials.json").write_text("SECRET-TOKEN", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    opened: list[str] = []
    real_read = Path.read_text

    def spy(self, *a, **kw):
        opened.append(self.name)
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)
    out = models.list_models("codex")
    assert [m["id"] for m in out["models"]] == ["gpt-a"] and out["models"][0]["efforts"] == ["low", "high"]
    assert out["efforts"] == ["low", "high"] and out["source"] == "codex-cache"
    assert opened == ["models_cache.json"] and "SECRET-TOKEN" not in json.dumps(out)


def test_models_codex_unreadable_gives_empty_list_with_free_input(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "none"))
    out = models.list_models("codex")
    assert out["models"] == [] and out["free_input"] is True and out["source"] == "none"
    (tmp_path / "none").mkdir()
    (tmp_path / "none" / "models_cache.json").write_text("{broken", encoding="utf-8")
    models.clear_cache()
    assert models.list_models("codex")["models"] == []


def test_models_opencode_lists_gateway_and_connection_default(monkeypatch):
    from app.models_mgmt import local_llm

    monkeypatch.setattr(local_llm, "llm_instances", lambda **kw: [{"alias": "qwen-local"}, {"alias": "glm-x"}])
    monkeypatch.setattr(accounts, "opencode_connection", lambda account: {"name": account, "model": "auto"})
    out = models.list_models("opencode")
    ids = [m["id"] for m in out["models"]]
    assert ids[:3] == ["auto", "qwen-local", "glm-x"] and out["default_model"] == "auto" and out["efforts"] == []


def test_models_result_cached_five_minutes(tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    cache = home / "models_cache.json"
    cache.write_text(json.dumps({"models": [{"slug": "m1", "supported_reasoning_levels": []}]}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert [m["id"] for m in models.list_models("codex")["models"]] == ["m1"]
    cache.write_text(json.dumps({"models": [{"slug": "m2", "supported_reasoning_levels": []}]}), encoding="utf-8")
    assert [m["id"] for m in models.list_models("codex")["models"]] == ["m1"]  # 覚えている
    models.clear_cache()
    assert [m["id"] for m in models.list_models("codex")["models"]] == ["m2"]


def test_models_api(admin_client):
    r = admin_client.get("/api/v1/commander/models", params={"cli": "claude"})
    assert r.status_code == 200 and r.json()["models"][1]["id"] == "opus"
    assert admin_client.get("/api/v1/commander/models", params={"cli": "gemini"}).status_code == 400


# ---------------------------------------------------------------- 引数への反映と検証

def test_claude_argv_gets_model_and_effort_only_when_set(env):
    argv, _e, _ = team_runner.build_launch({"cli": "claude", "model": "opus", "effort": "xhigh"}, "本文", env.work, env.tmp / "l")
    assert argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--effort") + 1] == "xhigh"
    plain, _e, _ = team_runner.build_launch({"cli": "claude"}, "本文", env.work, env.tmp / "l")
    assert "--model" not in plain and "--effort" not in plain


def test_codex_argv_gets_model_and_reasoning_effort(env):
    argv, _e, _ = team_runner.build_launch({"cli": "codex", "model": "gpt-a", "effort": "high"}, "本文", env.work, env.tmp / "l")
    assert argv[argv.index("-m") + 1] == "gpt-a" and argv[argv.index("-c") + 1] == "model_reasoning_effort=high"
    assert argv[-1] == "本文"
    plain, _e, _ = team_runner.build_launch({"cli": "codex"}, "本文", env.work, env.tmp / "l")
    assert "-m" not in plain and "-c" not in plain


def test_opencode_argv_model_and_variant(env, monkeypatch):
    monkeypatch.setattr(accounts, "opencode_connection", lambda account: {"name": account, "model": "auto"})
    argv, _e, _ = team_runner.build_launch({"cli": "opencode", "model": "qwen-local", "effort": "high"}, "本文", env.work, env.tmp / "l")
    assert argv[argv.index("--model") + 1] == "qwen-local#high"
    default, _e, _ = team_runner.build_launch({"cli": "opencode"}, "本文", env.work, env.tmp / "l")
    assert default[default.index("--model") + 1] == "auto"


@pytest.mark.parametrize("bad", ["-x", "a b", "a;rm", "$(x)", "x" * 81, "モデル", "a\nb"])
def test_endpoint_rejects_unsafe_model(env, bad):
    with pytest.raises(teams.TeamError):
        team_roles.clean_endpoint(EP("claude", model=bad))
    with pytest.raises(teams.TeamError):
        team_roles.clean_endpoint(EP("claude", effort=bad))


def test_endpoint_accepts_listed_and_free_values(env):
    ok = team_roles.clean_endpoint(EP("claude", model="claude-opus-4-1:beta/x#v", effort="max"))
    assert ok["model"] == "claude-opus-4-1:beta/x#v"
    with pytest.raises(teams.TeamError):
        team_roles.clean_endpoint(EP("gemini"))
    with pytest.raises(teams.TeamError):
        team_roles.clean_endpoint(EP("claude", kind="tmux"))
    with pytest.raises(teams.TeamError):
        team_roles.clean_endpoint(EP("claude", account="nope"))


def test_headless_member_keeps_model_and_validates(env):
    team = teams.create_team("班")
    done = teams.set_members(team["id"], [{"kind": "headless", "cli": "claude", "account": "default", "role": "developer",
                                           "label": "", "model": "opus", "effort": "high"}])
    assert done["members"][0]["model"] == "opus" and done["members"][0]["effort"] == "high"
    with pytest.raises(teams.TeamError):
        teams.set_members(team["id"], [{"kind": "headless", "cli": "claude", "role": "developer", "model": "-bad"}])


def test_console_command_gets_model_and_effort(env):
    command, _ = consoles._command_for(accounts.CLIS["claude"], str(env.work), None, "default", "opus", "high")
    assert "--model opus" in command and "--effort high" in command
    command, _ = consoles._command_for(accounts.CLIS["codex"], str(env.work), None, "default", "gpt-a", "high")
    assert "-m gpt-a" in command and "-c model_reasoning_effort=high" in command
    command, _ = consoles._command_for(accounts.CLIS["claude"], str(env.work), None, "default")
    assert "--model" not in command and "--effort" not in command


# ---------------------------------------------------------------- 役割

def test_new_team_gets_default_roles_and_router_copy(env):
    team = teams.create_team("班")
    assert [(r["name"], r["count"]) for r in team["roles"]] == [("実装", 2), ("レビュー", 1), ("試験", 1)]
    assert team["router"] is None and all(r["primary"] is None and r["secondary"] == [] for r in team["roles"])
    # 既定の設定を写す（id は新しい・設定は後から変えても既存のチームに響かない）
    ep = team_roles.clean_endpoint(EP("claude", model="sonnet"))
    cleaned = team_roles.clean_roles([role("設計", 3, primary=ep)])
    settings.set_team_defaults({**ep}, cleaned)
    copy = teams.create_team("写し")
    assert [r["name"] for r in copy["roles"]] == ["設計"] and copy["roles"][0]["id"] != cleaned[0]["id"]
    assert copy["roles"][0]["primary"]["model"] == "sonnet" and copy["router"]["model"] == "sonnet"
    assert [r["name"] for r in get_team(team["id"])["roles"]] == ["実装", "レビュー", "試験"]
    settings.set_team_defaults(None, [])
    assert [r["name"] for r in teams.create_team("戻し")["roles"]] == ["実装", "レビュー", "試験"]


def test_set_roles_validation(env):
    team = teams.create_team("班")
    ok = teams.set_roles(team["id"], [role("A", 4, primary=EP("claude"), secondary=[EP("codex")] * 3)])
    assert ok["roles"][0]["count"] == 4 and len(ok["roles"][0]["secondary"]) == 3
    for bad in ([role("A", 0)], [role("A", 5)], [role("A", True)], [role("A"), role("A")], [role("A", secondary=[EP("codex")] * 4)],
                [role("")], [role("x" * 21)], [role("A", primary={"cli": "zzz"})], "x"):
        with pytest.raises(teams.TeamError):
            teams.set_roles(team["id"], bad)


def test_task_role_validated_and_dropped_with_role(env):
    team = team_with_roles(role("実装", 1, EP("claude")))
    rid = team["roles"][0]["id"]
    assert teams.create_task(team["id"], {"title": "t", "role": rid}, "t")["role"] == rid
    with pytest.raises(teams.TeamError):
        teams.create_task(team["id"], {"title": "t", "role": "nope"}, "t")
    teams.set_roles(team["id"], [])
    assert get_team(team["id"])["tasks"][0]["role"] == ""


def test_dispatcher_calls_up_to_role_count_and_makes_auto_members(env, monkeypatch):
    hold(monkeypatch)
    team = team_with_roles(role("実装", 2, EP("claude", model="sonnet", effort="high")), max_parallel=4)
    rid = team["roles"][0]["id"]
    ids = [task_in(team["id"], rid, f"題{i}") for i in range(3)]
    assert team_dispatch.dispatch_team(team["id"]) == 2
    current = get_team(team["id"])
    assert sorted(t["status"] for t in current["tasks"]) == ["doing", "doing", "todo"]
    autos = [m for m in current["members"] if m.get("auto")]
    assert len(autos) == 2 and all(m["role_id"] == rid and m["model"] == "sonnet" and m["effort"] == "high" for m in autos)
    assert len({teams.member_key(m) for m in autos}) == 2
    shown = next(t for t in teams.list_teams() if t["id"] == team["id"])
    assert shown["roles"][0]["running"] == 2 and shown["roles"][0]["active_index"] == 0
    # 終わった枠は使い回す（メンバーを増やし続けない）
    done = current["tasks"][0]
    teams.update_task(team["id"], done["id"], {"status": "done"}, "t")
    assert team_dispatch.dispatch_team(team["id"]) == 1
    assert len([m for m in get_team(team["id"])["members"] if m.get("auto")]) == 2
    assert ids  # 3 件作った


def test_run_passes_model_and_effort_from_role(env):
    team = team_with_roles(role("実装", 1, EP("claude", model="opus", effort="max")))
    task_in(team["id"], team["roles"][0]["id"])
    assert team_dispatch.dispatch_team(team["id"]) == 1
    argv = env.calls("claude")[0]["argv"]
    assert argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--effort") + 1] == "max"


def test_role_without_endpoints_uses_legacy_pool(env):
    team = team_with_roles(role("実装", 1))
    teams.set_members(team["id"], [{"kind": "headless", "cli": "claude", "account": "default", "role": "developer", "label": "手動"}])
    tid = task_in(team["id"], team["roles"][0]["id"])
    assert team_dispatch.dispatch_team(team["id"]) == 1
    assert get_task(team["id"], tid)["status"] == "review"


def test_console_endpoint_opens_nexarch_console_in_team_workdir(env, monkeypatch):
    opened: list[dict] = []

    def fake_create(**kw):
        opened.append(kw)
        env.manager.sessions["aa000009"] = {"id": "aa000009", "alive": True, "commander_cli": kw["cli"].id, "program": kw["cli"].id}
        return {"opened": [{"id": "aa000009"}], "not_opened": 0, "reason": ""}

    monkeypatch.setattr(consoles, "create_consoles", fake_create)
    team = team_with_roles(role("実装", 1, EP("claude", kind="console", model="opus", effort="high")))
    tid = task_in(team["id"], team["roles"][0]["id"])
    assert team_dispatch.dispatch_team(team["id"]) == 1
    assert opened[0]["cwd"] == str(env.work) and opened[0]["model"] == "opus" and opened[0]["effort"] == "high"
    task = get_task(team["id"], tid)
    assert task["assignee"] == "aa000009" and task["status"] == "doing"
    assert any(i[0] == "aa000009" and "Nexarch タスク" in i[1] for i in env.manager.injected)


# ---------------------------------------------------------------- 上限で移る・回復で戻る・尽きたら止まる

def test_limit_moves_to_secondary_and_returns_to_primary_after_recovery(env, sent):
    env.mode("claude", "limit")
    team = team_with_roles(role("実装", 1, EP("claude"), [EP("codex")]))
    rid = team["roles"][0]["id"]
    first = task_in(team["id"], rid, "一つ目")
    assert team_dispatch.dispatch_team(team["id"]) == 1
    task = get_task(team["id"], first)
    assert task["status"] == "review" and [(r["cli"], r["result"]) for r in task["runs"]] == [("claude", "limited"), ("codex", "ok")]
    assert any("引き継ぎ" in h["event"] for h in task["history"]) and "【引き継ぎ】" in env.calls("codex")[0]["argv"][-1]
    assert any("引き継ぎました" in m for _t, m in sent)
    state = next(t for t in teams.list_teams() if t["id"] == team["id"])["roles"][0]
    assert state["on_secondary"] is True and state["active_index"] == 1
    # 回復の前の次のタスクはセカンダリ（上限のプライマリへは送らない）
    teams.update_task(team["id"], first, {"status": "done"}, "t")
    second = task_in(team["id"], rid, "二つ目")
    n_claude = len(env.calls("claude"))
    team_dispatch.dispatch_team(team["id"])
    assert len(env.calls("claude")) == n_claude and get_task(team["id"], second)["runs"][-1]["cli"] == "codex"
    # 回復の時刻が過ぎる → 次のタスクからプライマリへ戻る（履歴に残る）
    env.mode("claude", "ok")
    all_teams = teams._load()
    for entry in teams._find_team(all_teams, team["id"])["limits"].values():
        entry["until"] = 1.0
    teams._save(all_teams)
    teams.update_task(team["id"], second, {"status": "done"}, "t")
    third = task_in(team["id"], rid, "三つ目")
    team_dispatch.dispatch_team(team["id"])
    task3 = get_task(team["id"], third)
    assert task3["runs"][-1]["cli"] == "claude" and any("プライマリ（Claude Code）に戻りました" in h["event"] for h in task3["history"])
    assert get_task(team["id"], second)["runs"][-1]["cli"] == "codex"  # 走っていたものは止めない


def test_running_task_is_not_interrupted_when_primary_recovers(env, monkeypatch):
    hold(monkeypatch)
    team = team_with_roles(role("実装", 2, EP("claude"), [EP("codex")]))
    rid = team["roles"][0]["id"]
    all_teams = teams._load()
    t = teams._find_team(all_teams, team["id"])
    t["limits"] = {team_roles.endpoint_key(EP("claude")): {"until": 4_000_000_000.0, "at": 1.0}}
    t["roles"][0]["last_index"] = 1
    teams._save(all_teams)
    running = task_in(team["id"], rid, "走る")
    team_dispatch.dispatch_team(team["id"])
    assert get_task(team["id"], running)["status"] == "doing"
    all_teams = teams._load()
    teams._find_team(all_teams, team["id"])["limits"] = {}
    teams._save(all_teams)
    later = task_in(team["id"], rid, "次")
    team_dispatch.dispatch_team(team["id"])
    assert get_task(team["id"], running)["status"] == "doing" and get_task(team["id"], running)["runs"][-1]["cli"] == "codex"
    assert get_task(team["id"], later)["runs"][-1]["cli"] == "claude"


def test_chain_exhausted_blocks_with_notification_then_choose_handoff(env, sent):
    env.mode("claude", "limit")
    team = team_with_roles(role("実装", 1, EP("claude")))
    rid = team["roles"][0]["id"]
    tid = task_in(team["id"], rid)
    team_dispatch.dispatch_team(team["id"])
    task = get_task(team["id"], tid)
    assert task["status"] == "blocked" and task["blocked_reason"] == "no_handoff"
    assert any("引き継ぎ先なし" in h["event"] for h in task["history"])
    assert any("引き継ぎ先がありません" in m for _t, m in sent)
    view = next(t for t in teams.list_teams() if t["id"] == team["id"])["tasks"][0]
    assert view["blocked_reason"] == "no_handoff"
    # 引き継ぎ先を選ぶ → その場で再開し、チェックでセカンダリへ足す
    team_roles.choose_handoff(team["id"], tid, EP("codex", model="gpt-a", effort="high"), True, "t")
    done = get_task(team["id"], tid)
    assert done["status"] == "review" and done["runs"][-1]["cli"] == "codex"
    after = get_team(team["id"])
    assert [e["cli"] for e in after["roles"][0]["secondary"]] == ["codex"] and "blocked_reason" not in done
    argv = env.calls("codex")[0]["argv"]
    assert argv[argv.index("-m") + 1] == "gpt-a" and "【引き継ぎ】" in argv[-1]


def test_choose_handoff_without_adding_and_rejects_running(env, monkeypatch):
    env.mode("claude", "limit")
    team = team_with_roles(role("実装", 1, EP("claude")))
    tid = task_in(team["id"], team["roles"][0]["id"])
    team_dispatch.dispatch_team(team["id"])
    team_roles.choose_handoff(team["id"], tid, EP("codex"), False, "t")
    assert get_team(team["id"])["roles"][0]["secondary"] == []
    hold(monkeypatch)
    env.mode("claude", "ok")
    other = team_with_roles(role("実装", 1, EP("claude")))
    running = task_in(other["id"], other["roles"][0]["id"])
    team_dispatch.dispatch_team(other["id"])
    with pytest.raises(teams.TeamError):
        team_roles.choose_handoff(other["id"], running, EP("codex"), False, "t")


def test_wait_policy_keeps_same_endpoint(env):
    env.mode("claude", "limit")
    team = team_with_roles(role("実装", 1, EP("claude"), [EP("codex")]), on_limit="wait")
    tid = task_in(team["id"], team["roles"][0]["id"])
    team_dispatch.dispatch_team(team["id"])
    task = get_task(team["id"], tid)
    assert not env.calls("codex") and task.get("wait_until") and task["status"] == "doing"


def test_explicit_fallback_used_after_chain(env):
    env.mode("claude", "limit")
    team = team_with_roles(role("実装", 1, EP("claude")))
    teams.set_members(team["id"], [{"kind": "headless", "cli": "codex", "account": "default", "role": "developer", "label": "控え"}])
    spare = keys(get_team(team["id"]))[0]
    teams.update_team(team["id"], {"fallback": [spare]})
    tid = task_in(team["id"], team["roles"][0]["id"])
    team_dispatch.dispatch_team(team["id"])
    task = get_task(team["id"], tid)
    assert task["status"] == "review" and task["assignee"] == spare


# ---------------------------------------------------------------- ルーター

ROUTER_SCRIPT = r'''#!@@PY@@
import sys, json, os
REC = "@@REC@@"
STATE = "@@STATE@@"
args = sys.argv[1:]
n = int(open(STATE).read() or 0) if os.path.exists(STATE) else 0
open(STATE, "w").write(str(n + 1))
outs = json.load(open("@@OUTS@@"))
text = outs[min(n, len(outs) - 1)]
with open(REC, "a") as f:
    f.write(json.dumps({"kind": "@@KIND@@", "argv": args, "cwd": os.getcwd()}) + "\n")
if "@@KIND@@" == "claude":
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                      "total_cost_usd": 0.1, "usage": {"input_tokens": 10, "output_tokens": 5}}))
else:
    open(args[args.index("-o") + 1], "w").write(text)
    print(json.dumps({"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": text}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}))
'''


def router_fake(env: Env, cli: str, outputs: list[str]) -> None:
    outs = env.tmp / f"outs-{cli}.json"
    outs.write_text(json.dumps(outputs, ensure_ascii=False), encoding="utf-8")
    state = env.tmp / f"state-{cli}"
    if state.exists():
        state.unlink()
    path = env.tmp / f"fake-{cli}"
    path.write_text(ROUTER_SCRIPT.replace("@@PY@@", sys.executable).replace("@@REC@@", str(env.rec))
                    .replace("@@STATE@@", str(state)).replace("@@OUTS@@", str(outs)).replace("@@KIND@@", cli), encoding="utf-8")
    path.chmod(0o755)
    env.scripts[cli] = str(path)


def plan(**over) -> str:
    base = {"brief": "ログイン機能を作る", "roles": {"実装": 3, "レビュー": 1},
            "tasks": [{"title": "画面", "goal": "ログイン画面", "acceptance": "表示される", "files": ["src/login.py"],
                       "role": "実装", "checks": ["pytest -q"]},
                      {"title": "確認", "goal": "見る", "acceptance": "", "files": [], "role": "レビュー", "checks": []}]}
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


def auto_team(goal: str = "ログイン機能を作りたい") -> dict:
    created = team_router.create_and_start(goal)
    return get_team(created["id"])


def test_router_makes_team_from_goal(env, sent):
    router_fake(env, "claude", [plan()])
    settings.set_team_defaults(EP("claude", model="sonnet", effort="high"), None)
    team = auto_team()
    assert team["router_run"]["status"] == "done" and team["router_run"]["tasks"] == 2
    assert team["brief"] == "ログイン機能を作る"
    counts = {r["name"]: r["count"] for r in team["roles"]}
    assert counts["実装"] == 3 and counts["レビュー"] == 1 and counts["試験"] == 1
    by_role = {r["id"]: r["name"] for r in team["roles"]}
    assert [(t["title"], t["status"], by_role[t["role"]]) for t in team["tasks"]] == [("画面", "todo", "実装"), ("確認", "todo", "レビュー")]
    assert team["tasks"][0]["checks"] == ["pytest -q"] and team["tasks"][0]["files"] == ["src/login.py"]
    argv = env.calls("claude")[0]["argv"]
    assert argv[argv.index("--output-format") + 1] == "json" and "--json-schema" in argv
    assert argv[argv.index("--allowedTools") + 1] == "Read,Glob,Grep"  # 読むだけ
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "high"
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert set(schema["properties"]["roles"]["properties"]) == {"実装", "レビュー", "試験"}
    prompt = argv[argv.index("-p") + 1]
    assert "ログイン機能を作りたい" in prompt and "【役割】" in prompt and "実装" in prompt
    assert team["router_runs"][-1]["status"] == "done" and team["router_run"]["usage"]["output_tokens"] == 5


def test_router_prompt_lists_workdir_files_without_shell(env):
    router_fake(env, "claude", [plan()])
    created = teams.create_team("先に作る")
    (env.work / "x").mkdir(exist_ok=True)
    work = Path(created["workdir"])
    (work / "src").mkdir()
    (work / "src" / "a.py").write_text("x", encoding="utf-8")
    (work / ".git" / "config").write_text("secret", encoding="utf-8")
    for i in range(250):
        (work / f"f{i:03}.txt").write_text("x", encoding="utf-8")
    team_router.start(created["id"], "目標")
    prompt = prompt_of_call(env.calls("claude")[0])
    listed = [ln for ln in prompt.split("【作業場所のファイル（上位 200 件）】\n")[1].split("\n\n【役割】")[0].splitlines()]
    assert len(listed) == 200 and ".git/config" not in listed


def prompt_of_call(call: dict) -> str:
    argv = call["argv"]
    return argv[argv.index("-p") + 1]


def test_router_drops_unknown_roles_clamps_counts_and_bad_checks(env):
    bad_plan = plan(roles={"実装": 99, "魔法使い": 2, "レビュー": 0},
                    tasks=[{"title": "t", "goal": "g", "acceptance": "a", "files": [], "role": "魔法使い",
                            "checks": ["pytest -q", "cd x && make", "echo a | grep b", "", "python -c 'x", "ls\nrm -rf /", "pytest -q"]}])
    router_fake(env, "claude", [bad_plan])
    team = auto_team()
    counts = {r["name"]: r["count"] for r in team["roles"]}
    assert counts["実装"] == 4 and counts["レビュー"] == 1 and "魔法使い" not in counts
    task = team["tasks"][0]
    assert task["role"] == "" and task["checks"] == ["pytest -q"]
    events = [h["event"] for h in task["history"]]
    assert sum("確認のコマンドを捨てました" in e for e in events) >= 4 and any("役割なし" in e for e in events)
    assert any("丸めました" in n for n in team["router_run"]["dropped"]) and any("魔法使い" in n for n in team["router_run"]["dropped"])


def test_router_retries_once_when_shape_is_wrong(env):
    router_fake(env, "claude", ['{"oops": 1}', plan()])
    team = auto_team()
    assert team["router_run"]["status"] == "done" and len(env.calls("claude")) == 2
    assert "形が違います" in prompt_of_call(env.calls("claude")[1]) and "形が違います" not in prompt_of_call(env.calls("claude")[0])


def test_router_accepts_json_in_code_fence_and_noise(env):
    router_fake(env, "claude", ["説明です\n```json\n" + plan() + "\n```\n以上"])
    assert auto_team()["router_run"]["status"] == "done"


def test_router_fails_after_second_bad_output_and_notifies(env, sent):
    router_fake(env, "claude", ["これは JSON ではない", json.dumps({"brief": "x", "roles": {}, "tasks": []})])
    team = auto_team()
    assert team["router_run"]["status"] == "failed" and "形が違います" in team["router_run"]["reason"]
    assert team["tasks"] == [] and len(env.calls("claude")) == 2
    assert any("ルーターが作れませんでした" in m for _t, m in sent)
    assert "これは JSON" not in json.dumps(sent)


@pytest.mark.parametrize("bad", [
    {"brief": 1, "roles": {}, "tasks": [{"title": "t", "goal": "g"}]},
    {"brief": "", "roles": [], "tasks": [{"title": "t", "goal": "g"}]},
    {"brief": "", "roles": {}, "tasks": []},
    {"brief": "", "roles": {}, "tasks": [{"title": "t", "goal": "g"}] * 21},
    {"brief": "", "roles": {}, "tasks": [{"goal": "g"}]},
    {"brief": "", "roles": {"実装": "x"}, "tasks": [{"title": "t", "goal": "g"}]},
    [],
])
def test_validate_plan_rejects_wrong_shape(env, bad):
    team = teams.create_team("班")
    with pytest.raises(team_router.PlanError):
        team_router.validate_plan(bad, team)


def test_router_run_fails_on_cli_error_and_limit(env, sent):
    env.mode("claude", "limit")
    team = auto_team()
    assert team["router_run"]["status"] == "failed" and "上限" in team["router_run"]["reason"]
    env.mode("claude", "fail")
    assert auto_team("別の目標")["router_run"]["status"] == "failed"
    settings.set_team_defaults(EP("claude"), None)
    env.scripts.pop("claude")
    assert "見つかりません" in auto_team("さらに別")["router_run"]["reason"]


def test_router_codex_uses_output_schema_and_readonly(env):
    router_fake(env, "codex", [plan()])
    created = teams.create_team("codex 班")
    teams.set_router(created["id"], EP("codex", model="gpt-a", effort="high"))
    team_router.start(created["id"], "目標")
    argv = env.calls("codex")[0]["argv"]
    assert argv[argv.index("-s") + 1] == "read-only" and "--output-schema" in argv
    assert argv[argv.index("-m") + 1] == "gpt-a" and argv[argv.index("-c") + 1] == "model_reasoning_effort=high"
    assert get_team(created["id"])["router_run"]["status"] == "done"


def test_router_resolution_team_then_default_then_installed(env):
    team = teams.create_team("班")
    assert team_roles.router_endpoint(team)["cli"] == "claude"
    settings.set_team_defaults(EP("codex"), None)
    assert team_roles.router_endpoint(team)["cli"] == "codex"
    teams.set_router(team["id"], EP("opencode"))
    assert team_roles.router_endpoint(get_team(team["id"]))["cli"] == "opencode"
    teams.set_router(team["id"], None)
    assert get_team(team["id"])["router"] is None
    with pytest.raises(teams.TeamError):
        teams.set_router(team["id"], {"cli": "zzz"})


def test_router_uses_given_workdir_and_refuses_double_run(env, monkeypatch):
    hold(monkeypatch)
    folder = env.tmp / "root" / "mine"
    folder.mkdir()
    team = team_router.create_and_start("目標", "", str(folder))
    assert team["workdir"] == str(folder) and team["router_run"]["status"] == "running"
    with pytest.raises(teams.TeamError) as exc:
        team_router.start(team["id"], "もう一度")
    assert exc.value.status_code == 409
    with pytest.raises(teams.TeamError):
        team_router.create_and_start("目標", "", "/etc")
    with pytest.raises(teams.TeamError):
        team_router.create_and_start("  ")
    assert team_router.recover_interrupted() == 1 and get_team(team["id"])["router_run"]["status"] == "failed"


def test_router_starts_dispatcher_when_auto_run(env):
    router_fake(env, "claude", [plan(roles={"実装": 1}, tasks=[{"title": "t", "goal": "g", "acceptance": "", "files": [],
                                                                  "role": "実装", "checks": []}])])
    env.mode("codex", "ok")
    settings.set_team_defaults(None, team_roles.clean_roles([role("実装", 1, EP("codex"))]))
    team = auto_team()
    task = team["tasks"][0]
    assert task["status"] == "review" and task["runs"][-1]["cli"] == "codex"


# ---------------------------------------------------------------- API

def test_api_roles_router_auto_handoff_and_defaults(admin_client, env, monkeypatch):
    base = "/api/v1/commander"
    team = admin_client.post(API, headers=CSRF_HEADERS, json={"name": "班"}).json()
    assert len(team["roles"]) == 3
    r = admin_client.put(f"{API}/{team['id']}/roles", headers=CSRF_HEADERS,
                         json={"roles": [role("実装", 2, EP("claude", model="opus"), [EP("codex")])]})
    assert r.status_code == 200 and r.json()["roles"][0]["primary"]["model"] == "opus"
    assert admin_client.put(f"{API}/{team['id']}/roles", headers=CSRF_HEADERS, json={"roles": [role("A", 9)]}).status_code == 400
    r = admin_client.put(f"{API}/{team['id']}/router", headers=CSRF_HEADERS, json={"router": EP("claude", model="sonnet")})
    assert r.status_code == 200 and r.json()["router"]["model"] == "sonnet"
    assert admin_client.put(f"{API}/{team['id']}/router", headers=CSRF_HEADERS, json={"router": None}).json()["router"] is None
    listed = [t for t in admin_client.get(API).json()["teams"] if t["id"] == team["id"]][0]
    assert listed["roles"][0]["chain"][0]["label"].startswith("Claude Code") and listed["router_effective"]["cli"] == "claude"
    task = admin_client.post(f"{API}/{team['id']}/tasks", headers=CSRF_HEADERS,
                             json={"title": "t", "role": listed["roles"][0]["id"]}).json()
    assert task["role"] == listed["roles"][0]["id"]
    r = admin_client.post(f"{API}/{team['id']}/tasks/{task['id']}/handoff", headers=CSRF_HEADERS,
                          json={"endpoint": EP("codex"), "add_to_secondary": False})
    assert r.status_code == 200
    assert admin_client.post(f"{API}/{team['id']}/tasks/{task['id']}/handoff", headers=CSRF_HEADERS,
                             json={"endpoint": {"cli": "zzz"}}).status_code == 400
    # 既定
    r = admin_client.put(f"{base}/settings/team-defaults", headers=CSRF_HEADERS,
                         json={"default_router": EP("codex", model="gpt-a"), "default_roles": [role("設計", 2)]})
    assert r.status_code == 200 and r.json()["default_router"]["model"] == "gpt-a"
    assert admin_client.get(f"{base}/settings").json()["default_roles"][0]["name"] == "設計"
    assert admin_client.put(f"{base}/settings/team-defaults", headers=CSRF_HEADERS, json={"default_router": {"cli": "x"}}).status_code == 400
    # 自動で作る
    hold(monkeypatch)
    r = admin_client.post(f"{API}/auto", headers=CSRF_HEADERS, json={"goal": "目標の文"})
    assert r.status_code == 201 and r.json()["router_run"]["status"] == "running" and r.json()["roles"][0]["name"] == "設計"
    assert admin_client.post(f"{API}/auto", headers=CSRF_HEADERS, json={"goal": ""}).status_code == 400
    assert admin_client.put(f"{base}/settings/team-defaults", headers=CSRF_HEADERS, json={}).status_code == 200


def test_api_consoles_accept_model_and_effort(admin_client, env, monkeypatch):
    seen: dict = {}

    def fake_create(**kw):
        seen.update(kw)
        return {"opened": [], "not_opened": 0, "reason": ""}

    monkeypatch.setattr(consoles, "create_consoles", fake_create)
    r = admin_client.post("/api/v1/commander/consoles", headers=CSRF_HEADERS,
                          json={"cli": "claude", "cwd": str(env.work), "model": "opus", "effort": "high"})
    assert r.status_code == 201 and seen["model"] == "opus" and seen["effort"] == "high"
