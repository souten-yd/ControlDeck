from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.addon_runtime.service import audit_runtime
from app.models_mgmt import llama, resource_provider
from app.models_mgmt.ai_gateway import AITargetUnavailable, resolve_ai_target


router = APIRouter(prefix="/{addon_id}/ai/residency", tags=["addon-runtime-ai-residency"])
AIAuth = Annotated[RuntimePrincipal, Depends(require_runtime_capability("ai.inference"))]
HOLD_TTL_SECONDS = 120
HEARTBEAT_INTERVAL_SECONDS = 30


def _owner(principal: RuntimePrincipal) -> str:
    return f"addon:{principal.addon_id}:{principal.subject}"


async def _managed_text_target():
    try:
        target = await resolve_ai_target("text.generate")
    except AITargetUnavailable as exc:
        raise HTTPException(status_code=503, detail="text.generate target is unavailable") from exc
    return target


@router.post("/holds", status_code=201)
async def create_hold(request: Request, principal: AIAuth):
    """Keep the Host-selected LLM warm for a live conversational session.

    The hold is intentionally short and renewable. It is never persisted, so a
    SonicForge crash, lost network path, or ControlDeck restart cannot leave a
    permanent pin behind.
    """
    target = await _managed_text_target()
    if not target.gateway_managed:
        return {
            "held": False,
            "hold_id": None,
            "reason": "runtime_manages_residency",
            "heartbeat_interval_seconds": 0,
            "expires_at": None,
        }
    if not await llama.ensure_ready(target.model, timeout_seconds=180):
        raise HTTPException(status_code=503, detail="AI model failed to become ready")
    instance = llama.get_instance(target.model)
    residency_key = llama.residency_key(instance)
    provider = resource_provider.provider()
    hold_id = provider.create_residency_hold(
        residency_key,
        _owner(principal),
        ttl_seconds=HOLD_TTL_SECONDS,
    )
    llama.mark_used_by_base_url(target.base_url)
    expires_at = int(time.time()) + HOLD_TTL_SECONDS
    audit_runtime(
        request,
        principal,
        "addon.runtime.ai.residency.hold",
        "ai_gateway",
        "text.generate",
        {"held": True},
    )
    return {
        "held": True,
        "hold_id": hold_id,
        "reason": "session_hold",
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "expires_at": expires_at,
    }


@router.post("/holds/{hold_id}/renew")
async def renew_hold(hold_id: str, request: Request, principal: AIAuth):
    if not hold_id.startswith("hold:") or len(hold_id) > 80:
        raise HTTPException(status_code=404, detail="residency hold not found")
    target = await _managed_text_target()
    if not target.gateway_managed:
        raise HTTPException(status_code=409, detail="selected runtime does not use ControlDeck residency holds")
    provider = resource_provider.provider()
    if not provider.renew_residency_hold(
        hold_id,
        _owner(principal),
        ttl_seconds=HOLD_TTL_SECONDS,
    ):
        raise HTTPException(status_code=404, detail="residency hold expired or was not found")
    llama.mark_used_by_base_url(target.base_url)
    expires_at = int(time.time()) + HOLD_TTL_SECONDS
    return {
        "held": True,
        "hold_id": hold_id,
        "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
        "expires_at": expires_at,
    }


@router.delete("/holds/{hold_id}")
async def release_hold(hold_id: str, request: Request, principal: AIAuth):
    if not hold_id.startswith("hold:") or len(hold_id) > 80:
        raise HTTPException(status_code=404, detail="residency hold not found")
    released = resource_provider.provider().release_residency_hold(
        hold_id,
        _owner(principal),
    )
    audit_runtime(
        request,
        principal,
        "addon.runtime.ai.residency.release",
        "ai_gateway",
        "text.generate",
        {"released": released},
    )
    return {"released": released, "hold_id": hold_id}
