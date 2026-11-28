"""OpenCode の作業の道具（UI 検証 = Playwright MCP）と、作業の結果（control_deck.report）。

計画: docs/PLAN-opencode-dev-environment.md 第 1 段階。
"""
import json

import pytest

from app.features import registry
from app.integrations.opencode import provider, reports


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """Playwright MCP が管理領域に入っていて、node と Chrome がある状態。"""
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    package = tmp_path / "data" / "features" / "playwright-mcp" / "node_modules" / "@playwright" / "mcp"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": "0.0.84", "bin": {"playwright-mcp": "cli.js"}}))
    (package / "cli.js").write_text("// cli")
    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path / "integration")
    monkeypatch.setattr(provider, "codedev_root", lambda: tmp_path / "CodeDEV")
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS})
    which = {"node": "/usr/bin/node", "google-chrome": "/usr/bin/google-chrome"}
    monkeypatch.setattr(provider.shutil, "which", lambda name: which.get(name))
    return package


def test_mcp_entry_reads_the_package_bin(installed):
    assert registry.mcp_entry("playwright-mcp") == (installed / "cli.js").resolve()
    status = registry.status("playwright-mcp")
    assert status["installed"] and status["version"] == "0.0.84"


def test_mcp_entry_refuses_a_bin_outside_the_package(installed):
    (installed / "package.json").write_text(json.dumps({"bin": "../../../../evil.js"}))
    assert registry.mcp_entry("playwright-mcp") is None


def test_browser_is_isolated_and_kept_off_controldeck_and_the_internet(installed):
    server = provider.ui_verify_server("job1")
    command = server["command"]
    assert command[:2] == ["/usr/bin/node", str((installed / "cli.js").resolve())]
    assert {"--headless", "--isolated"} <= set(command)
    assert command[command.index("--executable-path") + 1] == "/usr/bin/google-chrome"
    blocked = command[command.index("--blocked-origins") + 1]
    from app.config import get_config

    assert f"http://127.0.0.1:{get_config().server.port}" in blocked.split(";")
    allowed = command[command.index("--allowed-origins") + 1].split(";")
    assert "http://localhost:*" in allowed and all("localhost" in o or "127.0.0.1" in o or "[::1]" in o for o in allowed)
    assert command[command.index("--output-dir") + 1] == str(provider.ui_verify_dir("job1"))


def test_external_sites_only_when_allowed(installed, monkeypatch):
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS, "ui_verify_allow_external": True})
    assert "--allowed-origins" not in provider.ui_verify_server("job1")["command"]


def test_no_server_when_off_or_missing(installed, monkeypatch):
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS, "ui_verify": False})
    assert provider.ui_verify_server("job1") is None
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS})
    monkeypatch.setattr(provider.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    assert provider.ui_verify_server("job1") is None, "Chrome が無ければ載せない"


def test_the_browser_tools_go_to_the_subagent_only(installed):
    """道具の説明だけで数万トークンになる。主の会話には載せない。"""
    payload = provider._config_payload("job1", "http://127.0.0.1:8090/v1", "auto")
    assert payload["mcp"]["playwright"]["type"] == "local"
    verifier = payload["agent"]["ui-verifier"]
    assert verifier["mode"] == "subagent" and "幅" in verifier["prompt"]
    assert verifier["tools"] == {"playwright_*": True, "playwright_browser_run_code_unsafe": False}
    assert payload["agent"]["build"]["tools"]["playwright_*"] is False
    v2 = provider._v2_payload(payload)
    assert v2["mcp"]["servers"]["playwright"]["disabled"] is False
    rules = v2["agents"]["ui-verifier"]["permissions"]
    assert rules[-1] == {"action": "playwright_browser_run_code_unsafe", "resource": "*", "effect": "deny"}
    system = v2["agents"]["ui-verifier"]["system"]
    assert "幅" in system and "prompt" not in v2["agents"]["ui-verifier"]
    # system は既定の指示を置き換えるので、Code Mode の呼び方を先頭に足す（無いと MCP を呼べない）。
    assert system.startswith(provider.CODE_MODE_PRIMER) and "tools.playwright" in system
    assert {"action": "playwright_*", "resource": "*", "effect": "deny"} in v2["agents"]["build"]["permissions"]


@pytest.fixture
def report_root(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "data_dir", lambda: tmp_path / "data")
    return tmp_path


def _body(**patch):
    value = {
        "kind": "ui_verify", "title": "ログイン画面", "verdict": "fail", "target": "http://localhost:5173/",
        "checks": [{"viewport": "320x800", "theme": "light", "screenshot": "w320-light.png", "status": "fail"},
                   {"viewport": "1280x800", "theme": "dark", "screenshot": "w1280-dark.png"}],
        "findings": [{"severity": "major", "title": "送信ボタンが画面外", "viewport": "320x800", "screenshot": "w320-light.png"},
                     {"severity": "minor", "title": "余白"}],
    }
    value.update(patch)
    return reports.ReportBody.model_validate(value)


def test_report_takes_its_screenshots_with_it(report_root):
    shots = report_root / "shots" / "2026-11-15"
    shots.mkdir(parents=True)
    (shots / "w320-light.png").write_bytes(b"\x89PNG")
    record = reports.save(_body(), owner_user_id=1, session="tui-1", images_from=report_root / "shots")
    assert record["missing_images"] == ["w1280-dark.png"]
    (shots / "w320-light.png").write_bytes(b"other")          # 次の session が撮り直しても
    path = reports.image_path(record["id"], "w320-light.png", owner_user_id=1)
    assert path.read_bytes() == b"\x89PNG"                     # 報告の写真は変わらない
    row = reports.list_reports(owner_user_id=1)[0]
    assert row["counts"] == {"critical": 0, "major": 1, "minor": 1, "info": 0}
    assert row["thumbnail"] == "w320-light.png"


def test_reports_are_private_to_their_owner(report_root):
    record = reports.save(_body(), owner_user_id=1, session="tui-1", images_from=None)
    assert reports.get(record["id"], owner_user_id=2) is None
    assert reports.list_reports(owner_user_id=2) == []
    assert reports.get(record["id"], owner_user_id=None) is not None   # 管理者
    assert not reports.delete(record["id"], owner_user_id=2)
    assert reports.delete(record["id"], owner_user_id=1)


def test_screenshot_names_cannot_escape(report_root):
    # 場所の部分は捨て、ファイル名だけで出力先の中を探す。外へは出られない。
    assert _body(checks=[{"screenshot": "../../secret.png"}]).checks[0].screenshot == "secret.png"
    with pytest.raises(ValueError):
        _body(checks=[{"screenshot": "notes.txt"}])
    record = reports.save(_body(checks=[], findings=[]), owner_user_id=1, session="s", images_from=None)
    assert reports.image_path(record["id"], "../report.json", owner_user_id=1) is None
    with pytest.raises(reports.ReportError):
        reports.get("../../etc", owner_user_id=None)


def test_report_tool_is_offered_and_saves(monkeypatch, report_root):
    from fastapi import HTTPException

    from app.addons import agent_mcp

    tool = agent_mcp._report_tool()
    assert tool["name"] == "control_deck.report"
    assert "ui_verify" in json.dumps(tool["inputSchema"])

    class _User:
        id = 7

    recorded = []
    monkeypatch.setattr(agent_mcp.audit, "record", lambda *a, **k: recorded.append(a[1]))
    monkeypatch.setattr(provider, "_integration_dir", lambda: report_root / "integration")
    result = agent_mcp._save_report(
        {"kind": "review", "title": "差分", "verdict": "pass", "findings": [{"severity": "info", "title": "ok"}]},
        _User(), {"sub": "opencode:tui-7"}, None, None)
    assert reports.get(result["id"], owner_user_id=7)["session"] == "tui-7"
    assert recorded == ["opencode.report"]
    with pytest.raises(HTTPException) as caught:
        agent_mcp._save_report({"kind": "nope", "title": "x"}, _User(), {"sub": "opencode:tui-7"}, None, None)
    assert caught.value.status_code == 422


# ---- 精密調査（control_deck.research、第 2 段階） ----

from app.integrations.opencode import research  # noqa: E402


def test_research_stays_local_unless_allowed(tmp_path):
    local = {"research_tool": True, "research_external": False}
    planned = research.plan({"question": "x", "sources": ["web", "local_code"]}, local, project_path=tmp_path)
    assert planned["sources"] == ["local_code"] and planned["refused"] == ["web"]
    with pytest.raises(research.ResearchRefused):
        research.plan({"question": "x", "sources": ["web"]}, local, project_path=tmp_path)
    opened = research.plan({"question": "x"}, {**local, "research_external": True}, project_path=None)
    assert "web" in opened["sources"] and "local_code" not in opened["sources"]


def test_research_project_must_be_inside_codedev(tmp_path):
    codedev = tmp_path / "CodeDEV"
    (codedev / "game").mkdir(parents=True)
    (tmp_path / "secret").mkdir()
    assert research.resolve_project(str(codedev / "game"), codedev=codedev) == (codedev / "game").resolve()
    assert research.resolve_project(str(tmp_path / "secret"), codedev=codedev) is None
    assert research.resolve_project(str(codedev / "game" / ".." / ".." / "secret"), codedev=codedev) is None
    assert research.resolve_project("/etc", codedev=codedev) is None


def test_research_tool_runs_deep_research_and_keeps_a_report(monkeypatch, report_root):
    import asyncio

    from app.addons import agent_mcp
    from app.workflows import chat_router

    codedev = report_root / "CodeDEV"
    (codedev / "game").mkdir(parents=True)
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS, "model": "auto"})
    monkeypatch.setattr(provider, "codedev_root", lambda: codedev)
    seen = {}

    async def fake_deep(body, progress=None):
        seen["sources"], seen["path"] = body.source_types, body.local_project_path
        return {"report": "結論 [1]", "sources": [
            {"title": "main.py", "url": "", "source": "local_code", "kind": "document", "snippet": "def f()", "meta": {}}],
            "research": {"citation_metrics": {"citation_coverage": 1.0}, "coverage_limits": []}}

    monkeypatch.setattr(chat_router, "_deep_search", fake_deep)
    monkeypatch.setattr(agent_mcp.audit, "record", lambda *a, **k: None)

    class _User:
        id = 3

    result = asyncio.run(agent_mcp._run_research(
        {"question": "f はどこで呼ばれる？", "sources": ["local_code", "web"], "project_dir": str(codedev / "game")},
        _User(), {"sub": "opencode:tui-3"}, None, None))
    assert seen == {"sources": ["local_code"], "path": str((codedev / "game").resolve())}
    assert result["used_sources"] == ["local_code"] and result["report"] == "結論 [1]"
    saved = reports.get(result["report_id"], owner_user_id=3)
    assert saved["kind"] == "research" and saved["sources"][0]["primary"] is True
    assert any("web" in item for item in saved["unverified"]), "許可されなかった情報源を記録する"


def test_research_tool_can_be_turned_off(monkeypatch):
    from app.addons import agent_mcp

    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS, "research_tool": False})
    assert agent_mcp._research_tool() is None
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS})
    assert "外部へ問い合わせない" in agent_mcp._research_tool()["description"]


def test_team_worker_research_respects_team_settings(monkeypatch, report_root):
    """目的: Nexarch のチームの担い手の精密調査（control_deck.research）が、チームの設定に従うこと。

    グローバルの外部送信の許可（research_external）が出ていても、
    - Web の調査を断ったチーム（web_research=false）
    - 閉じたチーム（local_only・ローカルだけ）
    の担い手が Web の情報源を使えないように、MCP の口（サーバー側）で止める。
    根拠: 2026-11-28 の依頼（brief C）「web_research=false は担い手の Web 道具にも、local_only=true は
    全起動/引継ぎ/モデル解決/Web出口で強制」。チームリーダーの research(web)（leader_mcp）は既に
    チームの設定を見るが、担い手の道はここで初めて止める。"""
    import asyncio

    from fastapi import HTTPException

    from app.addons import agent_mcp
    from app.commander import teams as commander_teams
    from app.workflows import chat_router

    monkeypatch.setattr(provider, "get_settings",
                        lambda: {**provider.DEFAULT_SETTINGS, "research_tool": True, "research_external": True})
    monkeypatch.setattr(commander_teams, "_make_workdir",
                        lambda name: {"workdir": "", "workdir_folder": "", "workdir_name": ""})
    store = commander_teams._path()
    for target in (store, store.with_name("teams.json.bak")):
        if target.exists():
            target.unlink()

    async def fail_if_called(body, progress=None):  # noqa: ARG001
        raise AssertionError("拒否されるはずの調査が Web へ出ました")

    monkeypatch.setattr(chat_router, "_deep_search", fail_if_called)

    class _User:
        id = 3

    made: list[str] = []
    try:
        team = commander_teams.create_team("断る班")
        made.append(team["id"])
        commander_teams.update_team(team["id"], {"web_research": False})
        claims = {"sub": f"opencode:team-{team['id']}-5", "actor_user_id": 3}
        with pytest.raises(HTTPException) as caught:
            asyncio.run(agent_mcp._run_research({"question": "x", "sources": ["web"]}, _User(), claims, None, None))
        assert caught.value.status_code == 403 and "Web の調査" in caught.value.detail
        # 閉じたチーム（local_only）でも同じく断る
        commander_teams.update_team(team["id"], {"web_research": True, "local_only": True})
        with pytest.raises(HTTPException) as caught2:
            asyncio.run(agent_mcp._run_research({"question": "x", "sources": ["web"]}, _User(), claims, None, None))
        assert caught2.value.status_code == 403 and "ローカル" in caught2.value.detail
        # チームの設定が許すときは今までどおり流れる（Web の情報源も使える）
        commander_teams.update_team(team["id"], {"local_only": False})
        monkeypatch.setattr(agent_mcp.audit, "record", lambda *a, **k: None)

        async def fake_deep(body, progress=None):  # noqa: ARG001
            return {"report": "結論 [1]", "sources": [
                {"title": "x", "url": "https://example.com/a", "source": "web", "kind": "document", "snippet": "s", "meta": {}}],
                "research": {"citation_metrics": {"citation_coverage": 1.0}, "coverage_limits": []}}

        monkeypatch.setattr(chat_router, "_deep_search", fake_deep)
        result = asyncio.run(agent_mcp._run_research({"question": "x", "sources": ["web"]}, _User(), claims, None, None))
        assert result["used_sources"] == ["web"]
        # チームに結びつかない token（ふつうの OpenCode の会話）は今までどおり
        plain = asyncio.run(agent_mcp._run_research({"question": "x", "sources": ["web"]}, _User(),
                                                    {"sub": "opencode:tui-3", "actor_user_id": 3}, None, None))
        assert plain["used_sources"] == ["web"]
    finally:
        for tid in made:
            try:
                commander_teams.delete_team(tid)
            except Exception:  # noqa: BLE001 - 掃除の失敗は試験を落とさない
                pass
        for target in (store, store.with_name("teams.json.bak")):
            if target.exists():
                target.unlink()


# ---- Context7・レビュー（第 2・3 段階） ----


def test_context7_is_off_until_turned_on(installed, monkeypatch, tmp_path):
    package = tmp_path / "data" / "features" / "context7-mcp" / "node_modules" / "@upstash" / "context7-mcp"
    (package / "dist").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"version": "4.4.4", "bin": {"context7-mcp": "dist/index.js"}}))
    (package / "dist" / "index.js").write_text("// x")
    assert provider.context7_server() is None, "外部へ問い合わせるので既定はオフ"
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS, "context7": True})
    server = provider.context7_server()
    assert server["command"] == ["/usr/bin/node", str((package / "dist" / "index.js").resolve())]
    payload = provider._config_payload("job1", "http://127.0.0.1:8090/v1", "auto")
    assert "context7" in payload["mcp"]


def test_reviewer_cannot_edit_and_gets_ast_grep(installed, monkeypatch, tmp_path):
    payload = provider._config_payload("job1", "http://127.0.0.1:8090/v1", "auto")
    reviewer = payload["agent"]["reviewer"]
    assert reviewer["mode"] == "subagent" and "control_deck.report" in reviewer["prompt"]
    assert reviewer["tools"] == {"edit": False, "write": False, "patch": False}
    assert "ast-grep" not in reviewer["description"], "未導入なら名前を出さない"
    assert "PATH" not in provider._runtime_env("v2", tmp_path)

    bin_dir = tmp_path / "data" / "features" / "ast-grep" / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "ast-grep").write_text("#!/bin/sh\necho ast-grep 0.51.0\n")
    (bin_dir / "ast-grep").chmod(0o755)
    assert provider.review_tool_bin() == bin_dir
    assert provider._runtime_env("v2", tmp_path)["PATH"].startswith(str(bin_dir))
    assert "ast-grep" in provider._reviewer_agent()["description"]
    assert "reviewer" in provider.Path(provider.__file__).with_name("auto-review.md").read_text()


def test_github_cli_only_when_allowed(monkeypatch, tmp_path):
    gh = tmp_path / "gh-config"
    gh.mkdir()
    (gh / "hosts.yml").write_text("github.com: {}\n")
    monkeypatch.setenv("GH_CONFIG_DIR", str(gh))
    monkeypatch.setattr(provider, "review_tool_bin", lambda: None)
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS})
    assert "GH_CONFIG_DIR" not in provider._runtime_env("v2", tmp_path), "既定は渡さない"
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS, "github_cli": True})
    assert provider._runtime_env("v2", tmp_path)["GH_CONFIG_DIR"] == str(gh)
    (gh / "hosts.yml").unlink()
    assert "GH_CONFIG_DIR" not in provider._runtime_env("v2", tmp_path), "ログインしていなければ渡さない"


def test_gh_token_and_destructive_commands_are_denied():
    rules = provider._forbidden_commands()
    for command in ("gh auth", "gh auth *", "* gh repo delete *", "*;gh secret *", "gh api *"):
        assert rules.get(command) == "deny", command
    assert "gh pr *" not in rules


# ---- プロジェクトの箱（OpenCode を podman の中で動かす） ----


def test_opencode_runs_in_a_project_box(monkeypatch, tmp_path):
    from app.maintenance import sandbox

    project = tmp_path / "CodeDEV" / "game"
    project.mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS})
    monkeypatch.setattr(provider.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path / "integration")
    monkeypatch.setattr(provider, "_skill_paths", lambda: [])
    monkeypatch.setattr(provider, "_addon_asset_roots", lambda: [])
    monkeypatch.setattr(sandbox, "_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(provider.Path, "home", lambda: tmp_path / "home")
    argv, env = provider._launch("v2", ["opencode2", "--standalone"], project, config, interactive=True)
    assert env == {}, "環境変数は箱の中へ渡す"
    assert argv[:2] == ["/usr/bin/podman", "run"] and "-it" in argv
    volumes = [argv[i + 1] for i, item in enumerate(argv) if item == "-v"]
    writable = {v.split(":")[0] for v in volumes if v.endswith(":rw")}
    assert str(project) in writable and str(config) in writable
    shared_cli = provider.cli_config_path()
    assert f"{shared_cli}:{shared_cli}:ro" in volumes
    assert str(shared_cli) not in writable
    assert not any(v.startswith(f"{shared_cli.parent}:") for v in volumes)
    assert not any("CodeDEV:" in v or v.startswith(f"{tmp_path / 'data'}:") for v in volumes), "他のプロジェクト・本番の data は見せない"
    assert argv[argv.index("-w") + 1] == str(project)
    assert argv[-2:] == ["opencode2", "--standalone"]
    assert "CONTROL_DECK_OPENCODE_BOX=1" in argv
    assert f"OPENCODE_CONFIG_DIR={config / 'opencode'}" in argv


def test_host_mode_and_missing_podman_fall_back(monkeypatch, tmp_path):
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS, "isolation": "host"})
    assert provider.isolation_mode() == "host"
    monkeypatch.setattr(provider, "get_settings", lambda: {**provider.DEFAULT_SETTINGS})
    monkeypatch.setattr(provider.shutil, "which", lambda name: None)
    assert provider.isolation_mode() == "host"
    argv, env = provider._launch("v2", ["opencode2"], tmp_path, tmp_path, interactive=True)
    assert argv == ["opencode2"] and env["XDG_CONFIG_HOME"] == str(tmp_path)


def test_isolation_setting_is_validated(monkeypatch, tmp_path):
    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "settings.json")
    assert provider.save_workflow({"isolation": "host"})["isolation"] == "host"
    with pytest.raises(ValueError):
        provider.save_workflow({"isolation": "vm"})


def test_browser_has_video_caps_for_the_operation_test(installed):
    """操作テストの動画（browser_start_video）はこの版では --caps=devtools が要る。"""
    assert "--caps=devtools" in provider.ui_verify_server("job1")["command"]
