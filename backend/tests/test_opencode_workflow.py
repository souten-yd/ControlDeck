"""OpenCode development-workflow extensions: Superpowers, Adversarial Review, Auto Advisor."""
from __future__ import annotations

import json

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
    return tmp_path


def _v2(tmp_path, model="qwen-flash-next"):
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
    assert plugins == [provider.WORKFLOW_PLUGINS["v2"][k] for k in ("superpowers", "adversarial_review", "auto_advisor")]
    assert all("@latest" not in p for p in plugins), "plugins must be pinned"
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


def test_v1_gets_only_what_v1_supports(isolated):
    provider.save_workflow({"superpowers": True, "adversarial_review": True, "auto_advisor": "active"})
    path = provider._runtime_config("job1", "http://127.0.0.1:8765/api/v1/llm/v1", "qwen-flash-next")
    payload = json.loads(path.read_text())
    assert payload["plugin"] == [provider.WORKFLOW_PLUGINS["v1"]["superpowers"],
                                 provider.WORKFLOW_PLUGINS["v1"]["adversarial_review"]]
    assert "_auto_advisor" not in payload


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
