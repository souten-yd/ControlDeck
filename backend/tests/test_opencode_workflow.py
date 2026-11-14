"""OpenCode development-workflow extensions: Superpowers, Adversarial Review, Auto Advisor."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.integrations.opencode import provider


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from app.models_mgmt import local_llm
    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path)
    rows = [{"alias": "qwen-flash-next", "role": "llm", "ctx_size": 65536},
            {"alias": "maya-s", "role": "llm", "ctx_size": 1048576}]
    monkeypatch.setattr(local_llm, "list_instances", lambda **kw: list(rows))
    monkeypatch.setattr(local_llm, "llm_instances", lambda **kw: list(rows))
    monkeypatch.setattr(provider, "_api_key_for", lambda base: "key")
    # All three plugins installed by default; a test can remove one from `installed`.
    installed = {fid: tmp_path / "plugins" / fid for fid in provider.WORKFLOW_FEATURES.values()}
    monkeypatch.setattr(provider.registry, "plugin_path", lambda fid: installed.get(fid))
    return SimpleNamespace(path=tmp_path, installed=installed)


def _v2(env, model="qwen-flash-next"):
    root = provider._runtime_config("job", "http://127.0.0.1:8765/api/v1/llm/v1", model, runtime="v2")
    config = root / "opencode"
    payload = json.loads((config / "opencode.json").read_text())
    advisor = config / "auto-advisor.json"
    return payload, (json.loads(advisor.read_text()) if advisor.exists() else None)


def test_extensions_are_off_by_default(isolated):
    payload, advisor = _v2(isolated)
    assert "plugins" not in payload and advisor is None
    assert set(payload["providers"]["controldeck"]["models"]) == {"qwen-flash-next"}


def test_develop_with_flash_next_and_review_with_maya(isolated):
    provider.save_workflow({"superpowers": True, "adversarial_review": True, "auto_advisor": "active",
                            "review_model": "maya-s", "advisor_model": "maya-s"})
    payload, advisor = _v2(isolated)
    plugins = payload["plugins"]
    # Loaded from ControlDeck's managed installs, never fetched by OpenCode at run time.
    assert plugins == [(isolated.path / "plugins" / fid).as_uri() for fid in (
        "opencode-superpowers", "opencode-adversarial-review", "opencode-auto-advisor")]
    models = payload["providers"]["controldeck"]["models"]
    assert set(models) == {"qwen-flash-next", "maya-s"}
    assert models["maya-s"]["limit"]["context"] == 1048576
    assert payload["agents"]["adversarial-reviewer"]["model"] == "controldeck/maya-s"
    assert payload["agents"]["constructive-reviewer"]["model"] == "controldeck/maya-s"
    # The advisor is Maya-S; the per-step decision stays on the session model (no swap).
    assert advisor["advisor"]["models"] == ["controldeck/maya-s"]
    assert advisor["routing"] == {"mode": "active", "models": ["controldeck/qwen-flash-next"]}


def test_turning_off_removes_plugins_and_advisor_file(isolated):
    provider.save_workflow({"adversarial_review": True, "auto_advisor": "observe"})
    payload, advisor = _v2(isolated)
    assert advisor["routing"]["mode"] == "observe"
    # No review model: reviewers inherit the session model, nothing pinned or added.
    assert "adversarial-reviewer" not in payload["agents"]
    provider.save_workflow({"adversarial_review": False, "auto_advisor": "off"})
    payload, advisor = _v2(isolated)
    assert "plugins" not in payload and advisor is None


def test_an_enabled_extension_without_its_plugin_is_left_out(isolated):
    """Enabled in OpenCode settings but not installed in オプション機能: skip it, do not fetch."""
    isolated.installed.pop("opencode-adversarial-review")
    provider.save_workflow({"superpowers": True, "adversarial_review": True, "review_model": "maya-s"})
    payload, _ = _v2(isolated)
    assert payload["plugins"] == [(isolated.path / "plugins" / "opencode-superpowers").as_uri()]
    assert "adversarial-reviewer" not in payload["agents"]
    assert provider.workflow_installed() == {"superpowers": True, "adversarial_review": False, "auto_advisor": True}


def test_plugins_are_managed_options_requiring_opencode_v2(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.features import registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    spec = registry.FEATURES["opencode-adversarial-review"]
    assert spec["kind"] == "opencode-plugin" and spec["requires"] == "opencode-v2"
    assert registry.status("opencode-adversarial-review")["installed"] is False
    assert registry.plugin_path("opencode-adversarial-review") is None
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        package = tmp_path / "data" / "features" / "opencode-adversarial-review" / "node_modules" / spec["module"]
        package.mkdir(parents=True, exist_ok=True)
        version = "2.9.0" if argv[-1].endswith("@latest") else "2.0.2"
        (package / "package.json").write_text(json.dumps({"name": spec["module"], "version": version}))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", run)
    monkeypatch.setattr(registry, "executable", lambda fid, _orig=registry.executable:
                        (tmp_path / "opencode2") if fid == "opencode-v2" else _orig(fid))
    installed = registry.install("opencode-adversarial-review")
    assert calls[-1][-1] == "@capybearista/opencode-adversarial-review@2.0.2"   # pinned
    assert installed["installed"] and installed["enabled"] and installed["version"] == "2.0.2"
    updated = registry.update("opencode-adversarial-review")
    assert calls[-1][-1] == "@capybearista/opencode-adversarial-review@latest"
    assert updated["previous_version"] == "2.0.2" and updated["version"] == "2.9.0"
    assert registry.plugin_path("opencode-adversarial-review").name == "opencode-adversarial-review"
    registry.uninstall("opencode-adversarial-review")
    assert registry.plugin_path("opencode-adversarial-review") is None


def test_workflow_validation(isolated):
    with pytest.raises(ValueError):
        provider.save_workflow({"auto_advisor": "always"})
    with pytest.raises(ValueError):
        provider.save_workflow({"review_model": "not-registered"})
    assert provider.save_workflow({"review_model": "auto"})["review_model"] == "auto"


def test_gateway_switch_request_loads_the_named_model(monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models_mgmt import gateway, large_model_lifecycle, local_llm

    calls = []
    monkeypatch.setattr(gateway, "_authorize", lambda request: None)
    monkeypatch.setattr(gateway, "resolve_instance", lambda model: {"alias": model, "loaded": False})
    async def prepare(alias):
        calls.append(("prepare", alias))
    async def ready(alias, timeout_seconds=0):
        calls.append(("ready", alias))
        return True
    monkeypatch.setattr(large_model_lifecycle, "prepare_switch", prepare)
    monkeypatch.setattr(local_llm, "ensure_ready", ready)
    with TestClient(app) as client:
        response = client.post("/api/v1/llm/v1/models/maya-s/load")
    assert response.status_code == 200 and response.json()["ready"] is True
    assert calls == [("prepare", "maya-s"), ("ready", "maya-s")]
