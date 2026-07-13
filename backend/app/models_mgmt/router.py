"""Model（Ollama）管理 API。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field

from app.audit import service as audit
from app.database import SessionLocal, get_db
from app.models import User
from app.models_mgmt import ollama
from app.security.deps import authenticate_websocket, require_permission

router = APIRouter(prefix="/models", tags=["models"])


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


@router.put("/settings")
def put_settings(body: SettingsBody, user: User = Depends(require_permission("workflows.edit"))):
    return ollama.save_settings({k: v for k, v in body.model_dump().items() if v is not None})


@router.get("/hf-search")
async def hf_search(q: str, user: User = Depends(require_permission("workflows.edit"))):
    if not q.strip():
        return []
    try:
        return await ollama.hf_search(q.strip())
    except ollama.OllamaError as e:
        raise HTTPException(status_code=502, detail=str(e))


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
