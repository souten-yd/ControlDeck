"""OpenAI互換ゲートウェイ。

OpenCode のように ControlDeck を経由せず llama.cpp を直接叩くクライアントにも
KVの受け入れ制御を効かせるための層。認証はセッションCookieではなく専用APIキー。
"""
from __future__ import annotations

import asyncio
import json

from tests.conftest import CSRF_HEADERS


def _issue(admin_client) -> str:
    response = admin_client.post("/api/v1/models/llm-gateway/key", headers=CSRF_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["api_key"]


def test_key_is_issued_and_reported_with_base_url(admin_client):
    key = _issue(admin_client)
    assert key.startswith("cdk-")
    settings = admin_client.get("/api/v1/models/llm-gateway")
    assert settings.status_code == 200
    body = settings.json()
    assert body["issued"] is True
    assert body["api_key"] == key
    # OpenCode の base_url にそのまま入れられる形
    assert body["base_url"].endswith("/api/v1/llm/v1")


def test_rotate_invalidates_previous_key(admin_client):
    first = _issue(admin_client)
    rotated = admin_client.post("/api/v1/models/llm-gateway/key?rotate=true", headers=CSRF_HEADERS)
    assert rotated.status_code == 200
    second = rotated.json()["api_key"]
    assert second != first
    assert admin_client.get("/api/v1/models/llm-gateway").json()["api_key"] == second


def test_gateway_requires_bearer_key(admin_client):
    _issue(admin_client)
    # キー無し
    assert admin_client.get("/api/v1/llm/v1/models").status_code == 401
    # 誤ったキー
    bad = admin_client.get("/api/v1/llm/v1/models",
                           headers={"Authorization": "Bearer wrong-key"})
    assert bad.status_code == 401
    # Cookie 認証では通さない（OpenAI互換クライアントはCookieを持てない前提の設計）
    assert "Authorization" not in CSRF_HEADERS


def test_models_listing_returns_registered_llm_aliases(admin_client, monkeypatch):
    from app.models_mgmt import llama

    key = _issue(admin_client)
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "chat-model", "role": "llm", "port": 8090},
        {"alias": "embed", "role": "embedding", "port": 8094},
    ])
    response = admin_client.get("/api/v1/llm/v1/models",
                                headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    ids = [m["id"] for m in response.json()["data"]]
    # embedding はチャット先ではないので出さない。autoは転送先をControlDeckに任せる仮想モデル。
    assert ids == ["auto", "chat-model"]


def test_unknown_model_falls_back_to_highest_priority_llm(monkeypatch):
    from app.models_mgmt import gateway, llama

    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "top", "role": "llm", "port": 8090},
        {"alias": "second", "role": "llm", "port": 8091},
    ])
    # 指定があればそれ、無ければ一覧の先頭（＝優先度最上位）
    assert gateway._target_endpoint("second") == ("second", 8091)
    assert gateway._target_endpoint("") == ("top", 8090)
    assert gateway._target_endpoint("does-not-exist") == ("top", 8090)


def test_admission_waits_using_shared_capacity(monkeypatch):
    """ゲートウェイ経由でも await_capacity を通る（直結との差を無くすのが目的）。"""
    import asyncio

    from app.models_mgmt import gateway, llama, lucebox

    seen = {}

    async def _fake(port, needed, *, timeout_seconds):
        seen.update({"port": port, "needed": needed, "timeout": timeout_seconds})
        return {"available": True, "accepting": True}

    monkeypatch.setattr(llama, "await_capacity", _fake)
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "local", "role": "llm", "port": 8090, "loaded": True},
    ])
    monkeypatch.setattr(lucebox, "list_instances", list)
    payload = {"messages": [{"role": "user", "content": "x" * 400}], "max_tokens": 256}
    asyncio.run(gateway._admit("local", 8090, payload))
    assert seen["port"] == 8090
    # プロンプト概算(400/4=100) + 出力上限(256)
    assert seen["needed"] == 356
    assert seen["timeout"] == gateway.CAPACITY_TIMEOUT_SECONDS


def test_admission_skips_capacity_wait_for_lucebox(monkeypatch):
    """Luceboxは共有KVプールを持たない。待つ対象が無いので素通しする。

    ここで llama.cpp と同じ待ちを掛けると、存在しないメトリクスを読みに行って
    毎回タイムアウト分だけ生成開始が遅れる。
    """
    import asyncio

    from app.models_mgmt import gateway, llama, lucebox

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("Luceboxでllama.cppの容量待ちを呼んではいけない")

    monkeypatch.setattr(llama, "await_capacity", _must_not_run)
    monkeypatch.setattr(llama, "list_instances", list)
    monkeypatch.setattr(lucebox, "list_instances", lambda: [
        {"alias": "luce", "role": "llm", "port": 8216, "loaded": True, "runtime": "lucebox"},
    ])
    result = asyncio.run(gateway._admit("luce", 8216, {"messages": [], "max_tokens": 16}))
    assert result["ok"] is True


def test_direct_port_still_works_and_is_resolvable(monkeypatch, tmp_path):
    """8090 直結のままでも壊れない（ゲートウェイは任意）。

    直結だと受け入れ制御は通らないが、アイドル判定は従来どおり効く必要がある。
    """
    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    provider.save_settings({"base_url": "http://127.0.0.1:8090/v1", "model": "llama",
                            "use_gateway": False})
    assert provider.is_gateway_url("http://127.0.0.1:8090/v1") is False
    assert provider.resolve_backend_port() == 8090
    # 直結ではAPIキーを要求しない（従来の挙動を変えない）
    assert provider._api_key_for("http://127.0.0.1:8090/v1") == "sk-no-key"


def test_gateway_url_resolves_to_backend_port_for_idle_tracking(monkeypatch, tmp_path):
    """ゲートウェイ経由でも「どのモデルが使われているか」を引けること。

    引けないと、OpenCode を使っている最中にアイドル判定でモデルが落とされる。
    """
    from app.integrations.opencode import provider
    from app.models_mgmt import llama

    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "llama", "role": "llm", "port": 8090},
        {"alias": "other", "role": "llm", "port": 8091},
    ])
    provider.save_settings({"base_url": provider.gateway_base_url(), "model": "other",
                            "use_gateway": True})
    assert provider.is_gateway_url(provider.gateway_base_url()) is True
    # ControlDeck のポートではなく、転送先の llama ポートが返る
    assert provider.resolve_backend_port() == 8091


def test_autoconfigure_sets_everything_needed_to_connect(monkeypatch, tmp_path):
    """導入直後に手作業なしで繋がること。"""
    from app.integrations.opencode import provider
    from app.models_mgmt import gateway, llama

    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(gateway, "_path", lambda: tmp_path / "gw.json")
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "top", "role": "llm", "port": 8090},
    ])
    settings = provider.autoconfigure()
    assert settings["base_url"] == provider.gateway_base_url()
    assert settings["model"] == "auto"      # 既定はモデル固定なし（転送先はゲートウェイが決める）
    assert settings["use_gateway"] is True
    # APIキーが発行され、runtime config へ載る
    key = gateway.get_api_key()
    assert key.startswith("cdk-")
    assert provider._api_key_for(settings["base_url"]) == key


def test_install_autoconfigures_opencode_connection(monkeypatch, tmp_path):
    """アドオン導入だけで通信できる状態になること（手作業を残さない）。"""
    from app.features import registry
    from app.integrations.opencode import provider
    from app.models_mgmt import gateway, llama

    monkeypatch.setattr(provider, "_settings_path", lambda: tmp_path / "s.json")
    monkeypatch.setattr(gateway, "_path", lambda: tmp_path / "gw.json")
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "llama", "role": "llm", "port": 8090},
    ])
    monkeypatch.setattr(registry, "_install_package",
                        lambda feature_id, latest: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(registry, "_managed_executable",
                        lambda feature_id: type("P", (), {"is_file": lambda self: True})())
    monkeypatch.setattr(registry, "status", lambda feature_id: {"installed": True})

    registry.install("opencode")
    settings = provider.get_settings()
    assert settings["base_url"] == provider.gateway_base_url()
    assert settings["model"] == "auto"
    assert gateway.get_api_key().startswith("cdk-")


def test_autoconfigure_failure_does_not_break_install(monkeypatch, tmp_path):
    """自動設定に失敗しても導入自体は成功させる（後から設定画面で直せる）。"""
    from app.features import registry

    monkeypatch.setattr(registry, "_install_package",
                        lambda feature_id, latest: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(registry, "_managed_executable",
                        lambda feature_id: type("P", (), {"is_file": lambda self: True})())
    monkeypatch.setattr(registry, "status", lambda feature_id: {"installed": True})

    def _boom():
        raise RuntimeError("設定できません")

    monkeypatch.setattr("app.integrations.opencode.provider.autoconfigure", _boom)
    assert registry.install("opencode") == {"installed": True}


def test_default_target_prefers_loaded_endpoint(monkeypatch):
    """未指定時は起動中のエンドポイントを優先する。

    停止中の別モデルを起こすと同じGPUへ二重にロードすることになり、稼働中の
    エンドポイントまでVRAM不足で巻き込む。
    """
    from app.models_mgmt import gateway, llama

    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "top", "role": "llm", "port": 8090, "loaded": False},
        {"alias": "running", "role": "llm", "port": 8091, "loaded": True},
    ])
    assert gateway.resolve_endpoint("") == ("running", 8091)
    assert gateway.resolve_endpoint("does-not-exist") == ("running", 8091)
    # 明示指定は従来どおりそのモデル（必要ならオンデマンド起動される）
    assert gateway.resolve_endpoint("top") == ("top", 8090)


def test_internal_calls_resolve_gateway_url_to_real_endpoint(monkeypatch):
    """内部の生成はゲートウェイへHTTPで戻らず、同じ規則で実エンドポイントを叩く。"""
    from app.models_mgmt import gateway, llama
    from app.models_mgmt.runtime_provider import resolve_target

    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "top", "role": "llm", "port": 8090, "loaded": False},
        {"alias": "running", "role": "llm", "port": 8091, "loaded": True},
    ])
    assert resolve_target(gateway.base_url(), "top") == ("http://127.0.0.1:8090/v1", "top")
    assert resolve_target(gateway.base_url(), "") == ("http://127.0.0.1:8091/v1", "running")
    # ゲートウェイ以外の接続先は素通し
    assert resolve_target("http://127.0.0.1:11434/v1", "qwen3") == (
        "http://127.0.0.1:11434/v1", "qwen3")


def test_provider_selection_follows_resolved_endpoint(monkeypatch):
    """ゲートウェイ宛のrequestは解決後のllama.cpp providerで処理される。"""
    from app.models_mgmt import gateway, llama
    from app.models_mgmt.runtime_provider import RuntimeChatRequest, provider_for_request

    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "running", "role": "llm", "port": 8091, "loaded": True},
    ])
    monkeypatch.setattr(llama, "endpoint_ports", lambda: {8091})
    request = RuntimeChatRequest(base_url=gateway.base_url(), model="", messages=[])
    provider = provider_for_request(request)
    assert provider.kind == "llama.cpp"
    assert request.base_url == "http://127.0.0.1:8091/v1"
    assert request.model == "running"


def test_auto_model_follows_the_running_endpoint(monkeypatch):
    """autoは「今動いているモデル」へ流す。クライアント側のモデル固定を外すための逃げ道。"""
    from app.models_mgmt import gateway, llama

    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "top", "role": "llm", "port": 8090, "loaded": False},
        {"alias": "running", "role": "llm", "port": 8091, "loaded": True},
    ])
    assert gateway.resolve_endpoint(gateway.AUTO_MODEL) == ("running", 8091)
    # 停止中しかなければ登録順の先頭を起こす
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "top", "role": "llm", "port": 8090, "loaded": False},
    ])
    assert gateway.resolve_endpoint(gateway.AUTO_MODEL) == ("top", 8090)


def test_stream_failure_is_reported_instead_of_an_empty_ok_stream(admin_client, monkeypatch):
    """転送先の失敗を 200 の空ストリームにしない。

    ストリームは本文を流し始める前に応答行が決まるため、status を見ずに中継すると
    エラーが「中身の無い成功」として届く。OpenAI互換クライアントはそれを空応答と
    見なして同じ要求を再送し続け、原因も見えないまま GPU を焼き続ける
    （llama.cpp の grammar エラーで実際に起きた）。
    """
    import httpx

    from app.models_mgmt import gateway, llama

    key = _issue(admin_client)
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "local", "role": "llm", "port": 8090, "loaded": True},
    ])

    async def _ready(alias, timeout_seconds=180):
        return True

    async def _admit(alias, port, payload):
        return {"accepting": True}

    async def _lease(alias, request):
        return object(), "lease-1", asyncio.get_running_loop().create_future()

    async def _release(adapter, lease_id, renew):
        renew.cancel()

    monkeypatch.setattr(llama, "ensure_ready", _ready)
    monkeypatch.setattr(gateway, "_admit", _admit)
    monkeypatch.setattr(gateway, "_acquire_gateway_lease", _lease)
    monkeypatch.setattr(gateway, "_release_gateway_lease", _release)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {
            "code": 400, "type": "invalid_request_error",
            "message": "Failed to initialize samplers: failed to parse grammar",
        }})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        gateway.httpx, "AsyncClient",
        lambda **kwargs: real_client(**{**kwargs, "transport": transport}),
    )

    response = admin_client.post(
        "/api/v1/llm/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "local", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 400
    # どのモデルが何で失敗したかが、そのままクライアントの表示になる。
    assert "failed to parse grammar" in response.json()["error"]["message"]
    assert response.json()["error"]["message"].startswith("local: ")


def test_upstream_error_keeps_the_reason_and_bounds_the_body():
    """転送先の本文はそのまま渡すが、上限は付ける（文法全体が返ることがある）。"""
    from app.models_mgmt import gateway

    structured = gateway._upstream_error("local", json.dumps({
        "error": {"code": 400, "type": "invalid_request_error", "message": "no slot"},
    }).encode())
    assert structured["error"]["message"] == "local: no slot"
    assert structured["error"]["type"] == "invalid_request_error"

    plain = gateway._upstream_error("local", b"x" * 50_000)
    assert plain["error"]["type"] == "upstream_error"
    assert len(plain["error"]["message"]) <= gateway.UPSTREAM_ERROR_CHARS + len("local: ")
