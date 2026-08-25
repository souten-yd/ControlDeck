from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.addons import registry
from app.models_mgmt.ai_gateway import capability_available


router = APIRouter(prefix="/{addon_id}/gateway", tags=["addon-runtime-gateway"])
GatewayAuth = Annotated[RuntimePrincipal, Depends(require_runtime_capability())]
GATEWAY_PROTOCOL_VERSION = "1.1"


def _gateway_document(
    principal: RuntimePrincipal,
    *,
    text_generate: bool,
    vision_analyze: bool,
    device_relays: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    granted = principal.granted_capabilities
    ai_granted = "ai.inference" in granted
    relay_granted = "devices.relay" in granted
    relays = device_relays or []
    device_available = bool(relay_granted and relays)
    return {
        "protocol_version": GATEWAY_PROTOCOL_VERSION,
        "addon_id": principal.addon_id,
        "control_plane": {
            "jobs": {
                "read": "jobs.read" in granted,
                "write": "jobs.write" in granted,
                "durable": True,
                "cancel_control": "jobs.write" in granted,
            },
            "resources": {
                "acquire": "resources.acquire" in granted,
                "queue": "resources.acquire" in granted,
                "leases": "resources.acquire" in granted,
                "credential_refresh": "resources.acquire" in granted,
            },
            "files": {
                "pick": "files.pick" in granted,
                "export": "files.export" in granted,
                "scoped_grants": bool({"files.pick", "files.export"} & granted),
                "output_commit": "files.export" in granted,
            },
            "ai": {
                "inference": ai_granted,
                "release": ai_granted,
                "capabilities": {
                    "text.generate": bool(ai_granted and text_generate),
                    "vision.analyze": bool(ai_granted and vision_analyze),
                },
            },
            "devices": {
                "relay": relay_granted,
                "pairing": device_available,
                "relay_ids": [
                    item.get("id") for item in relays if isinstance(item.get("id"), str)
                ],
            },
        },
        "transports": {
            "runtime_http": {"available": True, "version": "1"},
            "embedded_http_proxy": {"available": True, "version": "1"},
            "embedded_websocket_proxy": {"available": True, "version": "1"},
            "device_session": {
                "available": device_available,
                "version": "1" if device_available else None,
                "pairing": "one_time_code" if device_available else None,
                "credential_ttl_seconds": 28800 if device_available else None,
                "reason": None if device_available else (
                    "devices_relay_not_granted" if not relay_granted else "no_device_relays_declared"
                ),
            },
        },
        "ownership": {
            "host": [
                "authorization",
                "jobs",
                "resource_admission",
                "host_ai_routing",
                "scoped_file_grants",
                "agent_workflow_projection",
                "transport_relay",
                "device_pairing_and_relay",
            ],
            "addon": [
                "domain_models",
                "domain_routing",
                "worker_runtimes",
                "asset_semantics",
                "domain_provenance",
                "device_protocol_semantics",
            ],
        },
    }


@router.get("/capabilities")
async def gateway_capabilities(principal: GatewayAuth):
    text_generate = False
    vision_analyze = False
    if "ai.inference" in principal.granted_capabilities:
        text_generate = await capability_available("text.generate")
        vision_analyze = await capability_available("vision.analyze")
    try:
        current = registry.status(principal.addon_id)
        relays = (current.get("contributions") or {}).get("device_relays") or []
    except registry.AddonRegistryError:
        relays = []
    return _gateway_document(
        principal,
        text_generate=text_generate,
        vision_analyze=vision_analyze,
        device_relays=relays,
    )
