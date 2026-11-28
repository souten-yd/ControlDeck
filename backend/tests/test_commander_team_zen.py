"""AI チームの担い手に OpenCode（Zen・Go）のモデル（GLM-5.3 など）を使える、の試験。

目的: 2026-11-28 に本番の Nexarch で、チームの実装の担い手に OpenCode の GLM を当てようとしたら、選択肢に出ず、
起動も常に `controldeck/<モデル>`（ゲートウェイ）なので動かせないと分かった（利用者「ローカル LLM と OpenCode の
GLM と OpenRouter の DS を使って良い」）。次を守る:
- Zen の鍵があればモデルの一覧に Zen のモデルが出る（無ければ出ない）。
- 担い手が Zen のモデルなら `--model opencode-go/...` で起動し、設定のモデルはゲートウェイの既定のまま。
- 鍵は argv に出ない。箱なら 0600 の --env-file（後片付けで消える）、ホストなら子の環境変数。
- 鍵が無ければ起動の前に断る。コンソール（TUI）では断る。
根拠: 箱の作業場の Zen の渡し方（maintenance/sandbox.py の zen_env_file）と同じ形。既存の試験（t3a）は
_opencode_launch を偽物に置き換えていて、ここは見ていない（docs/test-catalog.md で確認）。
"""
from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.commander import accounts, consoles, models, team_runner
from app.integrations.opencode import provider, zen

KEY = "zen-secret-value-1234"


@pytest.fixture
def fake_opencode(monkeypatch, tmp_path):
    seen: dict = {}
    monkeypatch.setattr(provider, "active_binary", lambda: ("v2", Path("/opt/opencode2")))
    monkeypatch.setattr(provider, "get_settings", lambda: {"base_url": "http://127.0.0.1:8765/api/v1/llm/v1", "model": "auto"})

    def runtime_config(job, endpoint, model, **kw):
        seen["config_model"] = model
        path = tmp_path / job
        path.mkdir()
        return path

    monkeypatch.setattr(provider, "_runtime_config", runtime_config)
    monkeypatch.setattr(provider, "_managed_project_id", lambda cwd: "p")
    monkeypatch.setattr(provider, "_discard_runtime_config", lambda runtime, config: seen.setdefault("discarded", True))

    def launch(runtime, argv, cwd, config, *, interactive, extra_env=None, env_file=None):
        seen["extra_env"], seen["env_file"] = dict(extra_env or {}), env_file
        return list(argv), dict(extra_env or {})

    monkeypatch.setattr(provider, "_launch", launch)
    monkeypatch.setattr(zen, "get", lambda: KEY)
    monkeypatch.setattr(zen, "configured", lambda: True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    import app.models_mgmt.local_llm as local_llm

    async def ready(_url):
        return None

    monkeypatch.setattr(local_llm, "ensure_ready_by_base_url", ready)
    return seen


@pytest.mark.parametrize("mode", ["box", "host"])
def test_zen_model_runs_with_its_own_model_and_key_outside_argv(fake_opencode, monkeypatch, tmp_path, mode):
    monkeypatch.setattr(provider, "isolation_mode", lambda: mode)
    argv, env, cleanup = team_runner._opencode_launch("opencode-go/glm-5.3", tmp_path, "本文")
    assert argv[argv.index("--model") + 1] == "opencode-go/glm-5.3"
    assert fake_opencode["config_model"] == "auto", "設定はゲートウェイの既定のまま"
    assert all(KEY not in part for part in argv)
    if mode == "box":
        file = fake_opencode["env_file"]
        assert file and stat.S_IMODE(file.stat().st_mode) == 0o600 and KEY in file.read_text()
        assert KEY not in env.values()
        cleanup()
        assert not file.exists(), "後片付けで鍵のファイルを消す"
    else:
        assert fake_opencode["env_file"] is None and env["OPENCODE_API_KEY"] == KEY
        cleanup()
    assert fake_opencode.get("discarded")


def test_gateway_model_is_unchanged(fake_opencode, monkeypatch, tmp_path):
    monkeypatch.setattr(provider, "isolation_mode", lambda: "box")
    argv, _env, _ = team_runner._opencode_launch("qwen-flash-next", tmp_path, "本文")
    assert argv[argv.index("--model") + 1] == "controldeck/qwen-flash-next"
    assert fake_opencode["env_file"] is None and fake_opencode["extra_env"] == {}


def test_zen_without_key_is_refused_before_launch(fake_opencode, monkeypatch, tmp_path):
    monkeypatch.setattr(zen, "get", lambda: "")
    with pytest.raises(team_runner.LaunchError, match="Zen のキー"):
        team_runner._opencode_launch("opencode-go/glm-5.3", tmp_path, "本文")
    assert "config_model" not in fake_opencode


def test_zen_models_listed_only_with_a_key(monkeypatch):
    monkeypatch.setattr(accounts, "opencode_connection", lambda account: {"model": "auto"})
    import app.models_mgmt.local_llm as local_llm

    monkeypatch.setattr(local_llm, "llm_instances", lambda: [])
    monkeypatch.setattr(provider, "_cloud_models_for_opencode", lambda: [])
    monkeypatch.setattr(zen, "configured", lambda: True)
    ids = [m["id"] for m in models._opencode_models("default")[0]]
    assert "opencode-go/glm-5.3" in ids
    monkeypatch.setattr(zen, "configured", lambda: False)
    assert "opencode-go/glm-5.3" not in [m["id"] for m in models._opencode_models("default")[0]]


def test_console_refuses_zen_model():
    with pytest.raises(ValueError, match="コンソールでは使えません"):
        consoles._command_for(accounts.CLIS["opencode"], "/tmp", 1, model="opencode-go/glm-5.3")


def test_zen_and_cloud_members_do_not_take_the_local_slot():
    """GLM（Zen・Go）や OpenRouter のモデルの担い手は、ローカル LLM の同時の数（GPU を共有）に数えない。
    2026-11-28 本番: GLM の実装の担い手が、ローカルの調べものが終わるまで待たされた。"""
    from app.commander import team_dispatch, teams

    base = {"kind": "headless", "cli": "opencode", "account": "default"}
    assert teams.member_is_local({**base, "model": "auto"})
    assert teams.member_is_local({**base, "model": "qwen-flash-next"})
    assert not teams.member_is_local({**base, "model": "opencode-go/glm-5.3"})
    assert not teams.member_is_local({**base, "model": "cloud:openrouter/deepseek/deepseek-v4.1-flash-1017"})
    local = {**base, "id": "m1", "model": "auto"}
    glm = {**base, "id": "m2", "model": "opencode-go/glm-5.3"}
    team = {"members": [local, glm], "max_parallel": 3, "local_parallel": 1,
            "tasks": [{"id": 1, "status": "doing", "assignee": teams.member_key(local)}, {"id": 2, "status": "todo"}]}
    assert team_dispatch.blocker(team, team["tasks"][1], glm, check_overlap=False) == ""
    assert "ローカル" in team_dispatch.blocker(team, team["tasks"][1], {**local, "id": "m3"}, check_overlap=False)


def test_leader_sees_live_progress_and_wait_reason(monkeypatch):
    """チームリーダーの wait・read_result が、走っている仕事の今の進み具合と、待ちの理由を返す。
    2026-11-28 本番: 実行中の last_line・progress が空で、待ちの理由も無く、チームリーダーが状態を読めなかった。"""
    import time as _time

    from app.commander import leader_mcp, team_dispatch, team_runner

    team = {"id": "tX", "tasks": []}
    running = {"id": 1, "status": "doing", "title": "a", "run": {"started_at": _time.time() - 5, "last_line": "", "progress": []}}
    waiting = {"id": 2, "status": "todo", "title": "b"}
    with team_runner._active_lock:
        team_runner._active[("tX", 1)] = {"live": {"last_line": "shell: pytest", "progress": ["read: a.py"]}}
    team_dispatch._wait_reasons[("tX", 2)] = "ローカルの担い手の同時の数の上限です（GPU を共有）"
    try:
        monkeypatch.setattr(leader_mcp, "_brief", lambda team, task: {"run_id": str(task["id"])})
        monkeypatch.setattr(leader_mcp, "task_state", lambda task: "running" if task["status"] == "doing" else "queued")
        a = leader_mcp._result(team, running, {})
        assert a["last_line"] == "shell: pytest" and a["progress"] == ["read: a.py"] and a["elapsed_s"] >= 4
        b = leader_mcp._result(team, waiting, {})
        assert "GPU" in b["wait_reason"]
    finally:
        with team_runner._active_lock:
            team_runner._active.pop(("tX", 1), None)
        team_dispatch._wait_reasons.pop(("tX", 2), None)


def test_local_slot_is_counted_across_teams(monkeypatch):
    """ローカル LLM の担い手は、ほかのチームで走っている分も数える（GPU は 1 枚）。
    2026-11-28 本番: 3 チームの調べものが 1 枠の Strata に並び、1 本 17〜33 分かかった。"""
    from app.commander import team_dispatch, teams

    local = {"kind": "headless", "cli": "opencode", "account": "default", "model": "auto"}
    other = {"id": "tB", "members": [{**local, "id": "b1"}],
             "tasks": [{"id": 1, "status": "doing", "assignee": teams.member_key({**local, "id": "b1"})}]}
    mine = {"id": "tA", "members": [{**local, "id": "a1"}], "max_parallel": 3, "local_parallel": 1,
            "tasks": [{"id": 1, "status": "todo"}]}
    monkeypatch.setattr(teams, "_load", lambda: [other, mine])
    assert "ほかのチーム" in team_dispatch.blocker(mine, mine["tasks"][0], {**local, "id": "a1"}, check_overlap=False)
    other["tasks"][0]["status"] = "done"
    assert team_dispatch.blocker(mine, mine["tasks"][0], {**local, "id": "a1"}, check_overlap=False) == ""
