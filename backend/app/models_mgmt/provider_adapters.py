"""Provider固有のモデル操作を共通ライフサイクルへ変換するadapter。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.models_mgmt import llama, ollama, providers


class ProviderError(RuntimeError):
    pass


class ProviderNotFound(ProviderError):
    pass


class UnsupportedOperation(ProviderError):
    pass


async def _provider(provider_id: str) -> dict:
    catalog = await providers.list_providers()
    item = next((provider for provider in catalog if provider["id"] == provider_id), None)
    if item is None:
        raise ProviderNotFound("LLM providerが見つかりません")
    return item


def _ollama_model(model: dict) -> dict:
    return {
        "id": model["name"], "name": model["name"],
        "size_bytes": model.get("size", 0), "modified_at": model.get("modified_at", ""),
        "loaded": bool(model.get("loaded")),
        "details": {
            "family": model.get("family", ""), "parameter_size": model.get("parameter_size", ""),
            "quantization": model.get("quantization", ""), "vram_bytes": model.get("vram"),
        },
    }


async def list_models(provider_id: str) -> list[dict]:
    provider = await _provider(provider_id)
    if provider["provider"] == "ollama" and provider["managed"]:
        try:
            return [_ollama_model(model) for model in await ollama.list_models()]
        except ollama.OllamaError as e:
            raise ProviderError(str(e)) from e
    if provider["provider"] == "llama.cpp" and provider["managed"]:
        config = llama.get_config()["instance"]
        model_path = str(config.get("model_path") or "")
        if not model_path:
            return []
        health = await llama.health()
        path = Path(model_path)
        return [{
            "id": str(config.get("alias") or "llama"), "name": path.name,
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "modified_at": "", "loaded": bool(health.get("ok")),
            "details": {"path": model_path, "backend": llama.get_config().get("backend", "")},
        }]
    return [{
        "id": model, "name": model, "size_bytes": 0, "modified_at": "",
        # OpenAI互換APIだけでは各モデルのロード状態を判定できない。
        "loaded": None, "details": {},
    } for model in provider.get("models", [])]


async def load_model(provider_id: str, model_id: str, keep_alive: str | int | None = None) -> dict:
    provider = await _provider(provider_id)
    if "load" not in provider["capabilities"]:
        raise UnsupportedOperation("このproviderはモデルのロード操作に対応していません")
    try:
        from app.models_mgmt.runtime_policy import ensure_gpu_profile

        await asyncio.to_thread(ensure_gpu_profile, force=True)
    except RuntimeError as e:
        raise ProviderError(str(e)) from e
    if provider["provider"] == "ollama":
        try:
            return await ollama.load(model_id, keep_alive)
        except ollama.OllamaError as e:
            raise ProviderError(str(e)) from e
    config = llama.get_config()["instance"]
    if model_id != str(config.get("alias") or "llama"):
        raise ProviderNotFound("設定中のllama.cppモデルと一致しません")
    ok, error = await asyncio.to_thread(llama.start_instance)
    if not ok:
        raise ProviderError(error or "llama.cppの起動に失敗しました")
    return {"model": model_id, "loaded": True}


async def unload_model(provider_id: str, model_id: str) -> dict:
    provider = await _provider(provider_id)
    if "unload" not in provider["capabilities"]:
        raise UnsupportedOperation("このproviderはモデルのアンロード操作に対応していません")
    if provider["provider"] == "ollama":
        try:
            return await ollama.unload(model_id)
        except ollama.OllamaError as e:
            raise ProviderError(str(e)) from e
    config = llama.get_config()["instance"]
    if model_id != str(config.get("alias") or "llama"):
        raise ProviderNotFound("設定中のllama.cppモデルと一致しません")
    ok, error = await asyncio.to_thread(llama.stop_instance)
    if not ok:
        raise ProviderError(error or "llama.cppの停止に失敗しました")
    return {"model": model_id, "loaded": False}


async def delete_model(provider_id: str, model_id: str) -> None:
    provider = await _provider(provider_id)
    if "delete" not in provider["capabilities"]:
        raise UnsupportedOperation("このproviderはモデル削除に対応していません")
    try:
        await ollama.delete(model_id)
    except ollama.OllamaError as e:
        raise ProviderError(str(e)) from e
