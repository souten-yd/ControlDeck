from __future__ import annotations

from app.addon_runtime.auth import RuntimePrincipal
from app.addon_runtime.gateway import GATEWAY_PROTOCOL_VERSION, _gateway_document


def principal(*capabilities: str) -> RuntimePrincipal:
    return RuntimePrincipal(
        addon_id="media-forge",
        subject="user:1",
        actor_user_id=1,
        grant_ids=None,
        expires_at=9999999999,
        granted_capabilities=frozenset(capabilities),
        active=True,
    )


def test_gateway_document_projects_only_granted_host_capabilities():
    value = _gateway_document(
        principal("jobs.write", "resources.acquire", "files.export", "ai.inference"),
        text_generate=True,
        vision_analyze=False,
    )

    assert value["protocol_version"] == GATEWAY_PROTOCOL_VERSION
    assert value["addon_id"] == "media-forge"
    assert value["control_plane"]["jobs"] == {
        "read": False,
        "write": True,
        "durable": True,
        "cancel_control": True,
    }
    assert value["control_plane"]["resources"]["leases"] is True
    assert value["control_plane"]["files"]["pick"] is False
    assert value["control_plane"]["files"]["export"] is True
    assert value["control_plane"]["ai"]["capabilities"] == {
        "text.generate": True,
        "vision.analyze": False,
    }
    assert value["transports"]["embedded_websocket_proxy"]["available"] is True
    assert value["transports"]["device_session"]["available"] is False


def test_gateway_document_does_not_advertise_host_ai_without_grant():
    value = _gateway_document(
        principal("jobs.read"),
        text_generate=True,
        vision_analyze=True,
    )

    assert value["control_plane"]["ai"]["inference"] is False
    assert value["control_plane"]["ai"]["release"] is False
    assert value["control_plane"]["ai"]["capabilities"] == {
        "text.generate": False,
        "vision.analyze": False,
    }
