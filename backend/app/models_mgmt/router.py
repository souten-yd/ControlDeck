"""Model（Ollama）管理 API。"""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
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
                                            "gpu_profile": policy.amd_gpu.profile if policy.amd_gpu.enabled else "disabled"})
    return await runtime_policy.environment()


class ProviderLoadBody(BaseModel):
    keep_alive: str | int | None = None


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
    port: int | None = Field(default=None, ge=1024, le=65535)
    n_gpu_layers: int | None = Field(default=None, ge=0, le=999)
    ctx_size: int | None = Field(default=None, ge=0, le=1048576)
    deep_research_ctx_size: int | None = Field(default=None, ge=0, le=1048576)
    n_parallel: int | None = Field(default=None, ge=1, le=64)
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


def _llama_instance_patch(body: LlamaInstanceBody) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "model_path" in patch:
        from app.files import service as files

        try:
            model_path = files.resolve(str(patch["model_path"]))
        except (PermissionError, FileNotFoundError) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        if not model_path.is_file() or model_path.suffix.lower() != ".gguf":
            raise HTTPException(status_code=422, detail="llama.cppモデルは許可ルート内のGGUFファイルを指定してください")
        patch["model_path"] = str(model_path)
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


@router.post("/llama/instances/{alias}/delete")
def llama_delete_instance(
    alias: str, request: Request,
    user: User = Depends(require_permission("workflows.edit")), db=Depends(get_db),
):
    from app.models_mgmt import llama

    try:
        llama.delete_instance(alias)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit.record(db, "llama.instance_delete", user=user, resource_type="model", resource_id=alias,
                 request=request, metadata={"gguf_deleted": False})
    return {"ok": True, "gguf_deleted": False}


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


@router.get("/llama/options")
async def llama_options(user: User = Depends(require_permission("workflows.edit"))):
    """稼働バイナリの --help から利用可能な引数を返す（実在オプションのみ UI 表示するため）。"""
    from app.models_mgmt import llama

    return {"flags": await llama.detect_options()}
