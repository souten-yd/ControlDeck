"""Model（Ollama）管理 API。"""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from pydantic import BaseModel, ConfigDict, Field

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


@router.put("/runtime-policy")
async def put_runtime_policy(
    body: dict, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import runtime_policy

    current = runtime_policy.get_policy().model_dump()
    merged = {**current, **body}
    for nested in ("chat", "deep_research", "amd_gpu"):
        if isinstance(current.get(nested), dict) and isinstance(body.get(nested), dict):
            merged[nested] = {**current[nested], **body[nested]}
    try:
        policy = runtime_policy.RuntimePolicy.model_validate(merged)
        policy = runtime_policy.normalize_gpu_profile(policy)
        from app.models_mgmt import amd_gpu
        amd_gpu.apply_profile(policy.amd_gpu, force=True)
        await runtime_policy.apply_selection(policy)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    runtime_policy.save_policy(policy)
    audit.record(db, "model.runtime_policy", user=user, resource_type="model-runtime",
                 request=request, metadata={"runtime": policy.selected_runtime,
                                            "backend": policy.selected_backend,
                                            "coexistence": policy.coexistence,
                                            "supervision": policy.supervision,
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
    idle_unload_enabled: bool | None = None
    idle_unload_minutes: int | None = Field(default=None, ge=1, le=1440)
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
    spec_type: Literal["none", "draft-simple", "draft-mtp", "ngram-simple"] | None = None
    draft_max: int | None = Field(default=None, ge=1, le=128)
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

    for key in ("model_path", "mmproj_path"):
        if key not in patch:
            continue
        raw = str(patch[key])
        if key == "mmproj_path" and raw == "":  # 空文字はmmproj解除
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
        forbidden = sorted(set(body) & {"alias", "model_path", "mmproj_path", "role", "port",
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

    omo: dict | None = None
    try:
        from app.features.registry import is_enabled

        if is_enabled("omo"):
            from app.integrations.opencode.provider import get_settings, omo_concurrency_for

            settings = get_settings()
            gated = bool(settings.get("use_gateway"))
            # OMo が実際に使うモデルのスロット数で決める。
            # 先頭のエンドポイントを見ると、無関係なモデルの値を拾ってしまう。
            # autoのような仮想モデルは、実際の転送先へ解決してから並列数と表示名を決める。
            from app.models_mgmt import gateway

            target = str(settings.get("model") or "")
            try:
                target = gateway.resolve_endpoint(target)[0]
            except Exception:  # noqa: BLE001 - 未登録なら設定値のまま見せる
                pass
            slots = next(
                (int(i.get("n_parallel") or 1) for i in llama.list_instances()
                 if str(i.get("alias")) == target), 1,
            )
            concurrency, team = omo_concurrency_for(slots, gated=gated)
            omo = {"installed": True, "model": target, "slots": slots,
                   "concurrency": concurrency, "team_parallel": team, "gated": gated}
    except Exception:  # noqa: BLE001 - 表示用なので失敗しても他を返す
        omo = None

    # 起動に失敗しているモデルは「読み込み待ち」と紛らわしいので、理由を添えて別に返す。
    failed = [
        {"alias": str(i["alias"]), "port": int(i.get("port") or 0),
         "error": str(i.get("last_error") or "")}
        for i in llama.list_instances()
        if str(i.get("role", "llm")) == "llm" and i.get("runtime_status") == "FAILED"
    ]

    return {
        "endpoints": [
            {"id": e["id"], "label": e["label"], "port": e["port"],
             "running_alias": e["running_alias"], **capacity}
            for e, capacity in zip(running, capacities, strict=True)
        ],
        "failed": failed,
        "omo": omo,
    }


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


@router.get("/llama/options")
async def llama_options(user: User = Depends(require_permission("workflows.edit"))):
    """稼働バイナリの --help から利用可能な引数を返す（実在オプションのみ UI 表示するため）。"""
    from app.models_mgmt import llama

    return {"flags": await llama.detect_options()}


# ---- Lucebox ランタイム（DFlash 投機デコード / R9700） ----


class LuceboxInstanceBody(BaseModel):
    """Luceboxモデル設定。値域はランタイム側の検証と二重に持たない。"""

    model_config = ConfigDict(extra="forbid")

    alias: str | None = Field(default=None, min_length=1, max_length=128,
                              pattern=r"^[A-Za-z0-9._:-]+$")
    model_path: str | None = None
    draft_path: str | None = None
    port: int | None = Field(default=None, ge=1024, le=65535)
    max_ctx: int | None = Field(default=None, ge=512, le=1_048_576)
    draft_block_size: int | None = Field(default=None, ge=2, le=32)
    cache_type_k: Literal["f16", "bf16", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0", "tq3_0"] | None = None
    cache_type_v: Literal["f16", "bf16", "q4_0", "q4_1", "q5_0", "q5_1", "q8_0", "tq3_0"] | None = None
    fa_window: int | None = Field(default=None, ge=0, le=131_072)
    ddtree: bool | None = None
    ddtree_budget: int | None = Field(default=None, ge=1, le=256)
    default_max_tokens: int | None = Field(default=None, ge=0, le=1_048_576)
    draft_residency: Literal["auto", "persistent", "request-scoped"] | None = None
    fast_rollback: bool | None = None
    prefer_speculative: bool | None = None
    agent_turn_cache: bool | None = None
    auto_start: bool | None = None
    idle_exclude: bool | None = None
    order: int | None = Field(default=None, ge=1, le=64)


def _lucebox_instance_patch(body: LuceboxInstanceBody) -> dict:
    """GGUFパスを許可ルート内へ正規化する（llama.cpp と同じ境界を使う）。"""
    from app.files import service as files

    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    for key in ("model_path", "draft_path"):
        if key not in patch:
            continue
        raw = str(patch[key])
        if key == "draft_path" and raw == "":  # 空文字はドラフト解除（AR動作）
            continue
        try:
            resolved = files.resolve(raw)
        except (PermissionError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not resolved.is_file() or resolved.suffix.lower() != ".gguf":
            raise HTTPException(status_code=422, detail="許可ルート内のGGUFファイルを指定してください")
        patch[key] = str(resolved)
    return patch


@router.get("/lucebox/status")
async def lucebox_status(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import lucebox

    state = lucebox.runtime_status()
    if state["installed"] and state["instances"]:
        health = await asyncio.gather(*(
            lucebox.health(str(item["alias"])) for item in state["instances"]
        ))
        state["instances"] = [{**item, "health": h}
                              for item, h in zip(state["instances"], health, strict=True)]
    return state


@router.get("/lucebox/instances")
async def lucebox_instances(user: User = Depends(require_permission("workflows.run"))):
    from app.models_mgmt import lucebox

    instances = lucebox.list_instances()
    health = await asyncio.gather(*(lucebox.health(str(item["alias"])) for item in instances))
    return [{**item, "health": state} for item, state in zip(instances, health, strict=True)]


@router.post("/lucebox/instances", status_code=201)
def lucebox_create_instance(
    body: LuceboxInstanceBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """Lucebox用のモデル設定を登録する。llama.cppのGGUF登録とは別経路にする。"""
    from app.models_mgmt import local_llm, lucebox

    patch = _lucebox_instance_patch(body)
    alias = str(patch.get("alias") or "")
    if not alias or not patch.get("model_path"):
        raise HTTPException(status_code=422, detail="別名とターゲットGGUFは必須です")
    conflict = local_llm.alias_taken_by_other_runtime(alias, "lucebox")
    if conflict:
        raise HTTPException(status_code=422, detail=f"別名 '{alias}' は {conflict} が使用しています")
    try:
        result = lucebox.save_instance(alias, patch)
    except lucebox.LuceboxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "lucebox.instance_create", user=user, resource_type="model", resource_id=alias,
                 request=request, metadata={"port": result.get("port")})
    return result


@router.put("/lucebox/instances/{alias}")
def lucebox_update_instance(
    alias: str, body: LuceboxInstanceBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import lucebox

    try:
        lucebox.get_instance(alias)
        result = lucebox.save_instance(alias, _lucebox_instance_patch(body))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except lucebox.LuceboxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "lucebox.instance_save", user=user, resource_type="model", resource_id=alias,
                 request=request)
    return result


@router.post("/lucebox/instances/{alias}/select")
def lucebox_select_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import lucebox

    try:
        result = lucebox.select_instance(alias)
    except lucebox.LuceboxError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, "lucebox.instance_select", user=user, resource_type="model", resource_id=alias,
                 request=request)
    return result


@router.post("/lucebox/instances/{alias}/delete")
def lucebox_delete_instance(
    alias: str, request: Request,
    body: DeleteInstanceBody | None = None,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import lucebox

    delete_file = bool(body.delete_file) if body else False
    try:
        result = lucebox.delete_instance(alias, delete_file=delete_file)
    except lucebox.LuceboxError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, "lucebox.instance_delete", user=user, resource_type="model", resource_id=alias,
                 request=request, metadata={"gguf_deleted": result["gguf_deleted"],
                                            "requested_file_delete": delete_file})
    return {"ok": True, **result}


@router.post("/lucebox/instances/reorder")
def lucebox_reorder_instances(
    body: ReorderBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import lucebox

    try:
        result = lucebox.reorder_instances(body.order)
    except lucebox.LuceboxError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, "lucebox.instance_reorder", user=user, resource_type="model",
                 request=request, metadata={"order": body.order})
    return result


@router.post("/lucebox/instances/{alias}/start")
async def lucebox_start_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import lucebox

    ok, error = await asyncio.to_thread(lucebox.start_instance, alias)
    audit.record(db, "lucebox.start", user=user, resource_type="model", resource_id=alias,
                 request=request)
    if not ok:
        raise HTTPException(status_code=502, detail=error or "起動に失敗しました")
    return {"ok": True}


@router.post("/lucebox/instances/{alias}/stop")
async def lucebox_stop_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import lucebox

    ok, error = await asyncio.to_thread(lucebox.stop_instance, alias)
    audit.record(db, "lucebox.stop", user=user, resource_type="model", resource_id=alias,
                 request=request)
    return {"ok": ok, "error": error}


class LuceboxSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    track: Literal["rocm10", "rocm7"]


@router.post("/lucebox/switch")
async def lucebox_switch(
    body: LuceboxSwitchBody, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    """導入済みの別版/別トラックへ切り替える（再ダウンロード不要・ロールバック用）。"""
    from app.models_mgmt import lucebox

    try:
        result = await asyncio.to_thread(lucebox.switch_version, body.tag, body.track)
    except lucebox.LuceboxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(db, "lucebox.switch", user=user, resource_type="runtime",
                 resource_id=f"{body.tag}/{body.track}", request=request)
    return result
