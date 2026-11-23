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


# ---- 費用・データの扱い・残高（OpenRouter） ----


def test_data_policy_is_enforced_on_every_openrouter_request(client, store, monkeypatch):
    _configure()
    cloud.save_egress({"opencode": True})
    assert next(p for p in cloud.status()["providers"] if p["id"] == "openrouter")["data_policy"] == "deny"
    upstream = _Upstream(monkeypatch, httpx.Response(200, json={"choices": [], "usage": {}}))
    body = {"model": "cloud:openrouter/anthropic/claude-x", "messages": [],
            "provider": {"data_collection": "allow", "order": ["x"]}}
    client.post("/api/v1/llm/v1/chat/completions", headers=_gateway_headers("opencode"), json=body)
    sent = json.loads(upstream.sent[-1].content)["provider"]
    assert sent["data_collection"] == "deny" and sent["order"] == ["x"], "呼び出し元に緩めさせない"
    cloud.save_provider("openrouter", {"data_policy": "zdr"})
    client.post("/api/v1/llm/v1/chat/completions", headers=_gateway_headers("opencode"), json=body)
    assert json.loads(upstream.sent[-1].content)["provider"]["zdr"] is True
    with pytest.raises(cloud.CloudError):
        cloud.save_provider("openai", {"data_policy": "zdr"})
    with pytest.raises(cloud.CloudError):
        cloud.save_provider("openrouter", {"data_policy": "none"})


def test_remote_models_carry_price_and_privacy(store, monkeypatch):
    import asyncio

    _configure()
    monkeypatch.setattr(cloud, "_zdr_models", lambda: _async({"vendor/safe"}))
    _Upstream(monkeypatch, httpx.Response(200, json={"data": [
        {"id": "vendor/safe", "name": "Safe", "pricing": {"prompt": "0.000003", "completion": "0.000015"}, "created": 1700000000},
        {"id": "vendor/stored", "name": "Stored", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "vendor/free:free", "name": "Free", "pricing": {"prompt": "0", "completion": "0"}},
    ]}))
    rows = {row["id"]: row for row in asyncio.run(cloud.list_remote_models("openrouter"))}
    assert rows["vendor/safe"]["price_input"] == 3.0 and rows["vendor/safe"]["price_output"] == 15.0
    assert rows["vendor/safe"]["privacy"] == "zdr" and rows["vendor/safe"]["created"] == 1700000000
    assert rows["vendor/stored"]["privacy"] == "may_store"
    assert rows["vendor/free:free"]["privacy"] == "may_train" and rows["vendor/free:free"]["free"] is True


def test_account_reports_the_remaining_balance(store, monkeypatch):
    import asyncio

    _configure()
    # /key（このキー）と /credits（アカウント全体）の両方に同じ形の data を返す。
    upstream = _Upstream(monkeypatch, httpx.Response(200, json={"data": {
        "limit": 20, "limit_remaining": 12.5, "usage": 7.5, "usage_monthly": 3.25, "is_free_tier": False,
        "free_model_daily_requests": {"used": 3, "limit": 1000, "remaining": 997},
        "total_credits": 20, "total_usage": 16.93}}))
    result = asyncio.run(cloud.account("openrouter"))
    assert result["limit_remaining"] == 12.5 and result["usage_monthly"] == 3.25 and result["free_requests_remaining"] == 997
    assert result["balance"] == 3.07 and result["credits_total"] == 20
    assert [str(r.url) for r in upstream.sent] == ["https://openrouter.ai/api/v1/key", "https://openrouter.ai/api/v1/credits"]
    assert asyncio.run(cloud.account("openai")) == {"supported": False}


async def _async_value(value):
    return value


def _async(value):
    return _async_value(value)


# ---- 賢さ（パラメータ数）・対応機能・提供元（OpenRouter の絞り込み） ----


@pytest.mark.parametrize("names, expected", [
    (("meta-llama/llama-3.1-8b-instruct",), (8.0, None)),
    (("mistralai/mixtral-8x7b-instruct",), (56.0, None)),
    (("qwen/qwen3-30b-a3b",), (30.0, 3.0)),
    (("deepseek/deepseek-r1-distill-llama-70b",), (70.0, None)),
    (("google/gemma-3n-e4b-it",), (4.0, None)),
    (("HuggingFaceTB/SmolLM2-135M",), (0.135, None)),
    (("meta-llama/llama-4-maverick-17b-128e-instruct",), (17.0, None)),
    (("liquid/lfm-1.2b",), (1.2, None)),
    # 公開されていない・名前に書かれていないものは不明。似た文字列（128k・4bit・o3）を数えない。
    (("anthropic/claude-3.5-sonnet",), (None, None)),
    (("openai/gpt-4o-mini",), (None, None)),
    (("microsoft/phi-3.5-mini-128k-instruct",), (None, None)),
    (("x/model-4bit",), (None, None)),
    (("openai/o3",), (None, None)),
    # hugging_face_id → id → 表示名の順に見る。
    (("", "vendor/sonoma", "Sonoma 405B"), (405.0, None)),
    (("org/Model-72B", "vendor/model", "Model"), (72.0, None)),
])
def test_parameter_count_is_read_from_the_name(names, expected):
    assert cloud.parameter_count(*names) == expected


def test_remote_models_carry_size_capabilities_and_vendor(store, monkeypatch):
    import asyncio

    _configure()
    monkeypatch.setattr(cloud, "_zdr_models", lambda: _async(set()))
    _Upstream(monkeypatch, httpx.Response(200, json={"data": [
        {"id": "qwen/qwen3-30b-a3b", "name": "Qwen3 30B A3B", "hugging_face_id": "Qwen/Qwen3-30B-A3B",
         "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
         "supported_parameters": ["tools", "reasoning", "temperature"], "context_length": 131072},
        {"id": "anthropic/claude-x", "name": "Claude X", "architecture": {"modality": "text+image->text"},
         "supported_parameters": ["tools", "include_reasoning"]},
        {"id": "plain-model", "name": "Plain", "supported_parameters": "broken"},
    ]}))
    rows = {row["id"]: row for row in asyncio.run(cloud.list_remote_models("openrouter"))}
    qwen = rows["qwen/qwen3-30b-a3b"]
    assert (qwen["vendor"], qwen["params_b"], qwen["active_params_b"]) == ("qwen", 30.0, 3.0)
    assert qwen["modalities"] == ["image", "text"] and qwen["tools"] is True and qwen["reasoning"] is True
    claude = rows["anthropic/claude-x"]
    assert claude["params_b"] is None and claude["modalities"] == ["image", "text"]
    assert claude["tools"] is True and claude["reasoning"] is True
    plain = rows["plain-model"]
    assert (plain["vendor"], plain["params_b"], plain["modalities"], plain["tools"], plain["reasoning"]) == ("", None, [], False, False)


def test_other_providers_do_not_get_openrouter_only_fields(store, monkeypatch):
    import asyncio

    _configure(provider="openai", models=("gpt-x",))
    _Upstream(monkeypatch, httpx.Response(200, json={"data": [{"id": "gpt-x"}]}))
    row = asyncio.run(cloud.list_remote_models("openai"))[0]
    assert "params_b" not in row and row["privacy"] == "direct"


# ---- AI アシスタント・OpenCode の役割から外部モデルを選ぶ ----


def test_models_for_a_caller_follow_the_egress_switch(store):
    _configure()
    assert cloud.models_for("assistant") == [] and not cloud.usable("cloud:openrouter/anthropic/claude-x", caller="assistant")
    cloud.save_egress({"assistant": True})
    assert [item["id"] for item in cloud.models_for("assistant")] == ["cloud:openrouter/anthropic/claude-x"]
    assert cloud.usable("cloud:openrouter/anthropic/claude-x", caller="assistant")
    assert cloud.models_for("opencode") == [], "許可は呼び出し元ごと"
    assert cloud.models_for("unknown") == []


def _providers_listing(monkeypatch, *, runtime, local):
    import asyncio

    from app.models_mgmt import llama, local_llm, providers

    async def candidates():
        return []

    monkeypatch.setattr(providers, "_candidates", candidates)
    monkeypatch.setattr(providers, "_selected_runtime", lambda: runtime)
    monkeypatch.setattr(llama, "list_instances", lambda: list(local))
    monkeypatch.setattr(local_llm, "llm_instances", lambda **kw: list(local))
    return asyncio.run(providers.list_providers(include_gateway=True))


def test_assistant_model_list_includes_cloud_models_only_when_allowed(store, monkeypatch):
    local = [{"alias": "chat", "role": "llm", "port": 8090}]
    _configure()
    listed = _providers_listing(monkeypatch, runtime="llama.cpp", local=local)[0]
    assert listed["models"] == ["auto", "chat"] and listed["external_models"] == []
    cloud.save_egress({"opencode": True})
    assert _providers_listing(monkeypatch, runtime="llama.cpp", local=local)[0]["models"] == ["auto", "chat"], \
        "OpenCode への許可は AI アシスタントには効かない"
    cloud.save_egress({"assistant": True})
    listed = _providers_listing(monkeypatch, runtime="llama.cpp", local=local)[0]
    # ローカルが先、外部は後ろ。画面が見分けられるよう名前とプロバイダを別に渡す。
    assert listed["models"] == ["auto", "chat", "cloud:openrouter/anthropic/claude-x"]
    assert listed["external_models"] == [{"id": "cloud:openrouter/anthropic/claude-x",
                                          "name": "OpenRouter / anthropic/claude-x", "provider": "openrouter"}]
    assert listed["selected"] is True


def test_assistant_gets_a_gateway_entry_for_cloud_models_under_ollama(store, monkeypatch):
    _configure()
    assert _providers_listing(monkeypatch, runtime="ollama", local=[]) == []
    cloud.save_egress({"assistant": True})
    listed = _providers_listing(monkeypatch, runtime="ollama", local=[])
    assert [item["id"] for item in listed] == ["control-deck-gateway"]
    # 既定の接続先にはしない（外部への送信は利用者が自分で選んだときだけ）。
    assert listed[0]["selected"] is False and listed[0]["models"] == ["cloud:openrouter/anthropic/claude-x"]
    assert listed[0]["base_url"].endswith("/api/v1/llm/v1")


def test_internal_generation_keeps_cloud_models_on_the_gateway():
    from app.models_mgmt import gateway

    base = gateway.base_url()
    assert gateway.resolve_internal_target(base, "cloud:openrouter/anthropic/claude-x") == (base, "cloud:openrouter/anthropic/claude-x")
    assert gateway.client_headers(base, "assistant") == {gateway.CLIENT_HEADER: "assistant"}
    assert gateway.client_headers(base, "workflows") == {gateway.CLIENT_HEADER: "workflows"}
    assert gateway.client_headers("https://api.example.com/v1", "assistant") == {}, "外部の endpoint へは名乗らない"


def _mock_runtime_http(monkeypatch, handler):
    from app.models_mgmt import runtime_provider as rp

    real = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(rp.httpx, "AsyncClient", lambda *a, **kw: real(*a, **{**kw, "transport": transport}))

    async def nothing(self, request):
        return None

    monkeypatch.setattr(rp.LlmRuntimeProvider, "_prepare", nothing)
    return rp


def test_assistant_requests_name_their_caller_to_the_gateway(monkeypatch):
    """チャット・構造化生成・Deep Research はどれも runtime provider を通る。ヘッダはそこで 1 回付ける。"""
    import asyncio

    from app.models_mgmt import gateway

    seen = []

    def handler(request):
        seen.append(request)
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, text='data: {"choices":[{"delta":{"reasoning":"考"}}]}\n\n'
                                           'data: {"choices":[{"delta":{"content":"答"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    rp = _mock_runtime_http(monkeypatch, handler)
    base, model = rp.resolve_target(gateway.base_url(), "cloud:openrouter/anthropic/claude-x")
    provider = rp.provider_for_base_url(base)
    request = rp.RuntimeChatRequest(base, model, [{"role": "user", "content": "x"}], client="assistant")

    async def run():
        text = await provider.complete(request)
        chunks = [chunk async for chunk in provider.stream_chat(request, request_id="cloud-test")]
        return text, chunks

    text, chunks = asyncio.run(run())
    assert text == "ok" and [(c.type, c.content) for c in chunks] == [("thinking", "考"), ("content", "答")]
    assert len(seen) == 2
    for sent in seen:
        assert sent.headers[gateway.CLIENT_HEADER] == "assistant"
        assert json.loads(sent.content)["model"] == "cloud:openrouter/anthropic/claude-x"
    # ゲートウェイ以外（利用者が指定した外部 endpoint）へは付けない。
    seen.clear()
    other = rp.RuntimeChatRequest("https://llm.example.com/v1", "m", [{"role": "user", "content": "x"}])
    asyncio.run(rp.OpenAICompatibleRuntimeProvider().complete(other))
    assert gateway.CLIENT_HEADER not in seen[0].headers


def test_gateway_refusal_reaches_the_assistant_as_a_readable_message(monkeypatch):
    import asyncio

    from app.models_mgmt import gateway

    rp = _mock_runtime_http(monkeypatch, lambda request: httpx.Response(
        403, json={"detail": "AI アシスタント からの外部モデルの利用は許可されていません"}))
    request = rp.RuntimeChatRequest(gateway.base_url(), "cloud:openrouter/anthropic/claude-x", [])
    provider = rp.OpenAICompatibleRuntimeProvider()

    async def stream():
        return [chunk async for chunk in provider.stream_chat(request, request_id="refused")]

    for call in (lambda: provider.complete(request), stream):
        with pytest.raises(rp.RuntimeProviderError, match="許可されていません"):
            asyncio.run(call())


def test_local_only_requests_never_use_a_cloud_model():
    import asyncio

    from app.models_mgmt import gateway, runtime_provider as rp

    request = rp.RuntimeChatRequest(gateway.base_url(), "cloud:openrouter/anthropic/claude-x", [], local_only=True)
    with pytest.raises(rp.RuntimeProviderError, match="external model"):
        asyncio.run(rp.OpenAICompatibleRuntimeProvider().complete(request))


def test_assistant_chat_reaches_the_provider_through_the_gateway(client, store, monkeypatch):
    """ゲートウェイは、AI アシスタントと名乗った要求だけを AI アシスタントの許可で通す。"""
    _configure()
    cloud.save_egress({"assistant": True})
    upstream = _Upstream(monkeypatch, httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": {"prompt_tokens": 1}}))
    body = {"model": "cloud:openrouter/anthropic/claude-x", "messages": [{"role": "user", "content": "x"}]}
    assert client.post("/api/v1/llm/v1/chat/completions", headers=_gateway_headers(), json=body).status_code == 403
    assert upstream.sent == [], "名乗らない要求は外部クライアント扱いで、許可が無ければ送らない"
    response = client.post("/api/v1/llm/v1/chat/completions", headers=_gateway_headers("assistant"), json=body)
    assert response.status_code == 200 and len(upstream.sent) == 1
    assert cloud.usage_rows()[0]["caller"] == "assistant"


def test_only_assistant_paths_claim_the_assistant_caller():
    """Add-ons and other internal callers must not borrow the assistant's egress permission."""
    from app.addon_runtime import ai
    from app.models_mgmt import gateway
    from app.models_mgmt.runtime_provider import RuntimeChatRequest, _request_headers

    assert RuntimeChatRequest(base_url="x", model="m", messages=[]).client == "internal"
    body = ai.RuntimeAIRequest(capability="text.generate", messages=[{"role": "user", "content": "hi"}])
    target = type("T", (), {"base_url": gateway.base_url(), "model": "cloud:openrouter/a/b"})()
    request = ai._runtime_request(body, target)
    headers = _request_headers(request)
    assert headers.get(gateway.CLIENT_HEADER) not in ("assistant", "opencode", "workflows")


def test_internal_requests_to_the_gateway_carry_its_key(monkeypatch):
    """The assistant reaches cloud models through the gateway, which needs its key (was 401)."""
    from app.models_mgmt import gateway
    from app.models_mgmt.runtime_provider import RuntimeChatRequest, _request_headers

    monkeypatch.setattr(gateway, "get_api_key", lambda create=False: "gw-key")
    base = gateway.base_url()
    headers = _request_headers(RuntimeChatRequest(base, "cloud:openrouter/a/b", [], client="assistant"))
    assert headers["Authorization"] == "Bearer gw-key"
    assert gateway.internal_api_key("http://127.0.0.1:8096/v1") == "sk-no-key", "直結のランタイムへは鍵を送らない"
    assert gateway.internal_api_key(base, "given") == "given"


def test_account_flags_a_management_key(store, monkeypatch):
    import asyncio

    _configure()
    _Upstream(monkeypatch, httpx.Response(200, json={"data": {"is_management_key": True, "limit": None}}))
    assert asyncio.run(cloud.account("openrouter"))["management_key"] is True


# ---- Home のまとめ ----


def test_summary_reports_running_calls_totals_and_speed(store, monkeypatch):
    _configure()
    cloud.save_egress({"opencode": True})
    target = cloud.resolve("cloud:openrouter/anthropic/claude-x", caller="opencode")
    clock = [100.0]
    monkeypatch.setattr(cloud.time, "monotonic", lambda: clock[0])
    timer = cloud.begin_call(target, "opencode")
    clock[0] = 101.0
    cloud.note_stream(timer, b"data: {}\n\ndata: {}\n\n")
    clock[0] = 102.0
    running = cloud.summary()["running"]
    assert running[0]["model"] == "anthropic/claude-x" and running[0]["phase"] == "generate"
    assert running[0]["tokens"] == 2 and running[0]["caller_label"] == "OpenCode"
    clock[0] = 103.0
    cloud.record_usage(target, "opencode", {"prompt_tokens": 100, "completion_tokens": 41, "cost": 0.01},
                       status_code=200, timer=timer)
    data = cloud.summary()
    assert data["running"] == []
    row = cloud.usage_rows()[0]
    assert row["ttft_s"] == 1.0 and row["duration_s"] == 3.0 and row["tokens_per_second"] == 20.0
    assert data["today"]["calls"] == 1 and data["today"]["cost_usd"] == 0.01
    assert data["month"]["completion_tokens"] == 41
    # 直前の呼び出し: 初回応答とコンテキストに入った量。長さはモデル一覧を取ったときに覚える。
    assert data["last"]["ttft_s"] == 1.0 and data["last"]["prompt_tokens"] == 100
    assert data["last"]["context_length"] is None
    cloud._context_lengths[("openrouter", "anthropic/claude-x")] = 200000
    assert cloud.summary()["last"]["context_length"] == 200000


def test_running_call_reports_time_to_first_token(store, monkeypatch):
    _configure()
    cloud.save_egress({"opencode": True})
    target = cloud.resolve("cloud:openrouter/anthropic/claude-x", caller="opencode")
    clock = [50.0]
    monkeypatch.setattr(cloud.time, "monotonic", lambda: clock[0])
    timer = cloud.begin_call(target, "opencode")
    assert cloud.summary()["running"][0]["ttft_s"] is None
    clock[0] = 51.5
    cloud.note_stream(timer, b"data: {}\n\n")
    assert cloud.summary()["running"][0]["ttft_s"] == 1.5
    cloud.record_usage(target, "opencode", None, status_code=200, timer=timer)


def test_context_lengths_are_fetched_at_most_hourly():
    cloud._context_fetch_at.clear()
    assert cloud.needs_context_lengths("openrouter", "x/unknown") is True
    assert cloud.needs_context_lengths("openrouter", "x/unknown") is False
    cloud._context_lengths[("openrouter", "x/known")] = 1000
    assert cloud.needs_context_lengths("openrouter", "x/known") is False


def test_summary_endpoint_requires_settings_manage(admin_client, store):
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/api/v1/models/cloud-summary").status_code in (401, 403)
    data = admin_client.get("/api/v1/models/cloud-summary").json()
    assert data["running"] == [] and data["today"]["calls"] == 0 and data["last"] is None


def _route_upstream(monkeypatch, routes):
    """URL ごとに返す応答を変えるモック。送った要求は sent に残る。routes の値が例外なら送出する。"""
    sent = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            sent.append(request)
            result = routes[str(request.url)]
            if isinstance(result, Exception):
                raise result
            return result

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": Transport()}))
    return sent


def _item(model_id, price="0.000001"):
    return {"id": model_id, "name": model_id, "pricing": {"prompt": price, "completion": price}}


_KEYED = "https://openrouter.ai/api/v1/models"


def test_excluded_models_are_counted_against_the_public_list(store, monkeypatch):
    """目的: アカウントの設定で除外されたモデルの数（うち無料）を、鍵なしの公開の一覧との差で出す。
    根拠: 利用者の言葉（2026-11-23）「フリーモデルを制限しないにしてるのに 200 件しか取れず、無料は 1 件」。"""
    import asyncio

    _configure()
    monkeypatch.setattr(cloud, "_zdr_models", lambda: _async_value(set()))
    # 同じ URL に鍵あり・鍵なしで違う応答を返すため、Authorization の有無で分ける。
    keyed = httpx.Response(200, json={"data": [_item("a/one"), _item("a/two")]})
    public = httpx.Response(200, json={"data": [
        _item("a/one"), _item("a/two"), _item("b/hidden"), _item("c/free:free", "0"), _item("~d/alias")]})
    sent = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            sent.append(request)
            return keyed if "authorization" in request.headers else public

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": Transport()}))
    report = asyncio.run(cloud.remote_models_report("openrouter"))
    assert [m["id"] for m in report["models"]] == ["a/one", "a/two"]
    assert report["excluded"]["count"] == 3 and report["excluded"]["free_count"] == 1
    assert {m["id"] for m in report["excluded"]["models"]} == {"b/hidden", "c/free:free", "~d/alias"}
    assert [("authorization" in r.headers) for r in sent] == [True, False], "公開の一覧には秘密を送らない"
    assert isinstance(report["fetched_at"], int)


def test_public_list_failure_hides_the_count_without_breaking(store, monkeypatch):
    """目的: 公開の一覧が取れなくても、モデルの一覧は返し、除外の件数だけを出さない。
    根拠: 利用者の依頼（2026-11-23）「公開の一覧が取れなければ件数は出さない（失敗で画面を壊さない）」。"""
    import asyncio

    _configure()
    monkeypatch.setattr(cloud, "_zdr_models", lambda: _async_value(set()))

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if "authorization" in request.headers:
                return httpx.Response(200, json={"data": [_item("a/one")]})
            raise httpx.ConnectError("down")

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": Transport()}))
    report = asyncio.run(cloud.remote_models_report("openrouter"))
    assert len(report["models"]) == 1 and report["excluded"] is None


def test_non_openrouter_has_no_excluded_report(store, monkeypatch):
    """目的: OpenRouter 以外では公開の一覧を問い合わせない。根拠: 依頼「OpenRouter のときだけ」（2026-11-23）。"""
    import asyncio

    _configure("openai", models=("gpt-x",))
    sent = _route_upstream(monkeypatch, {"https://api.openai.com/v1/models": httpx.Response(200, json={"data": [_item("gpt-x")]})})
    report = asyncio.run(cloud.remote_models_report("openai"))
    assert report["excluded"] is None and len(sent) == 1


def test_refresh_ignores_the_zdr_cache(store, monkeypatch):
    """目的: 取り直すときは ZDR 一覧の 1 時間の覚えを使わず、問い合わせ直す。
    根拠: 利用者の言葉（2026-11-23）「モデル設定を更新した。モデルリストが更新もされない」。"""
    import asyncio

    _configure()
    zdr_url = "https://openrouter.ai/api/v1/endpoints/zdr"
    sent = _route_upstream(monkeypatch, {
        _KEYED: httpx.Response(200, json={"data": [_item("a/one")]}),
        zdr_url: httpx.Response(200, json={"data": [{"model_id": "a/one"}]}),
    })
    monkeypatch.setattr(cloud, "_zdr_cache", (0.0, set()))
    asyncio.run(cloud.remote_models_report("openrouter"))
    asyncio.run(cloud.remote_models_report("openrouter"))
    assert sum(str(r.url) == zdr_url for r in sent) == 1, "通常は覚えを使う"
    asyncio.run(cloud.remote_models_report("openrouter", refresh=True))
    assert sum(str(r.url) == zdr_url for r in sent) == 2, "取り直しでは問い合わせ直す"
