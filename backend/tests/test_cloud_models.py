"""外部のフロンティアモデル（docs/PLAN-frontier-models.md F1）。外部へは送らない（転送先はモック）。"""
import json

import httpx
import pytest

from app.models_mgmt import cloud


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud, "data_dir", lambda: tmp_path)
    return tmp_path


def _configure(provider="openrouter", models=("anthropic/claude-x",), **patch):
    cloud.save_provider(provider, {"api_key": "sk-or-v1-secretvalue1234"})
    cloud.save_provider(provider, {"enabled": True, "models": list(models), **patch})


def test_nothing_is_sent_by_default(store):
    _configure()
    with pytest.raises(cloud.CloudError) as caught:
        cloud.resolve("cloud:openrouter/anthropic/claude-x", caller="opencode")
    assert caught.value.status == 403 and "許可されていません" in str(caught.value)
    assert cloud.status()["egress"] == {caller: False for caller in cloud.CALLERS}


def test_allowed_caller_resolves_with_the_decrypted_key(store):
    _configure()
    cloud.save_egress({"opencode": True})
    target = cloud.resolve("cloud:openrouter/anthropic/claude-x", caller="opencode")
    assert (target.provider, target.model, target.base_url) == ("openrouter", "anthropic/claude-x", "https://openrouter.ai/api/v1")
    assert target.api_key == "sk-or-v1-secretvalue1234"
    with pytest.raises(cloud.CloudError):
        cloud.resolve("cloud:openrouter/anthropic/claude-x", caller="assistant")
    with pytest.raises(cloud.CloudError):
        cloud.resolve("cloud:openrouter/other/model", caller="opencode"), "選ばれていないモデルは送らない"


def test_the_key_is_never_stored_or_shown_in_plain_text(store):
    _configure()
    raw = (store / "cloud-providers.json").read_text()
    assert "secretvalue" not in raw
    assert (store / "cloud-providers.json").stat().st_mode & 0o777 == 0o600
    shown = json.dumps(cloud.status())
    assert "secretvalue" not in shown and cloud.status()["providers"][0]["key_hint"] == "1234"


def test_removing_the_key_disables_the_provider(store):
    _configure()
    cloud.save_provider("openrouter", {"api_key": ""})
    provider = next(p for p in cloud.status()["providers"] if p["id"] == "openrouter")
    assert not provider["configured"] and not provider["enabled"]
    assert cloud.enabled_models() == []


@pytest.mark.parametrize("patch", [
    {"base_url": "https://evil.example"},                      # 固定の送信先は変えられない
    {"models": ["bad model id"]}, {"monthly_budget_usd": -1}, {"unknown": 1}, {"api_key": "a b"},
])
def test_invalid_settings_are_rejected(store, patch):
    with pytest.raises(cloud.CloudError):
        cloud.save_provider("openrouter", patch)


def test_custom_endpoint_must_be_https(store):
    with pytest.raises(cloud.CloudError):
        cloud.save_provider("custom", {"base_url": "http://192.168.1.2:8000/v1"})
    with pytest.raises(cloud.CloudError):
        cloud.save_provider("custom", {"base_url": "https://user:pass@example.com/v1"})
    cloud.save_provider("custom", {"base_url": "https://llm.example.com/v1/"})
    assert next(p for p in cloud.status()["providers"] if p["id"] == "custom")["base_url"] == "https://llm.example.com/v1"


def test_monthly_budget_stops_further_calls(store):
    _configure(monthly_budget_usd=1.0)
    cloud.save_egress({"opencode": True})
    target = cloud.resolve("cloud:openrouter/anthropic/claude-x", caller="opencode")
    cloud.record_usage(target, "opencode", {"prompt_tokens": 10, "completion_tokens": 5, "cost": 1.2}, status_code=200)
    with pytest.raises(cloud.CloudError) as caught:
        cloud.resolve("cloud:openrouter/anthropic/claude-x", caller="opencode")
    assert caught.value.status == 429
    row = cloud.usage_rows()[0]
    assert row["cost_usd"] == 1.2 and row["prompt_tokens"] == 10 and "content" not in row


def test_unknown_caller_is_treated_as_external_api(store):
    _configure()
    cloud.save_egress({"api": True})
    assert cloud.resolve("cloud:openrouter/anthropic/claude-x", caller="anything").provider == "openrouter"


# ---- ゲートウェイ ----


class _Upstream:
    """httpx の送信をモックし、送った要求を記録する。"""

    def __init__(self, monkeypatch, response):
        self.sent = []
        outer = self

        class Transport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                outer.sent.append(request)
                return response

        real = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = Transport()
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", client)


def _gateway_headers(caller=""):
    from app.models_mgmt import gateway

    headers = {"Authorization": f"Bearer {gateway.get_api_key(create=True)}"}
    if caller:
        headers[gateway.CLIENT_HEADER] = caller
    return headers


def test_gateway_forwards_allowed_cloud_requests(client, store, monkeypatch):
    _configure()
    cloud.save_egress({"opencode": True})
    upstream = _Upstream(monkeypatch, httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "cost": 0.0001}}))
    response = client.post("/api/v1/llm/v1/chat/completions", headers=_gateway_headers("opencode"),
                           json={"model": "cloud:openrouter/anthropic/claude-x", "messages": [{"role": "user", "content": "x"}]})
    assert response.status_code == 200 and response.json()["choices"][0]["message"]["content"] == "hi"
    sent = upstream.sent[0]
    assert str(sent.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer sk-or-v1-secretvalue1234"
    assert json.loads(sent.content)["model"] == "anthropic/claude-x"
    assert cloud.usage_rows()[0]["caller"] == "opencode"


def test_gateway_refuses_when_the_caller_is_not_allowed(client, store, monkeypatch):
    _configure()
    upstream = _Upstream(monkeypatch, httpx.Response(200, json={}))
    response = client.post("/api/v1/llm/v1/chat/completions", headers=_gateway_headers("opencode"),
                           json={"model": "cloud:openrouter/anthropic/claude-x", "messages": []})
    assert response.status_code == 403 and upstream.sent == []


def test_gateway_lists_enabled_cloud_models(client, store):
    _configure()
    listed = client.get("/api/v1/llm/v1/models", headers=_gateway_headers()).json()["data"]
    assert {"id": "cloud:openrouter/anthropic/claude-x", "external": True}.items() <= next(
        item for item in listed if item["id"].startswith("cloud:")).items()


def test_settings_api_requires_settings_manage(admin_client, store):
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/api/v1/models/cloud").status_code in (401, 403)
    headers = {"X-Requested-With": "ControlDeck"}
    saved = admin_client.put("/api/v1/models/cloud/openrouter", json={"api_key": "sk-test-abcd"}, headers=headers)
    assert saved.status_code == 200 and "sk-test" not in saved.text
    assert admin_client.put("/api/v1/models/cloud/egress", json={"opencode": "yes"}, headers=headers).status_code == 400


def test_opencode_lists_cloud_models_only_when_allowed(store, monkeypatch, tmp_path):
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path / "integration")
    monkeypatch.setattr(provider, "codedev_root", lambda: tmp_path / "CodeDEV")
    _configure()
    models = provider._config_payload("j", "http://127.0.0.1:8765/api/v1/llm/v1", "auto")["provider"]["controldeck"]["models"]
    assert not any(key.startswith("cloud:") for key in models)
    cloud.save_egress({"opencode": True})
    models = provider._config_payload("j", "http://127.0.0.1:8765/api/v1/llm/v1", "auto")["provider"]["controldeck"]["models"]
    assert models["cloud:openrouter/anthropic/claude-x"]["name"].endswith("（外部）")
