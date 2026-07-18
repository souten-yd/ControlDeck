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


async def _enforce_load_limit(provider_kind: str, model_id: str) -> None:
    """全runtime共通の同時load上限。既にload済みの対象再要求は許可する。

    embedding/reranker（小型・RAG補助）はLLMの上限枠に数えず、対象にもしない。
    """
    from app.models_mgmt.runtime_policy import get_policy

    llm_instances = [item for item in llama.list_instances() if str(item.get("role", "llm")) == "llm"]
    if provider_kind == "llama.cpp":
        target = next((item for item in llama.list_instances() if str(item["alias"]) == model_id), None)
        if target is not None and str(target.get("role", "llm")) != "llm":
            return
    ollama_running = await ollama.running_models()
    ollama_names = {str(item.get("name") or item.get("model") or "") for item in ollama_running}
    llama_running = {str(item["alias"]) for item in llm_instances if item.get("loaded")}
    already = model_id in (ollama_names if provider_kind == "ollama" else llama_running)
    if already:
        return
    loaded = len(ollama_names) + len(llama_running)
    limit = get_policy().max_loaded_models
    if loaded >= limit:
        raise ProviderError(
            f"同時ロード上限は{limit}件です。共通設定で上限を増やすか、使用中モデルをアンロードしてください"
        )


async def list_models(provider_id: str) -> list[dict]:
    provider = await _provider(provider_id)
    if provider["provider"] == "ollama" and provider["managed"]:
        try:
            return [_ollama_model(model) for model in await ollama.list_models()]
        except ollama.OllamaError as e:
            raise ProviderError(str(e)) from e
    if provider["provider"] == "llama.cpp" and provider["managed"]:
        backend = llama.get_config().get("backend", "")
        # embedding/rerankerはLLM一覧に混ぜない（Embed/Rerankerタブで管理）
        instances = [item for item in llama.list_instances() if str(item.get("role", "llm")) == "llm"]
        health = await asyncio.gather(*(llama.health(str(item["alias"])) for item in instances))
        result = []
        for config, state in zip(instances, health, strict=True):
            model_path = str(config.get("model_path") or "")
            path = Path(model_path)
            result.append({
                "id": str(config["alias"]), "name": path.name or str(config["alias"]),
                "size_bytes": path.stat().st_size if path.is_file() else 0,
                # health(200) はモデル読み込み完了後のみ。読み込み中も unit 稼働なら loaded 扱いにし、
                # ロード操作直後にインジケータ/ボタンが未ロード表示のまま残らないようにする。
                "modified_at": "", "loaded": bool(state.get("ok")) or bool(config.get("loaded")),
                "details": {
                    "path": model_path, "backend": backend, "port": config.get("port"),
                    "base_url": config.get("base_url"), "unit": config.get("unit"),
                    "runtime_status": config.get("runtime_status"),
                },
            })
        return result
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
    await _enforce_load_limit(str(provider["provider"]), model_id)
    if provider["provider"] == "ollama":
        try:
            return await ollama.load(model_id, keep_alive)
        except ollama.OllamaError as e:
            raise ProviderError(str(e)) from e
    try:
        llama.get_instance(model_id)
    except KeyError:
        raise ProviderNotFound("設定中のllama.cppモデルと一致しません")
    ok, error = await asyncio.to_thread(llama.start_instance, model_id)
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
    try:
        llama.get_instance(model_id)
    except KeyError:
        raise ProviderNotFound("設定中のllama.cppモデルと一致しません")
    ok, error = await asyncio.to_thread(llama.stop_instance, model_id)
    if not ok:
        raise ProviderError(error or "llama.cppの停止に失敗しました")
    return {"model": model_id, "loaded": False}


async def delete_model(provider_id: str, model_id: str) -> None:
    provider = await _provider(provider_id)
    if "delete" not in provider["capabilities"]:
        raise UnsupportedOperation("このproviderはモデル削除に対応していません")
    if provider["provider"] == "llama.cpp":
        try:
            await asyncio.to_thread(llama.delete_instance, model_id)
            return
        except KeyError as e:
            raise ProviderNotFound(str(e)) from e
    try:
        await ollama.delete(model_id)
    except ollama.OllamaError as e:
        raise ProviderError(str(e)) from e


async def provider_health(provider_id: str) -> dict:
    provider = await _provider(provider_id)
    if provider["provider"] == "ollama" and provider["managed"]:
        try:
            state = await ollama.status()
            return {"ok": bool(state.get("available")), "provider": "ollama", "instances": []}
        except ollama.OllamaError as exc:
            raise ProviderError(str(exc)) from exc
    if provider["provider"] == "llama.cpp" and provider["managed"]:
        instances = llama.list_instances()
        states = await asyncio.gather(*(llama.health(str(item["alias"])) for item in instances))
        details = [
            {"alias": item["alias"], "port": item["port"], "ok": bool(state.get("ok")),
             "runtime_status": item.get("runtime_status")}
            for item, state in zip(instances, states, strict=True)
        ]
        return {"ok": any(item["ok"] for item in details), "provider": "llama.cpp",
                "installed": llama.is_installed(), "instances": details}
    return {"ok": bool(provider.get("available")), "provider": provider["provider"], "instances": []}
