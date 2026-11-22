"""Model（Ollama）管理 API。"""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.jobs import service as jobs
from app.models import User
from app.models_mgmt import ollama
from app.security.deps import authenticate_websocket, require_permission

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/runtime-environment")
async def runtime_environment(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import runtime_policy

    return await runtime_policy.environment()


@router.get("/swap-queue")
async def swap_queue(user: User = Depends(require_permission("workflows.run"))):
    """モデルの載せ替えの待ち行列（調停役の状態）。モデルごとの待ち数・最古の待ち秒・次の予定と理由・最近の載せ替え。"""
    from app.models_mgmt import swap_scheduler

    return await swap_scheduler.get_scheduler().status()


@router.put("/runtime-policy")
async def put_runtime_policy(
    body: dict, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import runtime_policy

    current = runtime_policy.get_policy().model_dump()
    merged = {**current, **body}
    for nested in ("chat", "deep_research", "amd_gpu", "preferred_models", "swap_scheduler"):
        if isinstance(current.get(nested), dict) and isinstance(body.get(nested), dict):
            merged[nested] = {**current[nested], **body[nested]}
    try:
        policy = runtime_policy.RuntimePolicy.model_validate(merged)
        deferred_only = policy.model_dump(exclude={"preferred_models", "vram_margin_gib", "swap_scheduler"}) == {
            k: v for k, v in current.items() if k not in {"preferred_models", "vram_margin_gib", "swap_scheduler"}}
        if not deferred_only:
            policy = runtime_policy.normalize_gpu_profile(policy)
        changed_preferences = {key: alias for key, alias in policy.preferred_models.items()
                               if alias and alias != current.get("preferred_models", {}).get(key)}
        if changed_preferences:
            choices = await runtime_policy.startup_models()
            for runtime, alias in changed_preferences.items():
                if not any(i["runtime"] == runtime and i["alias"] == alias and i["ready"] for i in choices):
                    raise ValueError(f"{runtime} の起動可能な登録モデルを選択してください: {alias}")
        from app.models_mgmt import amd_gpu
        # Preference and margin changes take effect on the next start without interrupting inference.
        if not deferred_only:
            amd_gpu.apply_profile(policy.amd_gpu, force=True)
            await runtime_policy.apply_selection(policy)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if "vram_margin_gib" in body:
        import math
        from app.models_mgmt import llama
        try:
            await asyncio.to_thread(llama.sync_vram_margin, math.ceil(policy.vram_margin_gib * 1024))
        except (RuntimeError, OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    runtime_policy.save_policy(policy)
    if current.get("preferred_models", {}).get("llama.cpp") != policy.preferred_models.get("llama.cpp"):
        from app.models_mgmt import llama
        # Update boot enablement only; no model is started or stopped.
        seen_endpoints: set[str] = set()
        for item in await asyncio.to_thread(llama.list_instances):
            endpoint = str(item.get("endpoint_id") or "")
            if endpoint and endpoint not in seen_endpoints:
                await asyncio.to_thread(llama.sync_instance_unit, str(item["alias"]))
                seen_endpoints.add(endpoint)
    audit.record(db, "model.runtime_policy", user=user, resource_type="model-runtime",
                 request=request, metadata={"runtime": policy.selected_runtime,
                                            "backend": policy.selected_backend,
                                            "coexistence": policy.coexistence,
                                            "preferred_models": policy.preferred_models,
                                            "vram_margin_gib": policy.vram_margin_gib,
                                            "gpu_profile": policy.amd_gpu.profile if policy.amd_gpu.enabled else "disabled"})
    return await runtime_policy.environment()


@router.get("/storage/volumes")
def storage_volumes(user: User = Depends(require_permission("workflows.run"))):
    """モデル置き場の候補になる実機ボリューム（ライブラリ追加時の選択肢）。"""
    from app.models_mgmt import libraries

    return libraries.detect_volumes()


@router.get("/libraries")
def model_libraries(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import libraries

    return {"libraries": libraries.list_libraries()}


class ModelLibraryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    label: str = Field(min_length=1, max_length=128)
    volume_uuid: str = Field(default="", max_length=64)
    subpath: str = Field(default="", max_length=512)
    path: str = Field(default="", max_length=1024)
    default: bool = False


@router.put("/libraries")
def put_model_libraries(
    body: list[ModelLibraryBody], request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """ライブラリ一覧を丸ごと置き換える。"""
    from app.models_mgmt import libraries, runtime_policy

    entries = [item.model_dump() for item in body]
    try:
        entries = libraries.validate_entries(entries)
    except libraries.LibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    policy = runtime_policy.get_policy()
    policy.model_libraries = [runtime_policy.ModelLibrary.model_validate(e) for e in entries]
    runtime_policy.save_policy(policy)
    audit.record(db, "model.libraries", user=user, resource_type="model-library",
                 request=request, metadata={"count": len(entries)})
    return {"libraries": libraries.list_libraries()}


@router.get("/libraries/{library_id}/scan")
async def scan_model_library(
    library_id: str, user: User = Depends(require_permission("workflows.run")),
):
    """ライブラリ内の GGUF 一覧。登録済み／未登録（孤児）を仕分けて返す。"""
    from app.models_mgmt import libraries

    try:
        return await asyncio.to_thread(libraries.scan_library, library_id)
    except libraries.LibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionError, FileNotFoundError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


class ProviderLoadBody(BaseModel):
    keep_alive: str | int | None = None


class ProviderPullBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=300)


@router.get("/providers")
async def providers(user: User = Depends(require_permission("workflows.run"))):
    """管理対象と検出済みのLLMランタイムを共通形式で返す。"""
    from app.models_mgmt.providers import list_providers

    return await list_providers()


class DisplayNameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=200)


@router.put("/display-names/{runtime}/{alias}")
def put_display_name(runtime: str, alias: str, body: DisplayNameBody, request: Request,
                     user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    """モデルの表示名を付ける（空で既定に戻す）。ゲートウェイでの名前（alias）は変えない。"""
    from app.models_mgmt import display_names

    try:
        name = display_names.set_name(runtime, alias, body.name)
    except display_names.DisplayNameError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "model.display_name", user=user, resource_type="model", resource_id=f"{runtime}:{alias}",
                 request=request, metadata={"name": name})
    return {"runtime": runtime, "alias": alias, "name": name}


@router.get("/providers/{provider_id}/models")
async def provider_models(provider_id: str, user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import provider_adapters

    try:
        return await provider_adapters.list_models(provider_id)
    except provider_adapters.ProviderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except provider_adapters.ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/providers/{provider_id}/pull-jobs", status_code=201)
async def provider_pull(
    provider_id: str, body: ProviderPullBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """Capability付きprovider adapter経由でdurable pull jobを開始する。"""
    from app.models_mgmt import provider_adapters

    target = body.model.strip()
    if not target:
        raise HTTPException(status_code=422, detail="モデル名を入力してください")
    try:
        await provider_adapters.ensure_operation(provider_id, "pull")
    except provider_adapters.ProviderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except provider_adapters.UnsupportedOperation as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def run(job: jobs.Job):
        async for chunk in provider_adapters.pull_model(provider_id, target):
            status = str(chunk.get("status", ""))
            job.set_progress(status or "取得中", chunk.get("completed"), chunk.get("total"))
            if status and (not job.events or job.events[-1]["message"] != status):
                job.log(status)
        return {"provider": provider_id, "model": target}

    job = jobs.create("model.pull", f"モデル取得: {target}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"), priority=0)
    audit.record(db, "model.pull", user=user, resource_type="model", resource_id=target,
                 request=request, metadata={"provider": provider_id, "job_id": job.id})
    return {"job_id": job.id}


@router.get("/providers/{provider_id}/health")
async def provider_health(provider_id: str, user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import provider_adapters

    try:
        return await provider_adapters.provider_health(provider_id)
    except provider_adapters.ProviderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except provider_adapters.ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/providers/{provider_id}/models/{model_id:path}/load")
async def provider_load(provider_id: str, model_id: str, body: ProviderLoadBody, request: Request,
                        user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    from app.models_mgmt import provider_adapters

    try:
        result = await provider_adapters.load_model(provider_id, model_id, body.keep_alive)
    except provider_adapters.ProviderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except provider_adapters.UnsupportedOperation as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except provider_adapters.ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    audit.record(db, "model.load", user=user, resource_type="model", resource_id=model_id,
                 request=request, metadata={"provider": provider_id})
    return result


@router.post("/providers/{provider_id}/models/{model_id:path}/unload")
async def provider_unload(provider_id: str, model_id: str, request: Request,
                          user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    from app.models_mgmt import provider_adapters

    try:
        result = await provider_adapters.unload_model(provider_id, model_id)
    except provider_adapters.ProviderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except provider_adapters.UnsupportedOperation as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except provider_adapters.ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    audit.record(db, "model.unload", user=user, resource_type="model", resource_id=model_id,
                 request=request, metadata={"provider": provider_id})
    return result


@router.delete("/providers/{provider_id}/models/{model_id:path}")
async def provider_delete(provider_id: str, model_id: str, request: Request,
                          user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    from app.models_mgmt import provider_adapters

    try:
        await provider_adapters.delete_model(provider_id, model_id)
    except provider_adapters.ProviderNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except provider_adapters.UnsupportedOperation as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except provider_adapters.ProviderError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    audit.record(db, "model.delete", user=user, resource_type="model", resource_id=model_id,
                 request=request, metadata={"provider": provider_id})
    return {"ok": True}


@router.get("/providers/{provider_id}/models/{model_id:path}/config")
async def provider_model_config(
    provider_id: str, model_id: str,
    user: User = Depends(require_permission("workflows.run")),
):
    from app.models_mgmt import provider_adapters

    try:
        return await provider_adapters.get_model_config(provider_id, model_id)
    except provider_adapters.ProviderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except provider_adapters.UnsupportedOperation as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except provider_adapters.ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/providers/{provider_id}/models/{model_id:path}/config")
async def provider_model_config_put(
    provider_id: str, model_id: str, body: dict, request: Request, reload: bool = False,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import provider_adapters

    patch = _validated_provider_patch(provider_id, body)
    try:
        result = await provider_adapters.configure_model(provider_id, model_id, patch)
    except provider_adapters.ProviderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (provider_adapters.UnsupportedOperation, provider_adapters.InvalidConfiguration) as exc:
        status = 409 if isinstance(exc, provider_adapters.UnsupportedOperation) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except provider_adapters.ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit.record(db, "model.configure", user=user, resource_type="model", resource_id=model_id,
                 request=request, metadata={"provider": provider_id, "fields": sorted(patch),
                                            "reload": reload})
    response: dict = result
    if reload:
        try:
            loaded = await provider_adapters.load_model(provider_id, model_id)
        except provider_adapters.ProviderNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except provider_adapters.UnsupportedOperation as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except provider_adapters.ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        response = {"config": result, "loaded": loaded}
    return response


@router.get("/status")
async def status(user: User = Depends(require_permission("workflows.run"))):
    return await ollama.status()


@router.get("")
async def list_models(user: User = Depends(require_permission("workflows.run"))):
    try:
        return await ollama.list_models()
    except ollama.OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/running")
async def running(user: User = Depends(require_permission("workflows.run"))):
    return await ollama.running_models()


@router.get("/settings")
def get_settings(user: User = Depends(require_permission("workflows.run"))):
    return ollama.get_settings()


class SettingsBody(BaseModel):
    base_url: str | None = None
    # idle_unload_enabled / idle_unload_minutes は ⚙️共通設定（RuntimePolicy）側で持つ。
    # ollama-settings.json 側のキーは効かないため、ここから書けるふりをしない。
    default_keep_alive: str | None = None
    default_model: str | None = None
    kv_cache_type: str | None = None
    flash_attention: bool | None = None


@router.put("/settings")
def put_settings(body: SettingsBody, user: User = Depends(require_permission("workflows.edit"))):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "kv_cache_type" in patch and patch["kv_cache_type"] not in ollama.KV_CACHE_TYPES:
        raise HTTPException(status_code=422, detail=f"kv_cache_type は {ollama.KV_CACHE_TYPES} のいずれか")
    return ollama.save_settings(patch)


@router.get("/ollama-env")
def ollama_env(user: User = Depends(require_permission("workflows.run"))):
    """稼働中 Ollama の KV キャッシュ/Flash Attention 環境変数の実際の状態（診断用）。"""
    return ollama.runtime_env()


@router.get("/options-spec")
def options_spec(user: User = Depends(require_permission("workflows.run"))):
    """UI がフォームを描くための、設定可能な options キー一覧。"""
    return {"int": sorted(ollama.OPT_INT), "float": sorted(ollama.OPT_FLOAT),
            "kv_cache_types": list(ollama.KV_CACHE_TYPES), "think_values": list(ollama.THINK_VALUES)}


# モデル個別設定は自由キー（options 群）。ollama 側で許可キー・型を検証する
class ModelConfigBody(BaseModel):
    model_config = {"extra": "allow"}


@router.get("/{model:path}/config")
def get_model_config(model: str, user: User = Depends(require_permission("workflows.run"))):
    return ollama.get_model_config(model)


@router.put("/{model:path}/config")
async def put_model_config(
    model: str, body: dict,
    reload: bool = False,
    user: User = Depends(require_permission("workflows.edit")),
):
    """モデル個別設定を保存。reload=true なら新しい設定で即ロードして反映する。"""
    cfg = ollama.set_model_config(model, body)
    result: dict = {"config": cfg}
    if reload:
        try:
            result["loaded"] = await ollama.load(model)
        except ollama.OllamaError as e:
            result["reload_error"] = str(e)
    return result


@router.get("/hf-search")
async def hf_search(q: str, user: User = Depends(require_permission("workflows.edit"))):
    if not q.strip():
        return []
    try:
        return await ollama.hf_search(q.strip())
    except ollama.OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e))


class PullJobBody(BaseModel):
    model: str = Field(min_length=1, max_length=300)


@router.post("/pull-jobs", status_code=201)
async def start_pull_job(
    body: PullJobBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """モデル取得をサーバー側ジョブとして開始する（ブラウザを閉じても継続）。"""
    target = body.model.strip()

    async def run(job: jobs.Job):
        async for chunk in ollama.pull_stream(target):
            status = str(chunk.get("status", ""))
            job.set_progress(status or "取得中", chunk.get("completed"), chunk.get("total"))
            if status and (not job.events or job.events[-1]["message"] != status):
                job.log(status)
        return {"model": target}

    job = jobs.create("model.pull", f"モデル取得: {target}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"), priority=0)
    audit.record(db, "model.pull", user=user, resource_type="model", resource_id=target,
                 request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


class RegisterJobBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=1024)


@router.post("/register-jobs", status_code=201)
async def start_register_job(
    body: RegisterJobBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """ローカル GGUF 登録をサーバー側ジョブとして開始する（ブラウザを閉じても継続）。"""
    name, path = body.name.strip(), body.path.strip()

    async def run(job: jobs.Job):
        async for chunk in ollama.register_gguf_stream(name, path):
            status = str(chunk.get("status", ""))
            job.set_progress(status or "処理中", chunk.get("completed"), chunk.get("total"))
            if status and (not job.events or job.events[-1]["message"] != status):
                job.log(status)
        return {"model": name}

    job = jobs.create("model.register", f"ローカル登録: {name}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"), priority=0)
    audit.record(db, "model.register", user=user, resource_type="model", resource_id=name,
                 request=request, metadata={"job_id": job.id, "path": path})
    return {"job_id": job.id}


@router.get("/gguf-scan")
async def gguf_scan(path: str, user: User = Depends(require_permission("workflows.edit"))):
    """フォルダ内の GGUF ファイル一覧（ローカル登録用）。許可ルート配下のみ。"""
    try:
        files = await asyncio.to_thread(ollama.scan_gguf, path)
    except (PermissionError, FileNotFoundError) as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ollama.OllamaError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "path": path,
        "files": [{**f, "suggest_name": ollama.suggest_model_name(f["name"])} for f in files],
    }


@router.websocket("/register")
async def register_local(websocket: WebSocket):
    """ローカル GGUF を Ollama モデルとして登録する。最初のメッセージ {name, path}。

    進捗（ハッシュ計算 → 転送 → 作成）を逐次返す。
    """
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "workflows.edit")
        if user is None:
            return
        username = user.username
    finally:
        db.close()
    await websocket.accept()
    try:
        first = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=15))
        name = str(first.get("name", "")).strip()
        path = str(first.get("path", "")).strip()
    except (asyncio.TimeoutError, json.JSONDecodeError):
        await websocket.close(code=4400)
        return
    if not name or not path:
        await websocket.send_text(json.dumps({"type": "error", "message": "モデル名とファイルパスが必要です"}))
        await websocket.close()
        return
    try:
        async for chunk in ollama.register_gguf_stream(name, path):
            await websocket.send_text(json.dumps({"type": "progress", **chunk}, ensure_ascii=False))
        db2 = SessionLocal()
        try:
            audit.record(db2, "model.register", username=username, resource_type="model",
                         resource_id=name, metadata={"path": path})
        finally:
            db2.close()
        await websocket.send_text(json.dumps({"type": "done", "model": name}))
    except (PermissionError, FileNotFoundError, ollama.OllamaError) as e:
        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False))
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


@router.get("/{model:path}/show")
async def show(model: str, user: User = Depends(require_permission("workflows.run"))):
    try:
        return await ollama.show(model)
    except ollama.OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e))


class KeepAliveBody(BaseModel):
    keep_alive: str | int | None = None


@router.post("/{model:path}/load")
async def load(model: str, body: KeepAliveBody, user: User = Depends(require_permission("workflows.edit"))):
    try:
        return await ollama.load(model, body.keep_alive)
    except ollama.OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{model:path}/unload")
async def unload(model: str, user: User = Depends(require_permission("workflows.edit"))):
    try:
        return await ollama.unload(model)
    except ollama.OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/{model:path}")
async def delete(
    model: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    try:
        await ollama.delete(model)
    except ollama.OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    audit.record(db, "model.delete", user=user, resource_type="model", resource_id=model, request=request)
    return {"ok": True}


@router.websocket("/pull")
async def pull(websocket: WebSocket):
    """モデル取得をストリーミングする。最初のメッセージ {model}。進捗を逐次返す。
    HuggingFace は model に hf.co/user/repo[:quant] を指定。"""
    db = SessionLocal()
    try:
        user = await authenticate_websocket(websocket, db, "workflows.edit")
        if user is None:
            return
    finally:
        db.close()
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        model = json.loads(first).get("model", "").strip()
    except (asyncio.TimeoutError, json.JSONDecodeError):
        await websocket.close(code=4400)
        return
    if not model:
        await websocket.send_text(json.dumps({"type": "error", "message": "モデル名が空です"}))
        await websocket.close()
        return
    try:
        async for chunk in ollama.pull_stream(model):
            await websocket.send_text(json.dumps({"type": "progress", **chunk}, ensure_ascii=False))
        await websocket.send_text(json.dumps({"type": "done"}))
    except ollama.OllamaError as e:
        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False))
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


# ---- llama.cpp ランタイム（第一級プロバイダー） ----


@router.get("/llama/status")
async def llama_status(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import llama

    st = llama.runtime_status()
    if st["installed"]:
        st["health"] = await llama.health()
    return st


@router.get("/llama/assets")
async def llama_assets(user: User = Depends(require_permission("workflows.edit"))):
    from app.models_mgmt import llama

    try:
        return {"tag": llama.DEFAULT_TAG, "assets": await llama.list_assets()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"リリース情報の取得に失敗: {e}")


class LlamaInstallBody(BaseModel):
    backend: str = Field(pattern="^(vulkan|rocm)$")  # CUDA は Ollama 利用のため対象外


@router.post("/llama/install-jobs", status_code=201)
async def llama_install(body: LlamaInstallBody, request: Request,
                        user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    """llama.cpp をサーバー側ジョブで導入する（ブラウザを閉じても継続）。"""
    from app.models_mgmt import llama

    backend = body.backend

    async def run(job: jobs.Job):
        return await llama.install_stream(job, backend)

    job = jobs.create("llama.install", f"llama.cpp 導入: {backend}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"), priority=-5)
    audit.record(db, "llama.install", user=user, resource_type="runtime", resource_id=backend,
                 request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/llama/switch")
async def llama_switch(body: LlamaInstallBody, request: Request,
                       user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    """導入済みの別バックエンド（rocm/vulkan/cuda）へ切り替える（再ダウンロード不要）。"""
    from app.models_mgmt import llama

    try:
        res = await asyncio.to_thread(llama.switch_backend, body.backend)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.record(db, "llama.switch", user=user, resource_type="runtime",
                 resource_id=body.backend, request=request)
    return res


@router.get("/llama/instance")
def llama_get_config(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import llama

    return llama.get_config()


class LlamaInstanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_path: str | None = None
    mmproj_path: str | None = None
    # 0 はモデル既定。上限は llama.cpp の受け付ける範囲に合わせず、文脈を
    # 食い潰さない現実的な幅で切る（1024 で 1 枚あたり約 1024 トークン）。
    image_min_tokens: int | None = Field(default=None, ge=0, le=16384)
    role: Literal["llm", "embedding", "reranker"] | None = None
    port: int | None = Field(default=None, ge=1024, le=65535)
    n_gpu_layers: int | None = Field(default=None, ge=0, le=999)
    ctx_size: int | None = Field(default=None, ge=0, le=1048576)
    deep_research_ctx_size: int | None = Field(default=None, ge=0, le=1048576)
    n_parallel: int | None = Field(default=None, ge=1, le=64)
    kv_unified: bool | None = None
    flash_attn: bool | None = None
    n_predict: int | None = Field(default=None, ge=-1, le=1048576)
    batch_size: int | None = Field(default=None, ge=32, le=65536)
    ubatch_size: int | None = Field(default=None, ge=32, le=65536)
    cache_type_k: Literal["f32", "f16", "bf16", "q8_0", "q4_0"] | None = None
    cache_type_v: Literal["f32", "f16", "bf16", "q8_0", "q4_0"] | None = None
    threads: int | None = Field(default=None, ge=-1, le=1024)
    threads_batch: int | None = Field(default=None, ge=-1, le=1024)
    mmap: bool | None = None
    mlock: bool | None = None
    # 種別はバイナリ側で増えるので Literal で固定せず、既知の上位集合で検証する。
    # カンマ区切りで複数指定できる（例: draft-mtp,ngram-mod）。
    spec_type: str | None = Field(default=None, max_length=128)
    draft_max: int | None = Field(default=None, ge=1, le=128)
    draft_min: int | None = Field(default=None, ge=0, le=128)
    draft_p_min: float | None = Field(default=None, ge=0, le=1)
    spec_draft_model_path: str | None = None
    spec_draft_ngl: int | None = Field(default=None, ge=-1, le=999)
    spec_draft_cache_type_k: Literal["", "f32", "f16", "bf16", "q8_0", "q4_0"] | None = None
    spec_draft_cache_type_v: Literal["", "f32", "f16", "bf16", "q8_0", "q4_0"] | None = None
    load_mode: Literal["", "auto", "none", "mmap", "mlock", "mmap+mlock", "dio"] | None = None

    @field_validator("spec_type")
    @classmethod
    def _known_spec_type(cls, value: str | None) -> str | None:
        from app.models_mgmt import llama

        if value is not None:
            llama.parse_spec_types(value)  # 未知の値・不正な組み合わせはここで弾く
        return value
    think: Literal["auto", "off", "low", "medium", "high", "xhigh", "custom"] | None = None
    think_budget_tokens: int | None = Field(default=None, ge=0, le=262144)
    cpu_moe: bool | None = None
    n_cpu_moe: int | None = Field(default=None, ge=0, le=256)
    temperature: float | None = Field(default=None, ge=0, le=5)
    top_k: int | None = Field(default=None, ge=0, le=10000)
    top_p: float | None = Field(default=None, ge=0, le=1)
    min_p: float | None = Field(default=None, ge=0, le=1)
    repeat_penalty: float | None = Field(default=None, ge=0, le=10)
    seed: int | None = Field(default=None, ge=-1, le=2147483647)
    alias: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    auto_start: bool | None = None
    idle_exclude: bool | None = None
    endpoint_id: str | None = Field(default=None, max_length=128)
    order: int | None = Field(default=None, ge=1, le=64)


@router.get("/llama/vision-detection")
def llama_vision_detection(
    model_path: str = Query(min_length=1, max_length=4096),
    user: User = Depends(require_permission("workflows.edit")),
):
    """GGUFと同じフォルダのmmproj候補を検出する。検出時点では有効化しない。"""
    from app.models_mgmt import llama

    validated = _llama_instance_patch(LlamaInstanceBody(model_path=model_path))
    candidates = llama.detect_vision_projectors(str(validated["model_path"]))
    return {
        "available": bool(candidates),
        "candidates": candidates,
        "suggested_path": candidates[0] if candidates else "",
        "enabled_by_default": False,
    }


def _llama_instance_patch(body: LlamaInstanceBody) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    from app.files import service as files

    for key in ("model_path", "mmproj_path", "spec_draft_model_path"):
        if key not in patch:
            continue
        raw = str(patch[key])
        if key != "model_path" and raw == "":  # 空文字はmmproj/ドラフト解除
            continue
        try:
            resolved = files.resolve(raw)
        except (PermissionError, FileNotFoundError) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if not resolved.is_file() or resolved.suffix.lower() != ".gguf":
            raise HTTPException(status_code=422, detail="許可ルート内のGGUFファイルを指定してください")
        patch[key] = str(resolved)
    return patch


def _validated_provider_patch(provider_id: str, body: dict) -> dict:
    patch: dict = body
    if provider_id == "llama.cpp":
        # 共通routeではmodel identity/path/portを変えず、既存の型・範囲検証を再利用する。
        forbidden = sorted(set(body) & {"alias", "model_path", "mmproj_path",
                                        "spec_draft_model_path", "role", "port",
                                        "endpoint_id", "order"})
        if forbidden:
            raise HTTPException(status_code=422, detail=f"共通設定APIでは変更できない項目です: {', '.join(forbidden)}")
        try:
            patch = LlamaInstanceBody.model_validate(body).model_dump(exclude_none=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return patch


@router.put("/llama/instance")
def llama_put_config(
    body: LlamaInstanceBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    patch = _llama_instance_patch(body)
    try:
        result = llama.save_config({"instance": patch})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "llama.instance_save", user=user, resource_type="runtime", request=request,
                 metadata={"alias": result.get("selected_alias")})
    return result


@router.get("/llama/instances")
async def llama_instances(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import llama

    instances = llama.list_instances()
    health = await asyncio.gather(*(llama.health(str(item["alias"])) for item in instances))
    return [{**item, "health": state} for item, state in zip(instances, health, strict=True)]


@router.post("/llama/instances", status_code=201)
def llama_create_instance(
    body: LlamaInstanceBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    patch = _llama_instance_patch(body)
    alias = str(patch.get("alias") or "")
    if not alias or not patch.get("model_path"):
        raise HTTPException(status_code=422, detail="aliasとGGUFモデルファイルは必須です")
    try:
        result = llama.save_instance(alias, patch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "llama.instance_create", user=user, resource_type="model", resource_id=alias,
                 request=request, metadata={"port": patch.get("port", 8080)})
    return result


@router.put("/llama/instances/{alias}")
def llama_update_instance(
    alias: str, body: LlamaInstanceBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    try:
        llama.get_instance(alias)
        result = llama.save_instance(alias, _llama_instance_patch(body))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "llama.instance_save", user=user, resource_type="model", resource_id=alias,
                 request=request)
    return result


@router.post("/llama/instances/{alias}/select")
def llama_select_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    try:
        result = llama.select_instance(alias)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, "llama.instance_select", user=user, resource_type="model", resource_id=alias,
                 request=request)
    return result


class DeleteInstanceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 既定は設定だけ削除。GGUF 本体の削除は取り消せないので明示指定を要る。
    delete_file: bool = False


@router.post("/llama/instances/{alias}/delete")
def llama_delete_instance(
    alias: str, request: Request,
    body: DeleteInstanceBody | None = None,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    delete_file = bool(body.delete_file) if body else False
    try:
        result = llama.delete_instance(alias, delete_file=delete_file)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, "llama.instance_delete", user=user, resource_type="model", resource_id=alias,
                 request=request, metadata={"gguf_deleted": result["gguf_deleted"],
                                            "requested_file_delete": delete_file})
    return {"ok": True, **result}


@router.get("/llama/capacity")
async def llama_capacity(user: User = Depends(require_permission("workflows.run"))):
    """稼働中エンドポイントの利用状況をまとめて返す（ダッシュボード表示用）。

    LLM並列（使用中/最大）・待ち行列・KV使用量を1回で取れるようにする。
    OMo 導入時は設定済みの論理並列も添えて、負荷の見当を付けられるようにする。
    """
    from app.models_mgmt import llama

    # 表示対象はチャット用モデル（role=llm）だけ。embedding / reranker は
    # RAG の補助で並列駆動の対象ではなく、並べると本来見たい行が埋もれる。
    chat_aliases = {
        str(i["alias"]) for i in llama.list_instances()
        if str(i.get("role", "llm")) == "llm"
    }
    running = [
        e for e in llama.list_endpoints()
        if e.get("running_alias") and str(e["running_alias"]) in chat_aliases
    ]
    capacities = await asyncio.gather(*(
        llama.endpoint_capacity(int(e["port"])) for e in running
    )) if running else []

    # 起動に失敗しているモデルは「読み込み待ち」と紛らわしいので、理由を添えて別に返す。
    failed = [
        {"alias": str(i["alias"]), "port": int(i.get("port") or 0),
         "error": str(i.get("last_error") or "")}
        for i in llama.list_instances()
        if str(i.get("role", "llm")) == "llm" and i.get("runtime_status") == "FAILED"
    ]

    endpoints = [
        {"id": e["id"], "label": e["label"], "port": e["port"],
         "running_alias": e["running_alias"], **capacity}
        for e, capacity in zip(running, capacities, strict=True)
    ]
    # Strata / Maya-S / Radiance report no llama.cpp-style /metrics: ControlDeck times the
    # requests it relays to them (live_stats). Same shape, so the widget shows them alike.
    from app.models_mgmt import live_stats, local_llm
    from app.monitoring.electricity import accumulator
    others = [item for item in await asyncio.to_thread(local_llm.llm_instances)
              if item.get("runtime") != "llama.cpp" and item.get("loaded")]
    contexts = await asyncio.gather(*(_engine_context(item) for item in others)) if others else []
    for item, (ctx_active, ctx_max) in zip(others, contexts, strict=True):
        port = int(item.get("port") or 0)
        stats = live_stats.snapshot(port)
        slots = max(1, int(item.get("n_parallel") or 1))
        ctx = ctx_max or int(item.get("ctx_size") or 0)
        endpoints.append({
            "id": f"{item['runtime']}:{item['alias']}", "label": str(item["alias"]), "port": port,
            "running_alias": str(item["alias"]), "runtime": str(item["runtime"]),
            "available": True, "slots": slots, "busy": min(slots, stats["busy"]),
            "ctx_total": ctx, "ctx_used": 0, "ctx_free": ctx, "usable": ctx,
            "deferred": max(0, stats["busy"] - slots), "accepting": stats["busy"] < slots,
            "tokens_per_second": stats["tokens_per_second"],
            "tokens_per_second_single": stats["tokens_per_second_single"],
            "phase": stats["phase"], "prefill_tokens_per_second": stats["prefill_tokens_per_second"],
            "prefilling": stats["prefilling"], "prompt_tokens": 0, "prompt_tokens_done": 0,
            "timed_by_controldeck": True,
            # 実行中（無ければ直前）の 1 本の「プロンプト + 生成」。KV の使用量ではない。
            "ctx_active": ctx_active,
        })
    # 動いているモデルが無いとき、Home に出す「最後に見えていたローカル LLM」（アンロード中の表示）。
    from app.models_mgmt import llm_last_seen
    last_local = await asyncio.to_thread(llm_last_seen.update, endpoints)
    # LLM が処理していた間の PC 全体の電力と電気代（Home の LLM カード）。
    return {"endpoints": endpoints, "failed": failed, "power": accumulator.llm_snapshot(),
            "last_local": last_local}


async def _engine_context(item: dict) -> tuple[int | None, int | None]:
    """(コンテキストに入っているトークン数, 最大)。Strata と Maya-S は自分の /metrics を読み、
    それ以外（Radiance）は ControlDeck が中継した直前の 1 本から数える。"""
    import httpx

    from app.models_mgmt import live_stats

    port = int(item.get("port") or 0)
    if item.get("runtime") in ("strata", "maya-s") and port:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=1.5) as client:
                response = await client.get(f"http://127.0.0.1:{port}/metrics")
            if response.status_code == 200:
                data = response.json()
                engine = data.get("engine") if isinstance(data, dict) and isinstance(data.get("engine"), dict) else {}
                limit = engine.get("max_context")
                return (live_stats.context_from_metrics(data),
                        int(limit) if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else None)
        except (httpx.HTTPError, ValueError):
            pass
    return live_stats.last_context(port), None


@router.get("/llama/endpoints/{endpoint_id}/capacity")
async def llama_endpoint_capacity(
    endpoint_id: str, user: User = Depends(require_permission("workflows.run")),
):
    """KVプールの使用状況。共有KVでは総量が尽きると即エラーになるため、UIで残量を見せる。"""
    from app.models_mgmt import llama

    endpoint = next((e for e in llama.list_endpoints() if e["id"] == endpoint_id), None)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="エンドポイントが見つかりません")
    return await llama.endpoint_capacity(int(endpoint["port"]))


@router.get("/hf/search")
async def hf_search_repos(q: str, user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import hf

    if not q.strip():
        return []
    try:
        return await hf.search_models(q.strip())
    except hf.HfError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/hf/repos/{repo:path}/files")
async def hf_repo_files(repo: str, revision: str = "main",
                        user: User = Depends(require_permission("workflows.run"))):
    """repo 内の GGUF を量子化バリアントとして返す。保存先の空き容量も添える。"""
    from app.models_mgmt import hf, libraries

    try:
        files = await hf.list_repo_files(repo, revision)
    except hf.HfError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"repo": repo, "variants": files, "libraries": libraries.list_libraries()}


class HfSettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(default="", max_length=200)


@router.get("/hf/settings")
def hf_settings(user: User = Depends(require_permission("workflows.edit"))):
    from app.models_mgmt import hf

    return {"has_token": hf.has_token()}


@router.put("/hf/settings")
def put_hf_settings(
    body: HfSettingsBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """gated repo 用のトークン。値はログにも監査にも残さない。"""
    from app.models_mgmt import hf

    hf.set_token(body.token)
    audit.record(db, "model.hf_token", user=user, resource_type="model", request=request,
                 metadata={"configured": bool(body.token.strip())})
    return {"has_token": hf.has_token()}


class HfDownloadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(min_length=1, max_length=200)
    files: list[str] = Field(min_length=1, max_length=64)
    revision: str = Field(default="main", max_length=100)
    library_id: str = Field(default="", max_length=64)
    expected_bytes: int = Field(default=0, ge=0)
    # 完了後に llama.cpp instance として登録する場合だけ指定する
    alias: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    role: Literal["llm", "embedding", "reranker"] | None = None
    endpoint_id: str | None = Field(default=None, max_length=128)
    port: int | None = Field(default=None, ge=1024, le=65535)


@router.post("/hf/download-jobs", status_code=201)
async def hf_download(
    body: HfDownloadBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """GGUF 取得をサーバー側ジョブで行う（ブラウザを閉じても継続）。"""
    from app.models_mgmt import hf

    register = None
    if body.alias:
        register = {"alias": body.alias, "role": body.role,
                    "endpoint_id": body.endpoint_id, "port": body.port}

    async def run(job: jobs.Job):
        return await hf.download(
            job, body.repo, body.files, library_id=body.library_id,
            revision=body.revision, expected_bytes=body.expected_bytes, register=register,
        )

    job = jobs.create("model.hf_download", f"HuggingFace取得: {body.repo}", run,
                      owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"), priority=0)
    audit.record(db, "model.hf_download", user=user, resource_type="model",
                 resource_id=body.repo, request=request,
                 metadata={"job_id": job.id, "files": len(body.files), "alias": body.alias or ""})
    return {"job_id": job.id}


@router.get("/llm-gateway")
def llm_gateway_settings(user: User = Depends(require_permission("workflows.edit"))):
    """ゲートウェイの接続情報。OpenCode 等の直結クライアント向け。"""
    from app.config import get_config as app_config
    from app.models_mgmt import gateway

    key = gateway.get_api_key()
    port = int(app_config().server.port)
    return {
        "issued": bool(key),
        "api_key": key,
        "base_url": f"http://127.0.0.1:{port}/api/v1/llm/v1",
    }


@router.post("/llm-gateway/key")
def llm_gateway_issue_key(
    request: Request, rotate: bool = False,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """APIキーを発行／再発行する。再発行すると既存クライアントは繋がらなくなる。"""
    from app.models_mgmt import gateway

    key = gateway.rotate_api_key() if rotate else gateway.get_api_key(create=True)
    audit.record(db, "llm_gateway.key", user=user, resource_type="runtime",
                 request=request, metadata={"rotated": bool(rotate)})
    return {"api_key": key}


@router.get("/llama/endpoints")
def llama_endpoints(user: User = Depends(require_permission("workflows.run"))):
    """エンドポイント（待受ポート）一覧。所属モデルと稼働中モデルを添える。"""
    from app.models_mgmt import llama

    return llama.list_endpoints()


class LlamaEndpointBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=128)
    port: int | None = Field(default=None, ge=1024, le=65535)


@router.put("/llama/endpoints/{endpoint_id}")
def llama_save_endpoint(
    endpoint_id: str, body: LlamaEndpointBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    try:
        result = llama.save_endpoint(endpoint_id, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "llama.endpoint_save", user=user, resource_type="runtime",
                 resource_id=endpoint_id, request=request, metadata={"port": result.get("port")})
    return result


@router.post("/llama/endpoints/{endpoint_id}/delete")
def llama_delete_endpoint(
    endpoint_id: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    try:
        llama.delete_endpoint(endpoint_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(db, "llama.endpoint_delete", user=user, resource_type="runtime",
                 resource_id=endpoint_id, request=request)
    return {"ok": True}


class ReorderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: list[str] = Field(min_length=1, max_length=64)


@router.post("/llama/instances/reorder")
def llama_reorder_instances(
    body: ReorderBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """一覧の並び＝優先度。自動起動・オンデマンド起動・既定モデルの選択に効く。"""
    from app.models_mgmt import llama

    try:
        result = llama.reorder_instances(body.order)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, "llama.instance_reorder", user=user, resource_type="model",
                 request=request, metadata={"order": body.order})
    return result


class DuplicateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    endpoint_id: str | None = Field(default=None, max_length=128)


@router.post("/llama/instances/{alias}/duplicate", status_code=201)
def llama_duplicate_instance(
    alias: str, body: DuplicateBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """設定を複製する。既定では同じエンドポイントに載せる（モデル切替用途）。"""
    from app.models_mgmt import llama

    try:
        llama.duplicate_instance(alias, body.alias, endpoint_id=body.endpoint_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "llama.instance_duplicate", user=user, resource_type="model",
                 resource_id=body.alias, request=request, metadata={"source": alias})
    return {"ok": True, "alias": body.alias}


@router.post("/llama/start")
async def llama_start(request: Request, user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    from app.models_mgmt import llama

    ok, err = await asyncio.to_thread(llama.start_instance)
    if not ok:
        raise HTTPException(status_code=502, detail=err or "起動に失敗しました")
    audit.record(db, "llama.start", user=user, resource_type="runtime", request=request)
    return {"ok": True}


@router.post("/llama/stop")
async def llama_stop(
    request: Request, user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    ok, err = await asyncio.to_thread(llama.stop_instance)
    audit.record(db, "llama.stop", user=user, resource_type="runtime", request=request)
    return {"ok": ok, "error": err}


@router.get("/embedding-endpoints")
async def embedding_endpoints(user: User = Depends(require_permission("workflows.run"))):
    """RAG設定用の埋め込みモデル候補（管理済みモデルから選択できるようにする）。

    role=embedding のllama.cpp instance（自動起動対応）と、Ollamaの埋め込み系
    モデル（名前ヒューリスティック）を返す。
    """
    import re as _re

    from app.models_mgmt import llama

    endpoints = []
    for item in llama.list_instances():
        if str(item.get("role", "llm")) == "embedding":
            endpoints.append({
                "label": f"{item['alias']}（llama.cpp・自動起動）",
                "base_url": str(item["base_url"]), "model": str(item["alias"]),
            })
    try:
        for m in await ollama.list_models():
            if _re.search(r"embed|bge|e5|gte|minilm", str(m["name"]), _re.I):
                endpoints.append({
                    "label": f"{m['name']}（Ollama）",
                    "base_url": ollama.base_url() + "/v1", "model": str(m["name"]),
                })
    except ollama.OllamaError:
        pass
    return {"endpoints": endpoints}


@router.get("/llama/role-presets")
def llama_role_presets(user: User = Depends(require_permission("workflows.run"))):
    """Embed/Reranker 推奨プリセットの導入・稼働状態。"""
    from app.models_mgmt import role_presets

    return {"presets": role_presets.preset_status()}


@router.post("/llama/role-presets/{preset_id}/install-jobs", status_code=201)
async def llama_role_preset_install(
    preset_id: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """プリセットのGGUFダウンロード+instance登録をサーバー側ジョブで行う。"""
    from app.models_mgmt import role_presets

    preset = role_presets.ROLE_PRESETS.get(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="未知のプリセットです")

    async def run(job: jobs.Job):
        return await role_presets.install(job, preset_id)

    job = jobs.create("model.preset", f"導入: {preset['label']}", run, owner_user_id=user.id,
                      idempotency_key=request.headers.get("idempotency-key"), priority=0)
    audit.record(db, "model.preset_install", user=user, resource_type="model",
                 resource_id=preset_id, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


class RetrievalSelectionBody(BaseModel):
    chat: str | None = None
    knowledge: str | None = None


@router.get("/retrieval")
def retrieval_models(user: User = Depends(require_permission("workflows.run"))):
    """RAG の補助モデル（埋め込み・reranker）の一覧と、使う reranker の選択。

    2026-11-20 利用者「Models タブで導入できるように。どのモデルを使うか選べるように」。CPU の reranker
    （xsmall・ruri）と、GPU の llama.cpp のプリセット（bge-m3・Qwen3-Reranker-4B）を同じ場所に並べる。"""
    from app.models_mgmt import llama, role_presets
    from app.workflows import cpu_reranker

    gpu = [p for p in role_presets.preset_status() if p.get("role") == "reranker"]
    return {
        "cpu_rerankers": [cpu_reranker.status(m) for m in cpu_reranker.MODELS],
        "gpu_reranker_installed": llama.find_role_instance("reranker") is not None,
        "gpu_rerankers": gpu,
        "selection": cpu_reranker.selection(),
        "choices": list(cpu_reranker.CHOICES),
        "in_use": {"chat": cpu_reranker.choose(True), "knowledge": cpu_reranker.choose(False)},
    }


@router.put("/retrieval/selection")
def retrieval_select(body: RetrievalSelectionBody, request: Request,
                     user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    from app.workflows import cpu_reranker

    values = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        selected = cpu_reranker.set_selection(values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "model.retrieval_select", user=user, resource_type="model", resource_id="reranker",
                 request=request, metadata=selected)
    return {"selection": selected, "in_use": {"chat": cpu_reranker.choose(True), "knowledge": cpu_reranker.choose(False)}}


@router.post("/retrieval/cpu/{model_id}/install-jobs", status_code=201)
async def retrieval_cpu_install(model_id: str, request: Request,
                          user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    """reranker をタップ 1 つで導入する（取得・ruri は変換・実行環境・試しの並べ替え）。

    async で受ける: ジョブは走っているイベントループへ載せる。同期関数だとスレッドで動きループが無く、ジョブの記録だけが
    残って「処理中…」が消えず、画面には 500 が一瞬出ていた（2026-11-20 本番 v0.87）。"""
    from app.workflows import cpu_reranker

    if model_id not in cpu_reranker.MODELS:
        raise HTTPException(status_code=404, detail="未知のモデルです")
    job = cpu_reranker.start_install_job(model_id, user.id)
    audit.record(db, "model.retrieval_install", user=user, resource_type="model", resource_id=model_id,
                 request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/retrieval/cpu/{model_id}/uninstall")
def retrieval_cpu_uninstall(model_id: str, request: Request,
                            user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    """使わなくする。取得・変換したファイル（保管庫）は残す（入れ直しで取り直さない）。"""
    from app.workflows import cpu_reranker

    if model_id not in cpu_reranker.MODELS:
        raise HTTPException(status_code=404, detail="未知のモデルです")
    state = cpu_reranker.uninstall(model_id)
    audit.record(db, "model.retrieval_uninstall", user=user, resource_type="model", resource_id=model_id, request=request)
    return state


class RetrievalResidentBody(BaseModel):
    value: bool


@router.post("/retrieval/cpu/{model_id}/{action}")
async def retrieval_cpu_load(model_id: str, action: str, request: Request,
                             user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    """reranker を先に載せる（load）・止める（unload）。GPU の ruri は空きが足りなければ載せない。"""
    from app.workflows import cpu_reranker

    if model_id not in cpu_reranker.MODELS or action not in ("load", "unload"):
        raise HTTPException(status_code=404, detail="未知のモデルか操作です")
    if action == "load":
        try:
            await cpu_reranker.warm(model_id)
        except cpu_reranker.CpuRerankerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        worker = cpu_reranker._workers.get(model_id)
        if worker is not None:
            await worker.stop()
    audit.record(db, f"model.retrieval_{action}", user=user, resource_type="model", resource_id=model_id, request=request)
    return cpu_reranker.status(model_id)


@router.put("/retrieval/cpu/{model_id}/resident")
def retrieval_cpu_resident(model_id: str, body: RetrievalResidentBody, request: Request,
                           user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    """常駐（使われなくなっても止めない）。GPU のものも、大きな LLM を起動する前には止める。"""
    from app.workflows import cpu_reranker

    if model_id not in cpu_reranker.MODELS:
        raise HTTPException(status_code=404, detail="未知のモデルです")
    cpu_reranker.set_resident(model_id, body.value)
    audit.record(db, "model.retrieval_resident", user=user, resource_type="model", resource_id=model_id,
                 request=request, metadata={"value": body.value})
    return cpu_reranker.status(model_id)


@router.post("/llama/role-presets/{preset_id}/uninstall")
async def llama_role_preset_uninstall(preset_id: str, request: Request,
                                      user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db)):
    """GPU の埋め込み・reranker（llama.cpp のプリセット）を外す: 止めて登録を消し、GGUF も消す（ほかで使っていなければ）。"""
    from app.models_mgmt import llama, role_presets

    preset = role_presets.ROLE_PRESETS.get(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="未知のプリセットです")
    try:
        result = await asyncio.to_thread(llama.delete_instance, str(preset["alias"]), delete_file=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="導入されていません") from exc
    audit.record(db, "model.preset_uninstall", user=user, resource_type="model", resource_id=preset_id,
                 request=request, metadata=result)
    return result


@router.get("/llama/options")
async def llama_options(user: User = Depends(require_permission("workflows.edit"))):
    """稼働バイナリの --help から利用可能な引数を返す（実在オプションのみ UI 表示するため）。"""
    from app.models_mgmt import llama

    return {
        "flags": await llama.detect_options(),
        # 投機デコードの種別は版ごとに増える。UIの選択肢はバイナリ実物から出す。
        "spec_types": await llama.detect_spec_types(),
        "spec_types_needing_draft_model": list(llama.SPEC_TYPES_NEEDING_DRAFT_MODEL),
        "draft_cache_types": [value for value in llama.DRAFT_CACHE_TYPES if value],
        # b10793 で --mmap / --mlock は --load-mode へ集約された。
        "load_modes": [value for value in llama.LOAD_MODES if value],
        "supports_load_mode": llama.supports_load_mode(),
    }

# ---- Opt-in Strata Terminal / gateway-managed models ----

class StrataModelBody(BaseModel):
    kv: Literal["int8", "q4_0", "k8v4", "fp16"] = "int8"
    vision: Literal["on", "off", "gpu", "cpu"] = "on"
    parallel: int = Field(default=1, ge=1, le=8)
    kv_resident: int | None = Field(default=None, ge=4096, le=1048576, multiple_of=1024)
    ram_keep_gib: float | None = Field(default=None, ge=0, le=256, allow_inf_nan=False)
    drafting: Literal["auto", "mtp", "dflash2", "off"] = "auto"
    model_path: str = Field(default="", max_length=4096)
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(min_length=1, max_length=48)
    family: Literal["qwen", "unsloth", "deepseek41"] = "qwen"
    model: str = Field(min_length=1, max_length=50)
    # Any "<N>k" from 4k to 1024k; strata_gateway.valid_context checks the range.
    context: str = Field(default="32k", pattern=r"^[1-9][0-9]{0,3}k$")
    port: int = Field(default=8095, ge=8081, le=65535)
    # Lifecycle, as llama.cpp's per-model settings. Changeable while running.
    idle_exclude: bool = False
    auto_start: bool = False
    # Default thinking for requests that name none ("" = the model's own, xhigh) and its cap (0 = none).
    reasoning_effort: Literal["", "low", "medium", "xhigh"] = ""
    thinking_budget: int = Field(default=0, ge=0, le=262144)


@router.get("/strata/instances")
def strata_instances(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import strata, strata_gateway
    return {"runtime": strata.runtime_status(), "instances": strata_gateway.list_instances()}


@router.post("/strata/memory-estimate")
def strata_memory_estimate(body: StrataModelBody, user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import strata_gateway, strata_memory
    if not strata_gateway.valid_context(body.context) or body.model not in strata_gateway._FAMILIES[body.family]:
        raise HTTPException(status_code=422, detail="モデルまたはコンテキストが不正です")
    cfg = body.model_dump()
    return strata_memory.estimate(cfg, strata_memory.hardware(), strata_memory.model_info(cfg))


@router.get("/strata/instances/{alias}/memory")
def strata_memory_allocations(alias: str, user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import strata_gateway, strata_memory
    try:
        cfg = strata_gateway.get_instance(alias)
        return strata_memory.observed(alias, cfg)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Strataモデルが見つかりません") from exc


@router.post("/strata/instances", status_code=201)
def strata_create_instance(
    body: StrataModelBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import strata_gateway

    try:
        result = strata_gateway.save_instance(
            body.alias, family=body.family, model=body.model,
            context=body.context, port=body.port, model_path=body.model_path,
            kv=body.kv, vision=body.vision, parallel=body.parallel, drafting=body.drafting,
            idle_exclude=body.idle_exclude, auto_start=body.auto_start,
            reasoning_effort=body.reasoning_effort, thinking_budget=body.thinking_budget,
            kv_resident=body.kv_resident, ram_keep_gib=body.ram_keep_gib,
        )
    except strata_gateway.StrataGatewayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "strata.instance_create", user=user, resource_type="model",
                 resource_id=body.alias, request=request)
    return result


@router.put("/strata/instances/{alias}")
def strata_update_instance(
    alias: str, body: StrataModelBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import strata_gateway
    if alias != body.alias:
        raise HTTPException(status_code=422, detail="モデル別名は変更できません")
    try:
        payload = body.model_dump()
        for key in {"kv_resident", "ram_keep_gib"} - body.model_fields_set:
            payload.pop(key)
        result = strata_gateway.save_instance(**payload, replace=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Strataモデル設定がありません") from exc
    except strata_gateway.StrataGatewayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "strata.instance_update", user=user, resource_type="model",
                 resource_id=alias, request=request)
    return result


@router.post("/strata/instances/{alias}/start-jobs", status_code=201)
async def strata_start_job(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import strata_gateway
    if alias not in {item["alias"] for item in await asyncio.to_thread(strata_gateway.list_instances)}:
        raise HTTPException(status_code=404, detail="Strataモデル設定がありません")

    async def run(job: jobs.Job) -> dict:
        job.set_progress("Strataモデルを取得・起動しています。容量と環境により時間がかかります", 0, 1)
        from app.models_mgmt import provider_adapters
        await provider_adapters.load_model("strata", alias)
        job.set_progress("起動完了", 1, 1)
        return {"alias": alias, "loaded": True}

    job = jobs.create("model.strata.start", f"Strata初回起動: {alias}", run,
                      owner_user_id=user.id, idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "strata.start", user=user, resource_type="model",
                 resource_id=alias, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/strata/instances/{alias}/stop")
async def strata_stop_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import strata_gateway
    ok, error = await asyncio.to_thread(strata_gateway.stop_instance, alias)
    if not ok:
        raise HTTPException(status_code=422, detail=error)
    audit.record(db, "strata.stop", user=user, resource_type="model",
                 resource_id=alias, request=request)
    return {"alias": alias, "loaded": False}


@router.post("/strata/instances/{alias}/delete")
def strata_delete_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import strata_gateway
    try:
        result = strata_gateway.delete_instance(alias)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Strataモデル設定がありません") from exc
    except strata_gateway.StrataGatewayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "strata.instance_delete", user=user, resource_type="model",
                 resource_id=alias, request=request)
    return result


# ---- Radiance（StillDeadcode/radiance）: Strata と同じ形の登録・起動・停止 ----

class RadianceModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(min_length=1, max_length=48)
    model_path: str = Field(min_length=1, max_length=4096)
    port: int = Field(default=8100, ge=8081, le=65535)
    context: int = Field(default=131072, ge=4096, le=1_048_576)
    # bf16 is exact; i8 halves the cache for a longer context (fp8 is GLM-5.3's latent cache only).
    kv_cache: Literal["bf16", "i8", "fp8"] = "bf16"
    max_num_seqs: int = Field(default=4, ge=1, le=32)
    # None: the container's own drafter depth ("auto"); 0 turns speculative decoding off.
    speculative_tokens: int | None = Field(default=None, ge=0, le=15)
    gpu_headroom_mib: int = Field(default=1024, ge=96, le=16384)
    # Default thinking for requests that name none ("" = the template's own, xhigh) and its cap (0 = none).
    reasoning_effort: Literal["", "none", "low", "medium", "xhigh"] = ""
    thinking_budget: int = Field(default=0, ge=0, le=262144)
    # More VRAM for the cache (delta-net state f16, vision tower in RAM); weights may leave the card (slow).
    memory_saver: bool = False
    ram_offload: bool = False
    # Lifecycle, as llama.cpp's per-model settings. Changeable while running.
    on_demand_start: bool = True
    idle_exclude: bool = False
    auto_start: bool = False


@router.get("/radiance/instances")
def radiance_instances(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import radiance
    return {"runtime": radiance.runtime_status(),
            "instances": radiance.list_instances() if radiance.installed() else []}


@router.post("/radiance/instances", status_code=201)
def radiance_create_instance(
    body: RadianceModelBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import radiance
    values = body.model_dump()
    try:
        result = radiance.save_instance(values.pop("alias"), **values)
    except radiance.RadianceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "radiance.instance_create", user=user, resource_type="model",
                 resource_id=body.alias, request=request)
    return result


@router.put("/radiance/instances/{alias}")
def radiance_update_instance(
    alias: str, body: RadianceModelBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import radiance
    if alias != body.alias:
        raise HTTPException(status_code=422, detail="モデル別名は変更できません")
    values = body.model_dump()
    values.pop("alias")
    try:
        result = radiance.save_instance(alias, **values, replace=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Radianceモデル設定がありません") from exc
    except radiance.RadianceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "radiance.instance_update", user=user, resource_type="model",
                 resource_id=alias, request=request)
    return result


@router.post("/radiance/instances/{alias}/start-jobs", status_code=201)
async def radiance_start_job(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import radiance
    if alias not in {item["alias"] for item in await asyncio.to_thread(radiance.list_instances)}:
        raise HTTPException(status_code=404, detail="Radianceモデル設定がありません")

    async def run(job: jobs.Job) -> dict:
        job.set_progress("Radianceモデルを読み込んでいます", 0, 1)
        from app.models_mgmt import provider_adapters
        await provider_adapters.load_model("radiance", alias)
        job.set_progress("起動完了", 1, 1)
        return {"alias": alias, "loaded": True}

    job = jobs.create("model.radiance.start", f"Radiance起動: {alias}", run,
                      owner_user_id=user.id, idempotency_key=request.headers.get("idempotency-key"))
    audit.record(db, "radiance.start", user=user, resource_type="model",
                 resource_id=alias, request=request, metadata={"job_id": job.id})
    return {"job_id": job.id}


@router.post("/radiance/instances/{alias}/stop")
async def radiance_stop_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import radiance
    ok, error = await asyncio.to_thread(radiance.stop_instance, alias)
    if not ok:
        raise HTTPException(status_code=422, detail=error)
    audit.record(db, "radiance.stop", user=user, resource_type="model",
                 resource_id=alias, request=request)
    return {"alias": alias, "loaded": False}


@router.post("/radiance/instances/{alias}/delete")
def radiance_delete_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import radiance
    try:
        result = radiance.delete_instance(alias)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Radianceモデル設定がありません") from exc
    except radiance.RadianceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "radiance.instance_delete", user=user, resource_type="model",
                 resource_id=alias, request=request)
    return result


@router.get("/radiance/instances/{alias}/log")
def radiance_instance_log(alias: str, user: User = Depends(require_permission("workflows.edit"))):
    from app.models_mgmt import radiance
    if alias not in {item["alias"] for item in radiance.list_instances()}:
        raise HTTPException(status_code=404, detail="Radianceモデル設定がありません")
    return {"alias": alias, "log": radiance.log_tail(alias, 80)}


# ---- 外部のフロンティアモデル（docs/PLAN-frontier-models.md） ----
# 秘密値と外部への送信を扱うので、見るのも変えるのも settings.manage に限る。


def _cloud_error(exc) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/cloud")
def cloud_status(user: User = Depends(require_permission("settings.manage"))):
    from app.models_mgmt import cloud

    return cloud.status()


@router.put("/cloud/egress")
def cloud_egress(body: dict, request: Request,
                 user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.models_mgmt import cloud

    try:
        result = cloud.save_egress(body)
    except cloud.CloudError as exc:
        raise _cloud_error(exc) from exc
    audit.record(db, "models.cloud.egress", user=user, resource_type="cloud", request=request,
                 metadata={key: bool(value) for key, value in body.items()})
    return result


@router.put("/cloud/{provider_id}")
def cloud_save(provider_id: str, body: dict, request: Request,
               user: User = Depends(require_permission("settings.manage")), db=Depends(get_db)):
    from app.models_mgmt import cloud

    try:
        result = cloud.save_provider(provider_id, body)
    except cloud.CloudError as exc:
        raise _cloud_error(exc) from exc
    # キーの値は記録しない。変えたという事実と、どの項目かだけ。
    audit.record(db, "models.cloud.provider", user=user, resource_type="cloud", resource_id=provider_id,
                 request=request, metadata={"fields": sorted(body), "key_set": bool(body.get("api_key"))})
    return result


@router.get("/cloud/{provider_id}/remote-models")
async def cloud_remote_models(provider_id: str, user: User = Depends(require_permission("settings.manage"))):
    from app.models_mgmt import cloud

    try:
        return {"models": await cloud.list_remote_models(provider_id)}
    except cloud.CloudError as exc:
        raise _cloud_error(exc) from exc


@router.get("/cloud/{provider_id}/account")
async def cloud_account(provider_id: str, user: User = Depends(require_permission("settings.manage"))):
    from app.models_mgmt import cloud

    try:
        return await cloud.account(provider_id)
    except cloud.CloudError as exc:
        raise _cloud_error(exc) from exc


@router.get("/cloud-usage")
def cloud_usage(days: int = Query(default=30, ge=1, le=366),
                user: User = Depends(require_permission("settings.manage"))):
    import time as _time

    from app.models_mgmt import cloud

    return {"rows": cloud.usage_rows(since=_time.time() - days * 86400, limit=2000)}


@router.get("/cloud-summary")
async def cloud_summary(user: User = Depends(require_permission("settings.manage"))):
    """Home の LLM カード用。実行中の呼び出し・今日/今月の合計・直前の呼び出し（本文なし）。"""
    from app.models_mgmt import cloud

    data = await asyncio.to_thread(cloud.summary)
    last = data.get("last")
    # ctx の分母（モデルのコンテキスト長）を知らなければ、モデル一覧を 1 時間に 1 回まで取りに行く。
    if last and last.get("context_length") is None and cloud.needs_context_lengths(str(last["provider"]), str(last["model"])):
        try:
            await cloud.list_remote_models(str(last["provider"]))
        except (cloud.CloudError, ValueError):  # 長さが分からないだけ。Home の表示は分母なしで続ける
            pass
        last["context_length"] = cloud.context_length(str(last["provider"]), str(last["model"]))
    return data
