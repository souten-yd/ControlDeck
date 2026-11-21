"""モデルの表示名。

目的:
- 利用者が付けた名前を、モデルの一覧（Models）とモデルを選ぶ欄（AI アシスタント）に出すこと。空にすると既定へ戻ること。
- ゲートウェイでの名前（alias）は変えないこと（既定のモデル・会話・Workflow の設定が alias を覚えているため）。
- Strata の手元の GGUF の登録は、系列名ではなくファイルの名前と量子化を既定にすること（同じ系列のモデルが同じ名前にならない）。

根拠: 2026-11-21 利用者「モデルが同じ名前になるので分かりにくい。任意の名前に変えれるようにしたい」。
"""
from __future__ import annotations

import asyncio

from tests.conftest import CSRF_HEADERS


def test_names_are_saved_per_runtime_and_alias_and_blank_restores_default(admin_client):
    from app.models_mgmt import display_names

    r = admin_client.put("/api/v1/models/display-names/strata/qwen-flash-next-uncensored", json={"name": "  無検閲 Q4  "},
                         headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["name"] == "無検閲 Q4"
    assert display_names.apply("strata", "qwen-flash-next-uncensored", "既定") == "無検閲 Q4"
    assert display_names.apply("llama.cpp", "qwen-flash-next-uncensored", "既定") == "既定"     # ランタイムごと
    admin_client.put("/api/v1/models/display-names/strata/qwen-flash-next-uncensored", json={"name": ""}, headers=CSRF_HEADERS)
    assert display_names.get("strata", "qwen-flash-next-uncensored") == ""
    assert admin_client.put("/api/v1/models/display-names/ollama/x", json={"name": "a"}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put("/api/v1/models/display-names/strata/x", json={"name": "a\nb" * 50},
                            headers=CSRF_HEADERS).status_code == 422


def test_strata_lists_file_names_and_custom_names(monkeypatch):
    from app.models_mgmt import display_names, provider_adapters, strata_gateway

    rows = [
        {"alias": "qwen-flash-next", "family": "unsloth", "model": "ud-iq4_xs", "port": 8096, "loaded": True,
         "base_url": "", "runtime_status": "READY", "context": "512k",
         "model_path": "/data1tb/LLM/Qwen3.8-Flash-Next-UD-IQ4_XS/UD-IQ4_XS/Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf"},
        {"alias": "qwen-flash-next-uncensored", "family": "unsloth", "model": "ud-q4_k_xl", "port": 8097, "loaded": False,
         "base_url": "", "runtime_status": "STOPPED", "context": "512k",
         "model_path": "/data1tb/LLM/Qwen3.8-Flash-Next-Uncensored-Q4_K_S/Q4_K_S/Qwen3.8-Flash-Next-Uncensored-Q4_K_S-00001-of-00003.gguf"},
    ]
    monkeypatch.setattr(strata_gateway, "list_instances", lambda: rows)

    async def provider(provider_id):
        return {"provider": "strata", "managed": True}

    monkeypatch.setattr(provider_adapters, "_provider", provider)
    listed = {m["id"]: m["details"] for m in asyncio.run(provider_adapters.list_models("strata"))}
    assert listed["qwen-flash-next"]["display_name"] != listed["qwen-flash-next-uncensored"]["display_name"]
    assert "Uncensored" in listed["qwen-flash-next-uncensored"]["display_name"]
    assert listed["qwen-flash-next-uncensored"]["quantization"] == "Q4_K_S"
    display_names.set_name("strata", "qwen-flash-next-uncensored", "無検閲版")
    listed = {m["id"]: m["details"] for m in asyncio.run(provider_adapters.list_models("strata"))}
    assert listed["qwen-flash-next-uncensored"]["display_name"] == "無検閲版"
    assert {m["id"] for m in asyncio.run(provider_adapters.list_models("strata"))} == {"qwen-flash-next", "qwen-flash-next-uncensored"}
    display_names.set_name("strata", "qwen-flash-next-uncensored", "")
