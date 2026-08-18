"""OpenAI互換ゲートウェイ。

OpenCode のように ControlDeck を経由せず llama.cpp を直接叩くクライアントにも、
KVの受け入れ制御（models_mgmt/llama.py の endpoint_capacity / await_capacity）を
効かせるための薄いリバースプロキシ。

共有KV(kv_unified)は総量が尽きると待たずに 500 を返し、実行中の他リクエストごと
失敗させる。slot不足は llama.cpp が queue に逃がすが、KV不足は逃がしてくれない。
そのため「投げる前に空くまで待つ」層をクライアントとllama.cppの間に挟む。

認証はセッションCookieではなく専用APIキー（Authorization: Bearer）。
OpenAI互換クライアントはCookieを持てないため。
"""
from __future__ import annotations

import json
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import data_dir
from app.security.crypto import decrypt_text, encrypt_text

router = APIRouter(prefix="/llm", tags=["llm-gateway"])

# 待受は既存Webサービス内。新しいポートもプロセスも増やさない。
# OpenCode 側は base_url に http://127.0.0.1:8765/api/v1/llm/v1 を設定する。
_SETTINGS = "llm-gateway.json"
KEY_PREFIX = "cdk-"


def _path():
    return data_dir() / _SETTINGS


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)  # APIキーを含むので他ユーザーから読ませない
    except OSError:
        pass


def get_api_key(*, create: bool = False) -> str:
    """ゲートウェイ用APIキー。保存は既存の暗号化ユーティリティを使う。"""
    data = _load()
    token = str(data.get("api_key") or "")
    if token:
        try:
            return decrypt_text(token)
        except Exception:  # noqa: BLE001 - 壊れていたら作り直す
            pass
    if not create:
        return ""
    plain = KEY_PREFIX + secrets.token_urlsafe(32)
    data["api_key"] = encrypt_text(plain)
    _save(data)
    return plain


def rotate_api_key() -> str:
    data = _load()
    data.pop("api_key", None)
    _save(data)
    return get_api_key(create=True)


def _authorize(request: Request) -> None:
    expected = get_api_key()
    if not expected:
        raise HTTPException(status_code=503, detail="ゲートウェイのAPIキーが未発行です")
    header = request.headers.get("authorization") or ""
    provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="APIキーが正しくありません")


def _target_endpoint(model: str) -> tuple[str, int]:
    """モデル名(alias)から転送先を決める。未指定なら既定モデルのエンドポイント。"""
    from app.models_mgmt import llama

    instances = llama.list_instances()
    match = next((i for i in instances if str(i.get("alias")) == model), None)
    if match is None:
        # 一覧は優先度順。既定は最優先のLLM。
        match = next((i for i in instances if str(i.get("role", "llm")) == "llm"), None)
    if match is None:
        raise HTTPException(status_code=404, detail="転送先のモデルが登録されていません")
    port = int(match.get("port") or 0)
    if not port:
        raise HTTPException(status_code=503, detail="転送先ポートが解決できません")
    return str(match["alias"]), port


# クライアント側のtimeoutより長く待たないよう、既定は控えめにする。
CAPACITY_TIMEOUT_SECONDS = 300


async def _admit(port: int, payload: dict[str, Any]) -> dict:
    """KVに空きが出るまで待つ。空くまで待って必ず通す方針。"""
    from app.models_mgmt import llama

    messages = payload.get("messages") or []
    prompt_chars = sum(len(str(m.get("content") or "")) for m in messages if isinstance(m, dict))
    # 見積りは概算。厳密さより「混雑時に待つ」ことが目的。
    needed = prompt_chars // 4 + int(payload.get("max_tokens") or 0)
    return await llama.await_capacity(port, needed, timeout_seconds=CAPACITY_TIMEOUT_SECONDS)


@router.get("/v1/models")
async def gateway_models(request: Request):
    """OpenAI互換のモデル一覧。登録済みLLMを優先度順で返す。"""
    _authorize(request)
    from app.models_mgmt import llama

    return {"object": "list", "data": [
        {"id": str(i["alias"]), "object": "model", "owned_by": "control-deck"}
        for i in llama.list_instances() if str(i.get("role", "llm")) == "llm"
    ]}


@router.post("/v1/chat/completions")
async def gateway_chat(request: Request):
    """受け入れ制御を挟んで llama.cpp へ転送する。stream もそのまま中継する。"""
    _authorize(request)
    from app.models_mgmt import llama

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSONボディが不正です") from None
    alias, port = _target_endpoint(str(payload.get("model") or ""))
    # 停止中ならオンデマンド起動（Chat経路と同じ扱い）
    if not await llama.ensure_ready(alias, timeout_seconds=240):
        raise HTTPException(status_code=503, detail="モデルの起動に失敗しました")
    await _admit(port, payload)
    payload["model"] = alias
    target = f"http://127.0.0.1:{port}/v1/chat/completions"

    if payload.get("stream"):
        async def relay():
            # ストリームはクライアントへそのまま流す。ここで加工しない。
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", target, json=payload) as upstream:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk

        return StreamingResponse(relay(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=None) as client:
        upstream = await client.post(target, json=payload)
    return JSONResponse(status_code=upstream.status_code, content=upstream.json())
