"""思考（reasoning）のモデル個別設定。"""
from __future__ import annotations

import pytest

from app.models_mgmt import thinking


@pytest.mark.parametrize(("value", "mode"), [
    ("auto", "auto"), ("off", "off"), ("low", "low"), ("medium", "medium"),
    ("high", "high"), ("xhigh", "xhigh"), ("custom", "custom"),
    # 旧語彙の読み替え
    ("", "auto"), (None, "auto"), ("on", "high"), ("max", "xhigh"),
    ("true", "high"), ("false", "off"), (True, "high"), (False, "off"),
    # 未知の値は既定（auto）へ倒す
    ("nonsense", "auto"),
])
def test_normalize_mode_accepts_current_and_legacy_vocabulary(value, mode):
    assert thinking.normalize_mode(value) == mode


def test_budget_only_applies_to_custom():
    assert thinking.spec("high", 999).budget_tokens == 0
    assert thinking.spec("custom", 999).budget_tokens == 999
    # 範囲外はクランプする
    assert thinking.spec("custom", 0).budget_tokens == 0     # 0 は未指定扱い
    assert thinking.spec("custom", 10**9).budget_tokens == thinking.MAX_THINK_BUDGET


def test_levels_map_to_budgets():
    assert thinking.effective_budget(thinking.spec("off")) == 0
    assert thinking.effective_budget(thinking.spec("low")) == 1024
    assert thinking.effective_budget(thinking.spec("medium")) == 4096
    assert thinking.effective_budget(thinking.spec("high")) == 16384
    assert thinking.effective_budget(thinking.spec("xhigh")) == 32768
    # auto は無制限（llama.cpp の -1）
    assert thinking.effective_budget(thinking.spec("auto")) == -1
    # custom はそのまま
    assert thinking.effective_budget(thinking.spec("custom", 2000)) == 2000


def test_custom_budget_rounds_to_nearest_level_for_level_only_runtimes():
    """Ollama / OpenAI互換はバジェット非対応なので、最も近いレベルへ落とす。"""
    assert thinking.spec("custom", 1100).reasoning_effort == "low"
    assert thinking.spec("custom", 5000).reasoning_effort == "medium"
    assert thinking.spec("custom", 30000).reasoning_effort == "xhigh"
    # Ollama は xhigh を解釈しないので high へ寄せる
    assert thinking.spec("xhigh").ollama_think == "high"
    assert thinking.spec("custom", 30000).ollama_think == "high"


def test_auto_sends_nothing_and_off_disables():
    assert thinking.spec("auto").enabled is None
    assert thinking.spec("auto").ollama_think is None
    assert thinking.spec("auto").reasoning_effort is None
    assert thinking.spec("off").enabled is False
    assert thinking.spec("off").ollama_think is False
    # off に思考強度は無い（強度を送ると有効化と矛盾する）
    assert thinking.spec("off").reasoning_effort is None


def test_llama_unit_args_reflect_per_model_think(monkeypatch, tmp_path):
    """llama.cpp では instance の CLI 引数になる。"""
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "think.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    monkeypatch.setattr(llama, "server_path", lambda: tmp_path / "llama-server")
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"GGUF")

    llama.save_instance("m", {"alias": "m", "model_path": str(gguf), "port": 9400,
                              "think": "high"})
    content = llama._unit_content("m")
    assert '"--reasoning" "on"' in content
    assert '"--reasoning-budget" "16384"' in content

    llama.save_instance("m", {"think": "off"})
    content = llama._unit_content("m")
    assert '"--reasoning" "off"' in content
    assert "--reasoning-budget" not in content

    # auto は何も指定しない（モデル既定に任せる）
    llama.save_instance("m", {"think": "auto"})
    assert "--reasoning" not in llama._unit_content("m")

    llama.save_instance("m", {"think": "custom", "think_budget_tokens": 2500})
    assert '"--reasoning-budget" "2500"' in llama._unit_content("m")


def test_embedding_role_does_not_get_reasoning_args(monkeypatch, tmp_path):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "emb.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    monkeypatch.setattr(llama, "server_path", lambda: tmp_path / "llama-server")
    gguf = tmp_path / "e.gguf"
    gguf.write_bytes(b"GGUF")
    llama.save_instance("e", {"alias": "e", "model_path": str(gguf), "port": 9401,
                              "role": "embedding", "think": "high"})
    assert "--reasoning" not in llama._unit_content("e")


def test_resolve_reads_llama_instance_by_model_name(monkeypatch, tmp_path):
    """同一ポートに複数モデルが載るため、モデル名（alias）優先で引く。"""
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "resolve.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    llama.save_instance("a", {"alias": "a", "model_path": "/m/a.gguf", "port": 9402,
                              "think": "low"})
    llama.save_instance("b", {"alias": "b", "model_path": "/m/b.gguf", "port": 9402,
                              "think": "xhigh"})
    assert thinking.resolve("http://127.0.0.1:9402/v1", "a").mode == "low"
    assert thinking.resolve("http://127.0.0.1:9402/v1", "b").mode == "xhigh"


def test_resolve_reads_ollama_model_config(monkeypatch):
    from app.models_mgmt import ollama

    monkeypatch.setattr(ollama, "base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(ollama, "get_model_config",
                        lambda model: {"think": "custom", "think_budget_tokens": 3000})
    resolved = thinking.resolve("http://127.0.0.1:11434/v1", "qwen")
    assert resolved.mode == "custom"
    assert resolved.budget_tokens == 3000


def test_resolve_unknown_endpoint_defaults_to_auto():
    assert thinking.resolve("https://external.example/v1", "gpt").mode == "auto"


def test_shared_policy_no_longer_carries_reasoning(tmp_path, monkeypatch):
    """共通設定から think を外した。旧キーが残ったJSONもそのまま読める。"""
    from app.models_mgmt import runtime_policy

    assert not hasattr(runtime_policy.ChatDefaults(), "reasoning")
    path = tmp_path / "model-runtime-policy.json"
    path.write_text('{"chat": {"reasoning": "on", "timeout_seconds": 120}}', encoding="utf-8")
    monkeypatch.setattr(runtime_policy, "_path", lambda: path)
    policy = runtime_policy.get_policy()
    assert policy.chat.timeout_seconds == 120


def test_openai_payload_carries_reasoning_effort():
    from app.models_mgmt.runtime_provider import (
        OpenAICompatibleRuntimeProvider, RuntimeChatRequest,
    )

    provider = OpenAICompatibleRuntimeProvider()
    request = RuntimeChatRequest(
        base_url="https://external.example/v1", model="m", messages=[],
        thinking="high", reasoning_effort="high",
    )
    payload = provider._payload(request, stream=False)
    assert payload["reasoning_effort"] == "high"
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}

    # 構造化出力などで思考を止めるときは強度も送らない
    off = RuntimeChatRequest(
        base_url="https://external.example/v1", model="m", messages=[],
        disable_thinking=True, reasoning_effort="high",
    )
    payload_off = provider._payload(off, stream=False)
    assert "reasoning_effort" not in payload_off
    assert payload_off["chat_template_kwargs"] == {"enable_thinking": False}


def test_llama_instance_api_accepts_think(admin_client, monkeypatch):
    from app.models_mgmt import llama
    from tests.conftest import CSRF_HEADERS, _sandbox

    gguf = _sandbox / "think-api.gguf"
    gguf.write_bytes(b"GGUF")
    monkeypatch.setattr(llama, "_config_path", lambda: _sandbox / "think-api.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)

    created = admin_client.post("/api/v1/models/llama/instances", json={
        "alias": "think-model", "model_path": str(gguf), "port": 9403,
        "think": "xhigh",
    }, headers=CSRF_HEADERS)
    assert created.status_code == 201, created.text
    assert created.json()["instances"]["think-model"]["think"] == "xhigh"

    bad = admin_client.put("/api/v1/models/llama/instances/think-model", json={
        "think": "ultra",
    }, headers=CSRF_HEADERS)
    assert bad.status_code == 422


def test_migration_moves_shared_reasoning_to_each_model(tmp_path, monkeypatch):
    """共通設定を消しただけだと体感が変わるので、旧値を個別未設定のモデルへ落とす。"""
    import json

    from app.models_mgmt import llama, ollama, runtime_policy

    policy_path = tmp_path / "model-runtime-policy.json"
    policy_path.write_text(json.dumps({"chat": {"reasoning": "off", "timeout_seconds": 300}}),
                           encoding="utf-8")
    monkeypatch.setattr(runtime_policy, "_path", lambda: policy_path)
    monkeypatch.setattr(llama, "_config_path", lambda: tmp_path / "llama.json")
    monkeypatch.setattr(llama, "is_installed", lambda: False)
    monkeypatch.setattr(ollama, "_settings_path", lambda: tmp_path / "ollama.json")

    llama.save_instance("plain", {"alias": "plain", "model_path": "/m/a.gguf", "port": 9500})
    llama.save_instance("tuned", {"alias": "tuned", "model_path": "/m/b.gguf", "port": 9501,
                                  "think": "xhigh"})
    llama.save_instance("embed", {"alias": "embed", "model_path": "/m/e.gguf", "port": 9502,
                                  "role": "embedding"})
    ollama.set_model_config("qwen", {"num_ctx": 8192})

    result = thinking.migrate_shared_reasoning()
    assert result["migrated"] is True
    assert result["mode"] == "off"

    cfg = llama.get_config()
    assert cfg["instances"]["plain"]["think"] == "off"      # 未設定は旧共通値を継ぐ
    assert cfg["instances"]["tuned"]["think"] == "xhigh"    # 個別設定は尊重する
    assert cfg["instances"]["embed"]["think"] == "auto"     # embedding は対象外
    assert ollama.get_model_config("qwen")["think"] == "off"

    # 旧キーは取り除かれ、2回目は何もしない
    assert "reasoning" not in json.loads(policy_path.read_text(encoding="utf-8"))["chat"]
    assert thinking.migrate_shared_reasoning()["migrated"] is False
