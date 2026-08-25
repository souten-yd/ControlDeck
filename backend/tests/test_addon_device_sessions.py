from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from app.addons.schema import AddonHealthReport, parse_manifest
from app.database import SessionLocal
from app.models import User
from tests.conftest import CSRF_HEADERS
from tests.test_addon_contract import addon_manifest


def device_manifest() -> dict:
    value = addon_manifest()
    value["host_capabilities"] = [
        *value["host_capabilities"],
        "devices.relay",
    ]
    value["contributions"]["device_relays"] = [
        {
            "id": "voice",
            "label": "Voice device",
            "endpoint": "/addon/v1/live/ws",
            "protocol": "sonic-edge/1",
            "transport": "websocket",
            "permission": "workflows.run",
        }
    ]
    return value


@pytest.fixture()
def enabled_device_addon(admin_client, monkeypatch, tmp_path):
    from app.addons import health, registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "device-addon-data")
    registry.reset_runtime_state_for_tests()
    health.reset_for_tests()

    async def healthy(addon_id: str, client=None):
        return registry.update_health(
            addon_id,
            AddonHealthReport.model_validate(
                {"status": "healthy", "contract_version": "2.0"}
            ),
        )

    monkeypatch.setattr(health, "recheck", healthy)
    response = admin_client.post(
        "/api/v1/addons",
        json=device_manifest(),
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 201, response.text
    enabled = admin_client.post(
        "/api/v1/addons/fake-addon/enable",
        headers=CSRF_HEADERS,
    )
    assert enabled.status_code == 200, enabled.text
    registry.update_health(
        "fake-addon",
        AddonHealthReport.model_validate(
            {"status": "healthy", "contract_version": "2.0"}
        ),
    )
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        user_id = admin.id
    finally:
        db.close()
    return admin_client, registry, user_id


def service_headers(user_id: int) -> dict[str, str]:
    from app.addons import tokens

    token = tokens.issue(
        "fake-addon",
        subject=str(user_id),
        kind="service",
        actor_user_id=user_id,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Control-Deck-Addon-ID": "fake-addon",
    }


def test_manifest_accepts_scoped_device_relay_contract():
    parsed = parse_manifest(device_manifest()).manifest
    assert "devices.relay" in parsed.host_capabilities
    relay = parsed.contributions.device_relays[0]
    assert relay.id == "voice"
    assert relay.endpoint == "/addon/v1/live/ws"
    assert relay.protocol == "sonic-edge/1"


def test_pairing_requires_explicit_devices_relay_grant(enabled_device_addon):
    client, registry, user_id = enabled_device_addon
    registry.set_enabled(
        "fake-addon",
        True,
        grants=["theme.read", "jobs.write", "resources.acquire"],
    )
    response = client.post(
        "/api/v1/addon-runtime/fake-addon/devices/pairings",
        json={"relay_id": "voice", "device_label": "M5"},
        headers=service_headers(user_id),
    )
    assert response.status_code == 403


def test_pairing_is_one_time_and_device_token_reconnects(
    enabled_device_addon, monkeypatch
):
    client, _registry, user_id = enabled_device_addon
    from app.addons import proxy, tokens

    upstream_messages: list[str | bytes] = []
    upstream_headers: list[dict[str, str]] = []

    class FakeUpstream:
        subprotocol = None

        async def send(self, message):
            upstream_messages.append(message)

        async def __aiter__(self):
            yield "upstream-ready"
            await asyncio.Event().wait()

    class FakeConnection:
        async def __aenter__(self):
            return FakeUpstream()

        async def __aexit__(self, *_args):
            return None

    def connect(url, headers, subprotocols):
        assert url == "ws://127.0.0.1:9130/addon/v1/live/ws"
        assert subprotocols == []
        upstream_headers.append(dict(headers))
        return FakeConnection()

    monkeypatch.setattr(proxy, "_connect_websocket", connect)

    pairing = client.post(
        "/api/v1/addon-runtime/fake-addon/devices/pairings",
        json={"relay_id": "voice", "device_label": "M5 CoreS3"},
        headers=service_headers(user_id),
    )
    assert pairing.status_code == 201, pairing.text
    body = pairing.json()
    assert body["protocol"] == "sonic-edge/1"
    code = body["pairing_code"]

    with client.websocket_connect(
        body["websocket_path"],
        headers={"X-Control-Deck-Pairing-Code": code},
    ) as socket:
        session = socket.receive_json()
        assert session["type"] == "control-deck.device.session"
        assert session["newly_paired"] is True
        device_token = session["device_token"]
        payload = tokens.verify(
            device_token,
            addon_id="fake-addon",
            kind="device",
            max_ttl_seconds=8 * 60 * 60,
        )
        assert payload["actor_user_id"] == user_id
        assert payload["sub"].startswith("device:voice:")
        assert socket.receive_text() == "upstream-ready"
        socket.send_text("device-message")

    assert "device-message" in upstream_messages
    assert upstream_headers
    relayed_auth = upstream_headers[0]["Authorization"].removeprefix("Bearer ")
    relayed_payload = tokens.verify(
        relayed_auth,
        addon_id="fake-addon",
        kind="service",
    )
    assert relayed_payload["actor_user_id"] == user_id
    assert relayed_auth != device_token

    with pytest.raises(WebSocketDisconnect) as reused:
        with client.websocket_connect(
            body["websocket_path"],
            headers={"X-Control-Deck-Pairing-Code": code},
        ):
            pass
    assert reused.value.code == 4403

    with client.websocket_connect(
        body["websocket_path"],
        headers={"Authorization": f"Bearer {device_token}"},
    ) as socket:
        refreshed = socket.receive_json()
        assert refreshed["type"] == "control-deck.device.session"
        assert refreshed["newly_paired"] is False
        assert refreshed["device_id"] == session["device_id"]
        assert refreshed["device_token"] != device_token
        assert socket.receive_text() == "upstream-ready"
