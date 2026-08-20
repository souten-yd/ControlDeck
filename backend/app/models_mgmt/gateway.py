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

import asyncio
import json
import re
import secrets
import uuid
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
# main.py が付ける API prefix を含めた実パス。接続先の判定にも使う。
API_PATH = "/api/v1/llm/v1"
# クライアントにモデルを固定させないための仮想モデル。転送先はControlDeckが決める。
AUTO_MODEL = "auto"


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


def base_url() -> str:
    """ゲートウェイのOpenAI互換 base_url。全クライアントの共通接続先。"""
    from app.config import get_config

    return f"http://127.0.0.1:{int(get_config().server.port)}{API_PATH}"


def is_gateway_url(url: str) -> bool:
    return API_PATH in str(url or "")


def resolve_endpoint(model: str) -> tuple[str, int]:
    """モデル名(alias)から転送先を決める。

    AUTO_MODEL・未指定・未登録のときは起動中のLLMを優先し、いなければ登録順（=優先度順）
    の先頭を採る。停止中の別モデルを起こすと同じGPUへ二重にロードすることになり、稼働中の
    エンドポイントまでVRAM不足で巻き込むため、起動済みがあればそれを使う。
    """
    from app.models_mgmt import llama

    instances = llama.list_instances()
    if model == AUTO_MODEL:
        model = ""
    match = next((i for i in instances if str(i.get("alias")) == model), None)
    if match is None:
        llms = [i for i in instances if str(i.get("role", "llm")) == "llm"]
        match = next((i for i in llms if i.get("loaded")), None) or (llms[0] if llms else None)
    if match is None:
        raise HTTPException(status_code=404, detail="転送先のモデルが登録されていません")
    port = int(match.get("port") or 0)
    if not port:
        raise HTTPException(status_code=503, detail="転送先ポートが解決できません")
    return str(match["alias"]), port


def _target_endpoint(model: str) -> tuple[str, int]:
    """後方互換の別名。"""
    return resolve_endpoint(model)


def resolve_internal_target(base: str, model: str) -> tuple[str, str]:
    """ゲートウェイ宛の (base_url, model) を実エンドポイントへ解決する。

    ControlDeck 内部の生成はゲートウェイへHTTPで戻らず、同じ解決規則で実インスタンスを
    直接叩く。往復のホップを増やさずに、受け入れ制御・thinking・キャンセルといった
    provider側の既存処理をそのまま効かせるため。
    """
    if not is_gateway_url(base):
        return base, model
    alias, port = resolve_endpoint(model)
    return f"http://127.0.0.1:{port}/v1", alias


# クライアント側のtimeoutより長く待たないよう、既定は控えめにする。
CAPACITY_TIMEOUT_SECONDS = 300


async def _admit(port: int, payload: dict[str, Any]) -> dict:
    """KVに空きが出るまで待つ。空くまで待って必ず通す方針。"""
    from app.models_mgmt.resource_provider import provider

    messages = payload.get("messages") or []
    prompt_chars = sum(len(str(m.get("content") or "")) for m in messages if isinstance(m, dict))
    # 見積りは概算。厳密さより「混雑時に待つ」ことが目的。
    needed = prompt_chars // 4 + int(payload.get("max_tokens") or 0)
    return await provider().await_capacity(
        port, needed, timeout_seconds=CAPACITY_TIMEOUT_SECONDS
    )


async def _wait_for_client(task: asyncio.Task, request: Request):
    while not task.done():
        done, _ = await asyncio.wait((task,), timeout=0.25)
        if done:
            break
        if await request.is_disconnected():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise HTTPException(status_code=499, detail="クライアントが切断しました")
    return await task


async def _renew_gateway_lease(lease_id: str) -> None:
    from app.resources.broker import broker

    try:
        while True:
            await asyncio.sleep(10)
            await broker.renew(lease_id)
    except asyncio.CancelledError:
        raise


async def _acquire_gateway_lease(alias: str, request: Request):
    from app.models_mgmt.resource_provider import provider
    from app.resources.broker import broker
    from app.resources.schema import RequestState

    adapter = provider()
    await adapter.enter_request()
    status = None
    try:
        requirement = adapter.resource_request(alias, f"gateway-{uuid.uuid4().hex[:16]}")
        status = await broker.submit(requirement)
        if status.state == RequestState.WAITING:
            status = await _wait_for_client(
                asyncio.create_task(broker.wait(status.request_id)), request
            )
        if status.state != RequestState.GRANTED or not status.lease_id:
            raise HTTPException(
                status_code=503,
                detail=f"GPU resource admission failed: {status.state.value}",
                headers={"Retry-After": "5"},
            )
        await broker.activate(status.lease_id)
        renew = asyncio.create_task(_renew_gateway_lease(status.lease_id))
        return adapter, status.lease_id, renew
    except (Exception, asyncio.CancelledError):
        if status is not None:
            try:
                await broker.cancel_request(status.request_id)
            except Exception:  # noqa: BLE001 - original admission error wins
                pass
            if status.lease_id:
                try:
                    await broker.release(status.lease_id)
                except Exception:  # noqa: BLE001 - original admission error wins
                    pass
        await adapter.leave_request()
        raise


async def _release_gateway_lease(adapter, lease_id: str, renew: asyncio.Task) -> None:
    from app.resources.broker import broker
    from app.resources.leases import LeaseError

    renew.cancel()
    await asyncio.gather(renew, return_exceptions=True)
    try:
        await broker.release(lease_id)
    except LeaseError:
        pass
    await adapter.leave_request()


def _record_gateway_oom(alias: str, lease_id: str, response: httpx.Response) -> None:
    if response.status_code < 500:
        return
    try:
        detail = response.text.casefold()
    except Exception:  # noqa: BLE001 - malformed provider response is not an OOM signal
        return
    if not (
        any(token in detail for token in ("out of memory", "out_of_memory", "cannot allocate"))
        or re.search(r"\boom\b", detail) is not None
    ):
        return
    from app.models_mgmt import llama
    from app.resources.broker import broker

    lease = broker.leases.get(lease_id)
    device = broker.devices.get(lease.device_id) if lease else None
    if lease:
        broker.telemetry.record_oom(
            llama.residency_key(llama.get_instance(alias)),
            lease.device_id,
            observed_peak_bytes=device.observed_used_bytes if device else lease.reserved_bytes,
            requested_bytes=lease.reserved_bytes,
        )


@router.get("/v1/models")
async def gateway_models(request: Request):
    """OpenAI互換のモデル一覧。登録済みLLMを優先度順で返す。"""
    _authorize(request)
    from app.models_mgmt import llama

    llms = [i for i in llama.list_instances() if str(i.get("role", "llm")) == "llm"]
    # autoを先頭に置く。クライアントが特定モデルを名指ししない限り、起動中のモデルへ流れる。
    data = [{"id": AUTO_MODEL, "object": "model", "owned_by": "control-deck"}] if llms else []
    data += [{"id": str(i["alias"]), "object": "model", "owned_by": "control-deck"} for i in llms]
    return {"object": "list", "data": data}


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
    adapter, lease_id, renew = await _acquire_gateway_lease(alias, request)
    try:
        # 停止中ならlease確保後にオンデマンド起動する。
        ready = await _wait_for_client(
            asyncio.create_task(llama.ensure_ready(alias, timeout_seconds=180)), request
        )
        if not ready:
            raise HTTPException(
                status_code=503,
                detail="モデルの起動に失敗しました",
                headers={"Retry-After": "5"},
            )
        await _admit(port, payload)
    except (Exception, asyncio.CancelledError):
        await _release_gateway_lease(adapter, lease_id, renew)
        raise
    payload["model"] = alias
    target = f"http://127.0.0.1:{port}/v1/chat/completions"

    def record_first_token(started_at: float) -> None:
        from app.resources.broker import broker as resource_broker

        try:
            resource_broker.telemetry.record_first_token(
                llama.residency_key(llama.get_instance(alias)),
                asyncio.get_running_loop().time() - started_at,
            )
        except Exception:  # noqa: BLE001 - telemetry must not affect inference
            return

    if payload.get("stream"):
        async def relay():
            # ストリームはクライアントへそのまま流す。ここで加工しない。
            started_at = asyncio.get_running_loop().time()
            first = True
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("POST", target, json=payload) as upstream:
                        async for chunk in upstream.aiter_bytes():
                            if first and chunk:
                                record_first_token(started_at)
                                first = False
                            yield chunk
            finally:
                await _release_gateway_lease(adapter, lease_id, renew)

        return StreamingResponse(relay(), media_type="text/event-stream")

    try:
        started_at = asyncio.get_running_loop().time()
        async with httpx.AsyncClient(timeout=None) as client:
            upstream = await client.post(target, json=payload)
        _record_gateway_oom(alias, lease_id, upstream)
        record_first_token(started_at)
        return JSONResponse(status_code=upstream.status_code, content=upstream.json())
    finally:
        await _release_gateway_lease(adapter, lease_id, renew)
