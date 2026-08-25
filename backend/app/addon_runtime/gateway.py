from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.addon_runtime.auth import RuntimePrincipal, require_runtime_capability
from app.models_mgmt.ai_gateway import capability_available


router = APIRouter(prefix="/{addon_id}/gateway", tags=["addon-runtime-gateway"])
GatewayAuth = Annotated[RuntimePrincipal, Depends(require_runtime_capability())]
GATEWAY_PROTOCOL_VERSION = "1.0"


def _gateway_document(
    principal: RuntimePrincipal,
    *,
    text_generate: bool,
    vision_analyze: bool,
) -> dict[str, Any]:
    granted = principal.granted_capabilities
    ai_granted = "ai.inference" in granted
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
        },
        "transports": {
            "runtime_http": {"available": True, "version": "1"},
            "embedded_http_proxy": {"available": True, "version": "1"},
            "embedded_websocket_proxy": {"available": True, "version": "1"},
            "device_session": {
                "available": False,
                "version": None,
                "reason": "generic_device_session_not_implemented",
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
            ],
            "addon": [
                "domain_models",
                "domain_routing",
                "worker_runtimes",
                "asset_semantics",
                "domain_provenance",
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
    return _gateway_document(
        principal,
        text_generate=text_generate,
        vision_analyze=vision_analyze,
    )
