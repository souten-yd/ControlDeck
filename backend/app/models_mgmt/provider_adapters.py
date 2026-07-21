"""Provider固有のモデル操作を共通ライフサイクルへ変換するadapter。"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from app.models_mgmt import llama, ollama, providers


class ProviderError(RuntimeError):
    pass


class ProviderNotFound(ProviderError):
    pass


class UnsupportedOperation(ProviderError):
    pass


class InvalidConfiguration(ProviderError):
    pass


_LLAMA_CONFIG_KEYS = {
    "n_gpu_layers", "ctx_size", "deep_research_ctx_size", "n_parallel", "flash_attn",
    "n_predict", "batch_size", "ubatch_size", "cache_type_k", "cache_type_v", "threads",
    "threads_batch", "mmap", "mlock", "spec_type", "draft_max", "cpu_moe", "n_cpu_moe",
    "temperature", "top_k", "top_p", "min_p", "repeat_penalty", "seed", "auto_start",
    "idle_exclude",
}


async def _provider(provider_id: str) -> dict:
    catalog = await providers.list_providers()
    item = next((provider for provider in catalog if provider["id"] == provider_id), None)
    if item is None:
        raise ProviderNotFound("LLM providerが見つかりません")
    return item


async def ensure_operation(provider_id: str, operation: str) -> dict:
    """操作開始前にcapabilityとControl Deck管理対象かを確認する。"""
    provider = await _provider(provider_id)
    labels = {"pull": "モデル取得", "configure": "モデル設定"}
    label = labels.get(operation, operation)
    if operation not in provider["capabilities"]:
        raise UnsupportedOperation(f"このproviderは{label}に対応していません")
    if operation in {"pull", "configure"} and not provider.get("managed"):
        raise UnsupportedOperation(f"このproviderはControl Deckからの{label}に対応していません")
    return provider


def _validate_ollama_config(patch: dict) -> None:
    unknown = sorted(set(patch) - ollama.MODEL_CONFIG_KEYS)
    if unknown:
        raise InvalidConfiguration(f"Ollamaで設定できない項目です: {', '.join(unknown)}")
    for key, value in patch.items():
        if value is None or value == "":
            continue
        if key in ollama.OPT_INT or key == "deep_research_num_ctx":
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidConfiguration(f"{key}は整数で指定してください")
            if key == "deep_research_num_ctx" and not 0 < value <= 1_048_576:
                raise InvalidConfiguration("deep_research_num_ctxは1〜1048576で指定してください")
        elif key in ollama.OPT_FLOAT:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidConfiguration(f"{key}は数値で指定してください")
        elif key in {"idle_exclude", "vlm_enabled"}:
            if not isinstance(value, bool):
                raise InvalidConfiguration(f"{key}はbooleanで指定してください")
        elif key == "keep_alive":
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise InvalidConfiguration("keep_aliveは期間文字列または整数で指定してください")
        elif key == "think" and str(value).lower() not in {*ollama.THINK_VALUES, "auto"}:
            raise InvalidConfiguration(f"thinkはautoまたは{', '.join(ollama.THINK_VALUES)}で指定してください")


def _ensure_ollama_model(provider: dict, model_id: str) -> None:
    requested = ollama.normalize_model_name(model_id)
    known = {ollama.normalize_model_name(str(item)) for item in provider.get("models", [])}
    if not requested or requested not in known:
        raise ProviderNotFound("Ollamaモデルが見つかりません")


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


async def pull_model(provider_id: str, model_id: str) -> AsyncIterator[dict]:
    """Provider capabilityに従ってモデル取得進捗を共通形式で返す。"""
    provider = await ensure_operation(provider_id, "pull")
    if provider["provider"] != "ollama" or not provider["managed"]:
        raise UnsupportedOperation("このproviderはControl Deckからのモデル取得に対応していません")
    try:
        async for item in ollama.pull_stream(model_id):
            yield item
    except ollama.OllamaError as exc:
        raise ProviderError(str(exc)) from exc


async def get_model_config(provider_id: str, model_id: str) -> dict:
    provider = await ensure_operation(provider_id, "configure")
    if provider["provider"] == "ollama" and provider["managed"]:
        _ensure_ollama_model(provider, model_id)
        return ollama.get_model_config(model_id)
    if provider["provider"] == "llama.cpp" and provider["managed"]:
        try:
            instance = llama.get_instance(model_id)
        except KeyError as exc:
            raise ProviderNotFound("設定中のllama.cppモデルと一致しません") from exc
        return {key: instance.get(key) for key in sorted(_LLAMA_CONFIG_KEYS) if key in instance}
    raise UnsupportedOperation("このproviderはControl Deckからのモデル設定に対応していません")


async def configure_model(provider_id: str, model_id: str, patch: dict) -> dict:
    """モデルの識別子・path・portを変更しない共通設定境界。"""
    provider = await ensure_operation(provider_id, "configure")
    if provider["provider"] == "ollama" and provider["managed"]:
        _ensure_ollama_model(provider, model_id)
        _validate_ollama_config(patch)
        return ollama.set_model_config(model_id, patch)
    if provider["provider"] == "llama.cpp" and provider["managed"]:
        unknown = sorted(set(patch) - _LLAMA_CONFIG_KEYS)
        if unknown:
            raise InvalidConfiguration(f"llama.cppで設定できない項目です: {', '.join(unknown)}")
        try:
            llama.get_instance(model_id)
            result = llama.save_instance(model_id, patch)
            return {"model": model_id, "config": await get_model_config(provider_id, model_id),
                    "selected_alias": result.get("selected_alias")}
        except KeyError as exc:
            raise ProviderNotFound("設定中のllama.cppモデルと一致しません") from exc
        except ValueError as exc:
            raise InvalidConfiguration(str(exc)) from exc
    raise UnsupportedOperation("このproviderはControl Deckからのモデル設定に対応していません")


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
