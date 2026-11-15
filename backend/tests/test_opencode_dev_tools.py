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
    assert "幅" in v2["agents"]["ui-verifier"]["system"] and "prompt" not in v2["agents"]["ui-verifier"]
    assert {"action": "playwright_*", "resource": "*", "effect": "deny"} in v2["agents"]["build"]["permissions"]


def test_task_prompt():
    assert provider.task_prompt("", "hello") == "hello"
    text = provider.task_prompt("ui_verify", "ログイン画面")
    assert text.startswith("【UI 検証】") and text.endswith("ログイン画面")
    assert "control_deck.report" in provider.task_prompt("research", "")
    with pytest.raises(provider.CodeAgentError):
        provider.task_prompt("deploy", "x")


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
