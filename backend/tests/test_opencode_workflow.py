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
    # 最終検証のモデルが無くても、レビュー役にはセッションのモデルを明示する（空だと呼んだ側のモデルを引き継ぐ。
    # 2026-11-22 役割ごとのモデル）
    assert payload["agents"]["adversarial-reviewer"]["model"] == payload["agents"]["reviewer"]["model"]
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
    installed = provider.workflow_installed()
    assert {key: installed[key] for key in ("superpowers", "adversarial_review", "auto_advisor")} == {
        "superpowers": True, "adversarial_review": False, "auto_advisor": True}
    assert installed["ui_verify"] is False


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


# ---- 役割（レビュー・相談・開発担当）に外部モデルを選ぶ ----

CLOUD = "cloud:openrouter/anthropic/claude-x"


@pytest.fixture
def cloud_store(monkeypatch, tmp_path):
    from app.models_mgmt import cloud

    (tmp_path / "cloud").mkdir()
    monkeypatch.setattr(cloud, "data_dir", lambda: tmp_path / "cloud")
    cloud.save_provider("openrouter", {"api_key": "sk-or-v1-secretvalue1234"})
    cloud.save_provider("openrouter", {"enabled": True, "models": ["anthropic/claude-x"]})
    return cloud


def test_cloud_role_models_need_the_opencode_egress_switch(isolated, cloud_store):
    with pytest.raises(ValueError, match="外部への送信を許可"):
        provider.save_workflow({"review_model": CLOUD})
    assert provider.cloud_choices() == {"cloud_models": [], "cloud_blocked": True}
    cloud_store.save_egress({"assistant": True})
    with pytest.raises(ValueError):
        provider.save_workflow({"advisor_model": CLOUD})  # AI アシスタントへの許可は OpenCode には効かない
    cloud_store.save_egress({"opencode": True})
    assert provider.cloud_choices() == {"cloud_blocked": False, "cloud_models": [
        {"id": CLOUD, "name": "OpenRouter / anthropic/claude-x", "provider": "openrouter"}]}
    with pytest.raises(ValueError):
        provider.save_workflow({"review_model": "cloud:openrouter/not/selected"})
    saved = provider.save_workflow({"review_model": CLOUD, "advisor_model": CLOUD, "developer_model": CLOUD})
    assert (saved["review_model"], saved["advisor_model"], saved["developer_model"]) == (CLOUD, CLOUD, CLOUD)


def test_cloud_models_serve_as_reviewer_advisor_and_developer(isolated, cloud_store):
    cloud_store.save_egress({"opencode": True})
    provider.save_workflow({"adversarial_review": True, "auto_advisor": "active", "developer_agent": True,
                            "review_model": CLOUD, "advisor_model": CLOUD, "developer_model": CLOUD})
    payload, advisor = _v2(isolated)
    models = payload["providers"]["controldeck"]["models"]
    assert models[CLOUD]["name"].endswith("（外部）"), "外部と分かる名前のまま並ぶ"
    assert payload["agents"]["adversarial-reviewer"]["model"] == f"controldeck/{CLOUD}"
    assert payload["agents"]["constructive-reviewer"]["model"] == f"controldeck/{CLOUD}"
    assert advisor["advisor"]["models"] == [f"controldeck/{CLOUD}"]
    # 要否の判断は毎回走るので、セッションと同じ（ローカルの）モデルのまま。
    assert advisor["routing"]["models"] == ["controldeck/qwen-flash-next"]
    developer = payload["agents"]["developer"]
    assert developer["mode"] == "subagent" and developer["model"] == f"controldeck/{CLOUD}"
    assert "開発担当" in developer["description"]


def test_revoking_egress_drops_cloud_role_models_from_new_sessions(isolated, cloud_store):
    cloud_store.save_egress({"opencode": True})
    provider.save_workflow({"adversarial_review": True, "auto_advisor": "active", "developer_agent": True,
                            "review_model": CLOUD, "advisor_model": CLOUD, "developer_model": CLOUD})
    cloud_store.save_egress({"opencode": False})
    payload, advisor = _v2(isolated)
    assert CLOUD not in json.dumps(payload) and CLOUD not in json.dumps(advisor)
    assert advisor["advisor"]["models"] == ["controldeck/qwen-flash-next"]
    assert "model" not in payload["agents"]["developer"], "セッションと同じモデルで動く"
    # 保存済みの値を送り返すだけの保存（ほかの項目の変更）は止めない。新しく選ぶことはできない。
    saved = provider.save_workflow({"superpowers": True, "review_model": CLOUD, "advisor_model": CLOUD, "developer_model": CLOUD})
    assert saved["superpowers"] is True
    provider.save_workflow({"developer_model": ""})
    with pytest.raises(ValueError):
        provider.save_workflow({"developer_model": CLOUD})


def test_developer_agent_is_optional_and_can_use_a_local_model(isolated):
    payload, _ = _v2(isolated)
    assert "developer" not in payload["agents"]
    provider.save_workflow({"developer_agent": True})
    payload, _ = _v2(isolated)
    assert "model" not in payload["agents"]["developer"]
    provider.save_workflow({"developer_model": "maya-s"})
    payload, _ = _v2(isolated)
    assert payload["agents"]["developer"]["model"] == "controldeck/maya-s"
    assert payload["providers"]["controldeck"]["models"]["maya-s"]["limit"]["context"] == 1048576
    with pytest.raises(ValueError, match="登録されていない"):
        provider.save_workflow({"developer_model": "not-registered"})


def test_workflow_api_exposes_roles_and_cloud_choices(isolated, cloud_store):
    # 経路は機能が有効なときだけ登録されるので、endpoint の関数を直接呼ぶ。
    from fastapi import HTTPException

    from app.integrations.opencode import router as api

    shown = api.get_workflow(user=None)
    assert shown["developer_agent"] is False and shown["developer_model"] == ""
    assert shown["cloud_models"] == [] and shown["cloud_blocked"] is True
    with pytest.raises(HTTPException) as refused:
        api.put_workflow(api.WorkflowBody(developer_model=CLOUD), user=None)
    assert refused.value.status_code == 422
    cloud_store.save_egress({"opencode": True})
    saved = api.put_workflow(api.WorkflowBody(developer_agent=True, developer_model=CLOUD), user=None)
    assert saved["developer_agent"] is True and saved["developer_model"] == CLOUD
    assert api.get_workflow(user=None)["cloud_models"][0]["id"] == CLOUD


def test_auto_review_is_on_by_default_and_adds_the_review_procedure(isolated):
    payload, _ = _v2(isolated)
    instructions = payload.get("instructions") or []
    assert any(path.endswith("auto-review.md") for path in instructions)
    text = (provider.Path(provider.__file__).with_name("auto-review.md")).read_text()
    assert "reviewer" in text and "advisor()" in text
    # No review model chosen: the reviewer inherits the session model.
    assert payload["agents"]["reviewer"]["model"]  # 役割のモデルを明示する（2026-11-22）


def test_auto_review_can_be_turned_off(isolated):
    provider.save_workflow({"auto_review": False})
    payload, _ = _v2(isolated)
    assert not any(path.endswith("auto-review.md") for path in payload.get("instructions") or [])


def test_review_model_applies_to_the_builtin_reviewer(isolated):
    provider.save_workflow({"review_model": "maya-s"})
    payload, _ = _v2(isolated)
    assert payload["agents"]["reviewer"]["model"] == "controldeck/maya-s"
    assert "maya-s" in payload["providers"]["controldeck"]["models"]
