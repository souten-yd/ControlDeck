from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.resources.broker import BrokerError, broker as resource_broker
from app.resources.leases import LeaseError
from app.resources.schema import ResourceRequest
from app.security.deps import require_permission

router = APIRouter(prefix="/resources", tags=["resources"])


def _not_found(exc: BrokerError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _lease_conflict(exc: LeaseError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("")
async def resource_snapshot(user: User = Depends(require_permission("system.view"))):
    return await resource_broker.snapshot()


# ── いま GPU に載っているもの ───────────────────────────────────────────
#
# ホームで見えるのは CPU/RAM/GPU/VRAM の総量だけで、その VRAM を何が使って
# いるのかは分からなかった。LLM だけ別扱いにすると、add-on が載せた画像・
# 動画・音声のモデルが数字の中に混ざったまま見えない。
#
# 出所は 2 つある。ControlDeck 自身が動かす LLM runtime と、resource lease を
# 取って GPU を確保している利用者である。後者は lease だけで表現できるので、
# ここに個別の add-on の語彙は要らない。名前は持ち主が自分で名乗る。

def _resident_since(value: float) -> float:
    return max(0.0, time.time() - value)


async def _runtime_residents() -> list[dict]:
    """LLM runtime が載せているもの。ControlDeck 自身の持ち物。"""
    from app.models_mgmt import llama, ollama

    items: list[dict] = []
    try:
        for instance in llama.list_instances():
            if not instance.get("loaded"):
                continue
            items.append({
                "id": f"llama:{instance['alias']}",
                "label": str(instance["alias"]),
                "source": "runtime",
                "owner": "llama.cpp",
                "role": str(instance.get("role", "llm")),
                "bytes": 0,
                "since_sec": None,
                "state": "active",
            })
    except Exception:
        # 一方が読めなくても、もう一方は出す。全部黙るより悪いことはない。
        pass
    try:
        for model in await ollama.running_models():
            name = str(model.get("name") or model.get("model") or "")
            if not name:
                continue
            items.append({
                "id": f"ollama:{name}",
                "label": name,
                "source": "runtime",
                "owner": "ollama",
                "role": "llm",
                "bytes": int(model.get("size_vram") or model.get("size") or 0),
                "since_sec": None,
                "state": "active",
            })
    except Exception:
        pass
    return items


@router.get("/residents")
async def resident_workloads(user: User = Depends(require_permission("system.view"))):
    """One list of everything currently holding GPU memory, however it got there."""
    snapshot = await resource_broker.snapshot()
    items = await _runtime_residents()
    for lease in snapshot.get("leases", []):
        if lease.get("state") not in {"granted", "active"}:
            continue
        owner = str(lease.get("owner") or "")
        items.append({
            "id": str(lease.get("lease_id") or ""),
            # owner は "addon:media-forge" の形。表示名は持ち主のものを使う。
            "label": owner.split(":", 1)[-1] or owner,
            "source": "addon" if owner.startswith("addon:") else "lease",
            "owner": owner,
            "role": None,
            "bytes": int(lease.get("reserved_bytes") or 0),
            "since_sec": _resident_since(float(lease.get("granted_at") or 0.0)),
            "state": str(lease.get("state")),
            "job_id": str(lease.get("job_id") or ""),
            "device_id": str(lease.get("device_id") or ""),
        })
    return {
        "devices": snapshot.get("devices", []),
        "items": items,
    }


@router.get("/requests/{request_id}")
async def resource_request(request_id: str, user: User = Depends(require_permission("system.view"))):
    try:
        return await resource_broker.request_status(request_id)
    except BrokerError as exc:
        raise _not_found(exc) from exc


@router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
async def submit_resource_request(
    body: ResourceRequest,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    result = await resource_broker.submit(body)
    audit.record(
        db,
        "resource.request",
        user=user,
        resource_type="resource_request",
        resource_id=result.request_id,
        request=request,
        metadata={
            "state": result.state.value,
            "reason": result.reason.value if result.reason else None,
            "device_id": result.device_id,
            "required_bytes": body.vram.required_bytes,
        },
    )
    return result


@router.delete("/requests/{request_id}")
async def cancel_resource_request(
    request_id: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    try:
        result = await resource_broker.cancel_request(request_id)
    except BrokerError as exc:
        raise _not_found(exc) from exc
    audit.record(db, "resource.request.cancel", user=user, resource_type="resource_request", resource_id=request_id, request=request)
    return result


async def _lease_action(lease_id: str, action: str):
    try:
        if action == "activate":
            return await resource_broker.activate(lease_id)
        if action == "renew":
            return await resource_broker.renew(lease_id)
        return await resource_broker.release(lease_id)
    except LeaseError as exc:
        raise _lease_conflict(exc) from exc


@router.post("/leases/{lease_id}/{action}")
async def resource_lease_action(
    lease_id: str,
    action: str,
    request: Request,
    user: User = Depends(require_permission("settings.manage")),
    db=Depends(get_db),
):
    if action not in {"activate", "renew", "release"}:
        raise HTTPException(status_code=404, detail="未対応のlease操作です")
    result = await _lease_action(lease_id, action)
    audit.record(
        db,
        f"resource.lease.{action}",
        user=user,
        resource_type="resource_lease",
        resource_id=lease_id,
        request=request,
        metadata={"device_id": result.device_id, "state": result.state.value},
    )
    return result

