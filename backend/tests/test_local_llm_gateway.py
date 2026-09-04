"""ゲートウェイから見た llama.cpp / Lucebox の統合（OpenCode の接続先）。"""
import asyncio

import pytest

from app.models_mgmt import gateway, llama, local_llm, lucebox

CSRF = {"X-Requested-With": "ControlDeck"}


@pytest.fixture()
def two_runtimes(monkeypatch):
    """llama.cpp と Lucebox に1件ずつ登録された状態。"""
    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "qwen-llama", "role": "llm", "port": 8080, "loaded": False,
         "model_path": "/m/a.gguf", "order": 1},
    ])
    monkeypatch.setattr(lucebox, "list_instances", lambda: [
        {"alias": "qwen-luce", "role": "llm", "port": 8216, "loaded": True, "runtime": "lucebox",
         "model_path": "/m/t.gguf", "draft_path": "/m/d.gguf", "order": 1},
    ])
    return None


def test_local_llm_lists_both_runtimes_with_a_runtime_tag(two_runtimes):
    listed = local_llm.llm_instances()
    assert [(item["alias"], item["runtime"]) for item in listed] == [
        ("qwen-llama", "llama.cpp"), ("qwen-luce", "lucebox"),
    ]
    assert local_llm.runtime_of("qwen-luce") == "lucebox"
    assert local_llm.endpoint_ports() == {8080, 8216}


def test_residency_keys_are_namespaced_per_runtime(two_runtimes):
    """同じGGUFでもランタイムが違えばVRAM占有は別物。実測キーを混ぜない。"""
    llama_key = local_llm.residency_key({"runtime": "llama.cpp", "model_path": "/m/x.gguf"})
    lucebox_key = local_llm.residency_key({"runtime": "lucebox", "model_path": "/m/x.gguf"})
    assert llama_key.startswith("llama:") and lucebox_key.startswith("lucebox:")
    assert llama_key != lucebox_key


def test_gateway_resolves_a_lucebox_alias_to_its_port(two_runtimes):
    assert gateway.resolve_endpoint("qwen-luce") == ("qwen-luce", 8216)
    assert gateway.resolve_endpoint("qwen-llama") == ("qwen-llama", 8080)


def test_gateway_auto_prefers_a_running_model_across_runtimes(two_runtimes):
    """AUTO は起動中を優先する。停止中を起こすと同じGPUへ二重にロードされる。"""
    assert gateway.resolve_endpoint(gateway.AUTO_MODEL) == ("qwen-luce", 8216)


def test_gateway_model_list_marks_the_runtime(admin_client, two_runtimes):
    key = admin_client.post("/api/v1/models/llm-gateway/key", json={}, headers=CSRF).json()["api_key"]
    response = admin_client.get("/api/v1/llm/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    data = {item["id"]: item for item in response.json()["data"]}
    # OpenCode はここに並ぶモデル名だけを見る。auto を含めて両ランタイムが1アドレスに出る。
    assert set(data) == {"auto", "qwen-llama", "qwen-luce"}
    assert data["qwen-luce"]["runtime"] == "lucebox"
    assert data["qwen-llama"]["runtime"] == "llama.cpp"


def test_runtime_provider_selects_the_lucebox_adapter(monkeypatch, two_runtimes):
    from app.models_mgmt import runtime_provider as rp

    monkeypatch.setattr(llama, "endpoint_ports", lambda: {8080})
    monkeypatch.setattr(lucebox, "endpoint_ports", lambda: {8216})
    assert rp.provider_for_base_url("http://127.0.0.1:8216/v1").kind == "lucebox"
    assert rp.provider_for_base_url("http://127.0.0.1:8080/v1").kind == "llama.cpp"
    assert rp.provider_for_base_url("http://example.com/v1").kind == "openai-compatible"


def test_opencode_autoconfigure_points_at_the_gateway_with_auto(monkeypatch, two_runtimes, tmp_path):
    from app.integrations.opencode import provider

    saved = {}
    monkeypatch.setattr(provider, "save_settings", lambda patch: saved.update(patch) or saved)
    monkeypatch.setattr(provider, "gateway_base_url", lambda: "http://127.0.0.1:8765/api/v1/llm/v1")
    monkeypatch.setattr(gateway, "get_api_key", lambda *, create=False: "cdk-test")
    provider.autoconfigure()
    # モデルを名指しせず auto を渡す。どちらのランタイムへ流すかはゲートウェイが決める。
    assert saved["model"] == gateway.AUTO_MODEL
    assert saved["use_gateway"] is True
    del tmp_path


def test_ensure_ready_dispatches_to_the_owning_runtime(monkeypatch, two_runtimes):
    called = {}

    async def _llama_ready(alias, *, timeout_seconds=240):
        called["llama"] = alias
        return True

    async def _lucebox_ready(alias, *, timeout_seconds=300):
        called["lucebox"] = alias
        return True

    monkeypatch.setattr(llama, "ensure_ready", _llama_ready)
    monkeypatch.setattr(lucebox, "ensure_ready", _lucebox_ready)
    assert asyncio.run(local_llm.ensure_ready("qwen-luce")) is True
    assert called == {"lucebox": "qwen-luce"}
    assert asyncio.run(local_llm.ensure_ready("qwen-llama")) is True
    assert called["llama"] == "qwen-llama"
    # 未登録のモデル名で他ランタイムを誤って起こさない。
    assert asyncio.run(local_llm.ensure_ready("missing")) is False


def test_providers_catalog_exposes_lucebox_as_managed(monkeypatch, two_runtimes):
    from app.models_mgmt import providers

    monkeypatch.setattr(lucebox, "runtime_status", lambda: {
        "installed": True, "track": "rocm10", "track_label": "ROCm 10",
        "instances": lucebox.list_instances(),
    })
    monkeypatch.setattr(llama, "runtime_status", lambda: {
        "installed": True, "base_url": "http://127.0.0.1:8080/v1", "port": 8080,
        "instances": llama.list_instances(),
    })
    catalog = asyncio.run(providers._candidates())
    managed = {item["id"] for item in catalog if item["managed"]}
    assert "lucebox" in managed and "llama.cpp" in managed
    assert providers.capabilities("lucebox", managed=True) == providers.capabilities("llama.cpp", managed=True)


def test_gateway_pins_temperature_for_lucebox_only(monkeypatch, tmp_path):
    """OpenCode等の外部クライアントはDFlash2の制約を知らない。ゲートウェイで揃える。"""
    monkeypatch.setattr(lucebox, "_config_path", lambda: tmp_path / "lucebox-runtime.json")
    monkeypatch.setattr(lucebox, "_runtime_state", lambda _alias: {"status": "STOPPED"})
    monkeypatch.setattr(lucebox, "_sync_auto_start", lambda _alias: None)
    lucebox.save_instance("luce", {"model_path": "/m/t.gguf", "port": 8216})

    assert local_llm.pins_greedy_sampling("luce") is True
    # llama.cpp のモデルには触らない（共有KVでサンプリングを潰す理由がない）。
    assert local_llm.pins_greedy_sampling("qwen-llama") is False
    # 個別設定で切れば、呼び出し側のtemperatureをそのまま通す。
    lucebox.save_instance("luce", {"prefer_speculative": False})
    assert local_llm.pins_greedy_sampling("luce") is False


class _FakeRequest:
    """headers だけ持つ最小の Request。_greedy_sampling_for はこれしか見ない。"""

    def __init__(self, headers=None):
        from starlette.datastructures import Headers

        self.headers = Headers(headers or {})


@pytest.fixture()
def lucebox_config(monkeypatch, tmp_path):
    monkeypatch.setattr(lucebox, "_config_path", lambda: tmp_path / "lucebox-runtime.json")
    monkeypatch.setattr(lucebox, "_runtime_state", lambda _alias: {"status": "STOPPED"})
    monkeypatch.setattr(lucebox, "_sync_auto_start", lambda _alias: None)
    return lucebox


OPENCODE_HEADERS = {gateway.CLIENT_HEADER: gateway.OPENCODE_CLIENT}


def test_sampling_policy_comes_from_the_instance_setting_only(lucebox_config):
    """クライアントで上書きしない。

    以前は「OpenCodeが停止中のモデルを起こすなら投機ON」を優先していたが、
    greedy固定でエージェントが同じツール呼び出しを繰り返して止まらなくなった。
    速度の利得よりループの害が重いので、個別設定だけで決める。
    """
    lucebox_config.save_instance("luce", {"model_path": "/m/t.gguf", "prefer_speculative": False})
    stopped = {"alias": "luce", "port": 8216, "loaded": False, "runtime": "lucebox"}
    running = {"alias": "luce", "port": 8216, "loaded": True, "runtime": "lucebox"}
    for instance in (stopped, running):
        assert gateway._greedy_sampling_for(instance, _FakeRequest(OPENCODE_HEADERS)) is False
        assert gateway._greedy_sampling_for(instance, _FakeRequest()) is False

    lucebox_config.save_instance("luce", {"prefer_speculative": True})
    for instance in (stopped, running):
        assert gateway._greedy_sampling_for(instance, _FakeRequest(OPENCODE_HEADERS)) is True
        assert gateway._greedy_sampling_for(instance, _FakeRequest()) is True


def test_opencode_does_not_change_llama_cpp_sampling(lucebox_config):
    """llama.cppは温度に依存せず投機が効く。サンプリングを潰す理由が無い。"""
    stopped = {"alias": "qwen-llama", "port": 8080, "loaded": False, "runtime": "llama.cpp"}
    assert gateway._greedy_sampling_for(stopped, _FakeRequest(OPENCODE_HEADERS)) is False


def test_opencode_runtime_config_carries_the_client_header(tmp_path, monkeypatch):
    """識別ヘッダ自体は診断用に残す（サンプリングの判断には使わない）。"""
    import json
    from pathlib import Path

    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path)
    monkeypatch.setattr(provider, "_api_key_for", lambda _base: "cdk-test")
    path = provider._runtime_config("job1", "http://127.0.0.1:8765/api/v1/llm/v1", "auto")
    options = json.loads(Path(path).read_text(encoding="utf-8"))["provider"]["controldeck"]["options"]
    assert options["headers"] == {gateway.CLIENT_HEADER: gateway.OPENCODE_CLIENT}
