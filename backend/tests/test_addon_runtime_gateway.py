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

    assert value["protocol_version"] == GATEWAY_PROTOCOL_VERSION == "1.1"
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
    assert value["control_plane"]["devices"] == {
        "relay": False,
        "pairing": False,
        "relay_ids": [],
    }
    assert value["transports"]["embedded_websocket_proxy"]["available"] is True
    assert value["transports"]["device_session"] == {
        "available": False,
        "version": None,
        "pairing": None,
        "credential_ttl_seconds": None,
        "reason": "devices_relay_not_granted",
    }


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


def test_device_session_requires_both_grant_and_declared_relay():
    relay = {
        "id": "voice",
        "endpoint": "/addon/v1/live/ws",
        "protocol": "sonic-edge/1",
    }
    granted_only = _gateway_document(
        principal("devices.relay"),
        text_generate=False,
        vision_analyze=False,
        device_relays=[],
    )
    assert granted_only["control_plane"]["devices"] == {
        "relay": True,
        "pairing": False,
        "relay_ids": [],
    }
    assert granted_only["transports"]["device_session"]["available"] is False
    assert granted_only["transports"]["device_session"]["reason"] == "no_device_relays_declared"

    declared_without_grant = _gateway_document(
        principal(),
        text_generate=False,
        vision_analyze=False,
        device_relays=[relay],
    )
    assert declared_without_grant["transports"]["device_session"]["available"] is False
    assert declared_without_grant["transports"]["device_session"]["reason"] == "devices_relay_not_granted"

    ready = _gateway_document(
        principal("devices.relay"),
        text_generate=False,
        vision_analyze=False,
        device_relays=[relay],
    )
    assert ready["control_plane"]["devices"] == {
        "relay": True,
        "pairing": True,
        "relay_ids": ["voice"],
    }
    assert ready["transports"]["device_session"] == {
        "available": True,
        "version": "1",
        "pairing": "one_time_code",
        "credential_ttl_seconds": 28800,
        "reason": None,
    }
