"""Provider固有のモデル操作を共通ライフサイクルへ変換するadapter。"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

from app.models_mgmt import display_names
from app.models_mgmt.container_runtime import CONTAINER_RUNTIMES
from app.models_mgmt import llama, local_llm, model_names, ollama, providers, strata_catalog, strata_gateway


class ProviderError(RuntimeError):
    pass


class ProviderNotFound(ProviderError):
    pass


class UnsupportedOperation(ProviderError):
    pass


class InvalidConfiguration(ProviderError):
    pass


_LLAMA_CONFIG_KEYS = {
    "n_gpu_layers", "ctx_size", "deep_research_ctx_size", "n_parallel", "kv_unified", "flash_attn",
    "n_predict", "batch_size", "ubatch_size", "cache_type_k", "cache_type_v", "threads",
    "threads_batch", "mmap", "mlock", "load_mode", "spec_type", "draft_max", "draft_min", "draft_p_min",
    "spec_draft_ngl", "spec_draft_cache_type_k", "spec_draft_cache_type_v",
    "cpu_moe", "n_cpu_moe",
    "temperature", "top_k", "top_p", "min_p", "repeat_penalty", "seed", "auto_start",
    "idle_exclude", "think", "think_budget_tokens",
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


# 上限の判定は「今いくつ載っているか」を数えて行う。数え終えてから実際に
# 載るまでの間に別の要求が同じ判断をすると、両方が通る。実測: 上限 1 に対し
# 同時要求で 2 つ載った。ensure_chat_model_ready の lock は base_url::model
# 単位なので、別モデルの要求同士は直列化されない。
#
# 判定と、判定の前提になる状態の変更を、同じ区間に入れる。ロードそのものは
# 数分かかりうるので区間には入れず、進行中のものを予約として数える。
_admission = asyncio.Lock()
_inflight: set[tuple[str, str]] = set()


async def _enforce_load_limit(provider_kind: str, model_id: str) -> None:
    """全runtime共通の同時load上限。既にload済みの対象再要求は許可する。

    embedding/reranker（小型・RAG補助）はLLMの上限枠に数えず、対象にもしない。
    進行中のロードも枠を占有しているものとして数える。まだ載っていないだけで、
    載ることが決まっているためである。
    """
    from app.models_mgmt.runtime_policy import get_policy

    llm_instances = local_llm.llm_instances()
    if provider_kind in local_llm.RUNTIMES:
        target = local_llm.find(model_id)
        if target is not None and str(target.get("role", "llm")) != "llm":
            return
    ollama_running = await ollama.running_models()
    ollama_names = {str(item.get("name") or item.get("model") or "") for item in ollama_running}
    llama_running = {str(item["alias"]) for item in llm_instances if item.get("loaded")}
    already = model_id in (ollama_names if provider_kind == "ollama" else llama_running)
    if already or (provider_kind, model_id) in _inflight:
        return
    others = {item for item in _inflight if item[1] not in ollama_names | llama_running}
    loaded = len(ollama_names) + len(llama_running) + len(others)
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
            display, quant = model_names.from_file(model_path)
            result.append({
                "id": str(config["alias"]), "name": path.name or str(config["alias"]),
                "size_bytes": model_names.file_size(model_path),
                # health(200) はモデル読み込み完了後のみ。読み込み中も unit 稼働なら loaded 扱いにし、
                # ロード操作直後にインジケータ/ボタンが未ロード表示のまま残らないようにする。
                "modified_at": "", "loaded": bool(state.get("ok")) or bool(config.get("loaded")),
                "details": {
                    "path": model_path, "backend": backend, "port": config.get("port"),
                    "backend_label": llama.BACKEND_LABELS.get(backend, backend),
                    "runtime": "llama.cpp",
                    "base_url": config.get("base_url"), "unit": config.get("unit"),
                    "runtime_status": config.get("runtime_status"),
                    "vision_enabled": bool(config.get("mmproj_path")),
                    "display_name": display_names.apply("llama.cpp", str(config["alias"]), display or str(config["alias"])),
                    "quantization": quant,
                },
            })
        return result
    if provider["provider"] == "maya-s" and provider["managed"]:
        from app.models_mgmt import maya_s
        def maya_row(item: dict) -> dict:
            path = str(item.get("model_path") or "")
            if item["alias"] == maya_s.ALIAS:
                name, quant = model_names.from_file(path) if path else ("", "")
                # "GLM-5.3-Flash-Maya-S-v2" + "IQ2_XXS": the Maya tier is part of the quantisation.
                tier = name.removeprefix("GLM-5.3-Flash-") if name.startswith("GLM-5.3-Flash-") else ""
                quant = " ".join(part for part in (tier, quant) if part)
                size = model_names.file_size(path) or 94_700_000_000
            else:
                quant = str(item.get("tier") or "")
                size = int(float(item.get("size_gb") or 0) * 1e9)
            model = str(item.get("display_name") or "GLM-5.3-Flash · Maya-S").split(" · ")[0]
            return {
                "id": item["alias"], "name": f"Maya · {item.get('display_name') or model}",
                "size_bytes": size, "modified_at": "",
                "loaded": item["loaded"],
                "details": {"runtime": "maya-s", "backend": "HIP",
                            "port": item["port"], "base_url": item["base_url"],
                            "display_name": display_names.apply("maya-s", item["alias"], str(item.get("display_name") or model)),
                            "quantization": quant or "Maya-S",
                            "vision_enabled": bool(item.get("vision")),
                            # エンジンの更新で、このモデルのファイルに新しい版が出た。
                            "update_available": bool(item.get("update_available")),
                            "update_gb": item.get("update_gb") or 0,
                            "setup_id": str(item.get("setup_id") or ""),
                            "folder": str(item.get("folder") or "")}}
        return [maya_row(item) for item in await asyncio.to_thread(maya_s.list_instances)]
    if provider["provider"] == "strata" and provider["managed"]:
        def strata_name(item: dict) -> tuple[str, str]:
            # 手元の GGUF を使う登録は、系列名ではなくファイルの名前と量子化を出す。系列名だと同じ系列のモデルが同じ名前になった
            # （2026-11-21 利用者「モデルが同じ名前になるので分かりにくい」）
            name, quant = model_names.from_file(item.get("model_path") or "") if item.get("model_path") else ("", "")
            default = name or strata_catalog.short_name(item["family"])
            return display_names.apply("strata", item["alias"], default), quant or str(item["model"]).upper()

        return [{
            "id": item["alias"],
            "name": f"Strata · {item['family']} / {item['model']}",
            "size_bytes": model_names.file_size(item.get("model_path") or ""), "modified_at": "",
            "loaded": item["loaded"],
            "details": {
                "runtime": "strata", "backend": "ROCm",
                "port": item["port"], "base_url": item["base_url"],
                "runtime_status": item["runtime_status"],
                "display_name": strata_name(item)[0],
                "quantization": strata_name(item)[1], "context": item["context"],
                "vision_enabled": item.get("vision", "on") != "off",
            },
        } for item in await asyncio.to_thread(strata_gateway.list_instances)]
    if provider["provider"] == "radiance" and provider["managed"]:
        from app.models_mgmt import radiance

        def size(path: str) -> int:
            try:
                return os.path.getsize(path)
            except OSError:
                return 0
        def radiance_row(item: dict) -> dict:
            display, quant = model_names.from_file(str(item["model_path"]))
            return {
                "id": item["alias"],
                "name": f"Radiance · {os.path.basename(str(item['model_path'])).removesuffix('.rad')}",
                "size_bytes": size(str(item["model_path"])), "modified_at": "", "loaded": item["loaded"],
                "details": {
                    "runtime": "radiance", "backend": "HIP",
                    "port": item["port"], "base_url": item["base_url"],
                    "runtime_status": item["runtime_status"], "context": item["context"],
                    "path": item["model_path"], "display_name": display_names.apply("radiance", item["alias"], display or item["alias"]),
                    "quantization": quant,
                    # The published Qwen3.8-27B containers carry their vision tower.
                    "vision_enabled": True,
                },
            }
        return [radiance_row(item) for item in await asyncio.to_thread(radiance.list_instances)]
    if provider["provider"] == "sly-radiance" and provider["managed"]:
        from app.models_mgmt import sly_radiance

        def sly_row(item: dict) -> dict:
            folder = Path(str(item["model_path"]))
            return {
                "id": item["alias"], "name": f"Sly-Radiance · {folder.name}",
                "size_bytes": sum(f.stat().st_size for f in folder.glob("*.safetensors") if f.is_file()),
                "modified_at": "", "loaded": item["loaded"],
                "details": {
                    "runtime": "sly-radiance", "backend": "HIP",
                    "port": item["port"], "base_url": item["base_url"],
                    "runtime_status": item["runtime_status"], "context": item["context"],
                    "path": item["model_path"],
                    "display_name": display_names.apply("sly-radiance", item["alias"], folder.name),
                    "quantization": item.get("quantization") or "MXFP4",
                    # The container is started with --limit-mm-per-prompt.image 0.
                    "vision_enabled": False,
                },
            }
        return [sly_row(item) for item in await asyncio.to_thread(sly_radiance.list_instances)]
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
    kind = str(provider["provider"])
    if kind in ("strata", *CONTAINER_RUNTIMES):
        # A large model takes the card: free the other large models (and, for Radiance, idle
        # llama.cpp chat models) first, so the load limit counts what will actually remain.
        from app.models_mgmt import large_model_lifecycle
        try:
            await large_model_lifecycle.release_for(kind, keep=str(model_id))
        except RuntimeError as exc:
            raise ProviderError(str(exc)) from exc
    reservation = (kind, model_id)
    # 判定と予約を同じ区間で行う。ここを分けると、両方が「まだ空きがある」を
    # 見てから両方が載る。
    async with _admission:
        await _enforce_load_limit(kind, model_id)
        held = reservation not in _inflight
        _inflight.add(reservation)
    try:
        if kind == "ollama":
            try:
                return await ollama.load(model_id, keep_alive)
            except ollama.OllamaError as e:
                raise ProviderError(str(e)) from e
        if local_llm.find(model_id) is None:
            raise ProviderNotFound(f"設定中の{kind}モデルと一致しません")
        if kind in CONTAINER_RUNTIMES:
            # One container model takes the whole card: free the others first, then wait until it serves.
            module = local_llm.module_for(kind)
            if not await module.ensure_ready(model_id, start=True):
                tail = await asyncio.to_thread(module.log_tail, model_id, 8)
                label = "Sly-Radiance" if kind == "sly-radiance" else "Radiance"
                raise ProviderError(f"{label}の起動に失敗しました" + (f": {tail[-400:]}" if tail else ""))
            return {"model": model_id, "loaded": True}
        ok, error = await asyncio.to_thread(local_llm.start_instance, model_id)
        if not ok:
            raise ProviderError(error or f"{kind}の起動に失敗しました")
        return {"model": model_id, "loaded": True}
    finally:
        # 失敗しても枠を返す。返さないと、以後その分だけ上限が目減りする。
        if held:
            _inflight.discard(reservation)


async def unload_model(provider_id: str, model_id: str) -> dict:
    provider = await _provider(provider_id)
    if "unload" not in provider["capabilities"]:
        raise UnsupportedOperation("このproviderはモデルのアンロード操作に対応していません")
    if provider["provider"] == "ollama":
        try:
            return await ollama.unload(model_id)
        except ollama.OllamaError as e:
            raise ProviderError(str(e)) from e
    kind = str(provider["provider"])
    if local_llm.find(model_id) is None:
        raise ProviderNotFound(f"設定中の{kind}モデルと一致しません")
    ok, error = await asyncio.to_thread(local_llm.stop_instance, model_id)
    if not ok:
        raise ProviderError(error or f"{kind}の停止に失敗しました")
    return {"model": model_id, "loaded": False}


async def delete_model(provider_id: str, model_id: str) -> None:
    provider = await _provider(provider_id)
    if "delete" not in provider["capabilities"]:
        raise UnsupportedOperation("このproviderはモデル削除に対応していません")
    if provider["provider"] == "strata":
        try:
            await asyncio.to_thread(strata_gateway.delete_instance, model_id)
            return
        except (KeyError, strata_gateway.StrataGatewayError) as exc:
            raise ProviderError(str(exc)) from exc
    if provider["provider"] in CONTAINER_RUNTIMES:
        module = local_llm.module_for(str(provider["provider"]))
        try:
            await asyncio.to_thread(module.delete_instance, model_id)
            return
        except (KeyError, RuntimeError) as exc:   # each module's error class is a RuntimeError
            raise ProviderError(str(exc)) from exc
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
    if provider["provider"] == "maya-s" and provider["managed"]:
        from app.models_mgmt import maya_s
        items = await asyncio.to_thread(maya_s.list_instances)
        states = await asyncio.gather(*(maya_s.health(str(item["alias"])) for item in items)) if items else [
            await maya_s.health()]
        return {"ok": any(bool(state["ok"]) for state in states), "provider": "maya-s",
                "instances": [{"alias": item["alias"], "port": item["port"], **state}
                              for item, state in zip(items, states)] if items else list(states)}
    if provider["provider"] == "strata" and provider["managed"]:
        instances = await asyncio.to_thread(strata_gateway.list_instances)
        states = await asyncio.gather(*(strata_gateway.health(str(item["alias"])) for item in instances))
        return {
            "ok": any(state["ok"] for state in states), "provider": "strata",
            "installed": True,
            "instances": [{"alias": item["alias"], "port": item["port"],
                           "ok": state["ok"], "runtime_status": item["runtime_status"]}
                          for item, state in zip(instances, states, strict=True)],
        }
    if provider["provider"] in CONTAINER_RUNTIMES and provider["managed"]:
        module = local_llm.module_for(str(provider["provider"]))
        instances = await asyncio.to_thread(module.list_instances)
        states = await asyncio.gather(*(module.health(str(item["alias"])) for item in instances))
        return {
            "ok": any(state["ok"] for state in states), "provider": provider["provider"], "installed": True,
            "instances": [{"alias": item["alias"], "port": item["port"],
                           "ok": state["ok"], "runtime_status": item["runtime_status"]}
                          for item, state in zip(instances, states, strict=True)],
        }
    return {"ok": bool(provider.get("available")), "provider": provider["provider"], "instances": []}
