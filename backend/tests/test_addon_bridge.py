from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.addons.schema import AddonHealthReport
from app.database import SessionLocal
from app.models import AuditLog
from tests.conftest import CSRF_HEADERS
from tests.test_addon_contract import addon_manifest


@pytest.fixture()
def bridge_addon(admin_client, monkeypatch, tmp_path):
    from app.addons import bridge, health, registry, tokens

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "addon-bridge-data")
    monkeypatch.setattr(tokens, "data_dir", lambda: tmp_path / "addon-token-data")
    registry.reset_runtime_state_for_tests()
    bridge.reset_for_tests()
    health.reset_for_tests()

    async def healthy(addon_id: str, client=None):
        return registry.update_health(addon_id, AddonHealthReport.model_validate({
            "status": "healthy", "contract_version": "2.0",
        }))

    monkeypatch.setattr(health, "recheck", healthy)
    manifest = addon_manifest()
    manifest["host_capabilities"].append("notifications.show")
    assert admin_client.post("/api/v1/addons", json=manifest, headers=CSRF_HEADERS).status_code == 201
    assert admin_client.post("/api/v1/addons/fake-addon/enable", headers=CSRF_HEADERS).status_code == 200
    return admin_client, registry


def handshake(client):
    response = client.post("/api/v1/addons/fake-addon/bridge/handshake", headers=CSRF_HEADERS, json={
        "bridge_version": "1.0", "view_id": "workspace",
    })
    assert response.status_code == 200, response.text
    return response.json()


def call(client, session, method, params=None):
    return client.post("/api/v1/addons/fake-addon/bridge/call", headers=CSRF_HEADERS, json={
        "bridge_version": "1.0",
        "session_nonce": session["session_nonce"],
        "view_id": "workspace",
        "method": method,
        "params": params or {},
    })


def test_bridge_handshake_binds_nonce_and_authorizes_granted_method(bridge_addon):
    client, _registry = bridge_addon
    session = handshake(client)
    assert session["bridge_version"] == "1.0"
    assert session["expires_in"] == 600
    assert "host.theme.get" in session["allowed_methods"]
    response = call(client, session, "host.theme.get")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "method": "host.theme.get"}


def test_bridge_rejects_unknown_method_bad_nonce_and_external_route_with_codes(bridge_addon):
    client, _registry = bridge_addon
    session = handshake(client)
    unknown = call(client, session, "host.shell.exec")
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "method_not_supported"
    bad_route = call(client, session, "host.route.open", {"route": "https://example.com"})
    assert bad_route.status_code == 422
    assert bad_route.json()["detail"]["code"] == "invalid_params"
    session["session_nonce"] += "tampered"
    invalid = call(client, session, "host.theme.get")
    assert invalid.status_code == 403
    assert invalid.json()["detail"]["code"] == "invalid_session"


def test_bridge_checks_capability_permission_and_disabled_state(bridge_addon, monkeypatch):
    client, registry = bridge_addon
    from app.addons import bridge

    session = handshake(client)
    current = registry.status("fake-addon")
    current["granted_capabilities"] = [item for item in current["granted_capabilities"] if item != "theme.read"]
    with monkeypatch.context() as scoped:
        scoped.setattr(bridge, "_view", lambda *_args: (current, {"id": "workspace"}))
        denied = call(client, session, "host.theme.get")
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "capability_not_granted"
    permission = call(client, session, "host.permission.has", {"permission": "settings.manage"})
    assert permission.status_code == 200 and permission.json()["has_permission"] is True
    assert client.post("/api/v1/addons/fake-addon/disable", headers=CSRF_HEADERS).status_code == 200
    disabled = call(client, session, "host.permission.has", {"permission": "settings.manage"})
    assert disabled.status_code == 409
    assert disabled.json()["detail"]["code"] == "addon_disabled"


def test_bridge_rate_limits_and_audits_without_message_body(bridge_addon, monkeypatch):
    client, registry = bridge_addon
    from app.addons import bridge

    monkeypatch.setattr(bridge, "BRIDGE_CALLS_PER_MINUTE", 1)
    session = handshake(client)
    params = {"title": "Done", "message": "secret notification body", "dedupe_key": "job-1"}
    assert call(client, session, "host.notification.show", params).status_code == 200
    limited = call(client, session, "host.notification.show", params)
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "rate_limited"
    activity = registry.activity("fake-addon")
    assert activity[0]["result"] == "rate_limited"
    with SessionLocal() as db:
        rows = db.execute(select(AuditLog).where(AuditLog.action == "addon.bridge").order_by(AuditLog.id.desc()).limit(2)).scalars().all()
    assert rows and all("secret notification body" not in row.metadata_json for row in rows)
    metadata = json.loads(rows[0].metadata_json)
    assert metadata["method"] == "host.notification.show"


def test_bridge_requires_csrf_and_available_view(bridge_addon):
    client, _registry = bridge_addon
    assert client.post("/api/v1/addons/fake-addon/bridge/handshake", json={
        "bridge_version": "1.0", "view_id": "workspace",
    }).status_code == 403
    missing = client.post("/api/v1/addons/fake-addon/bridge/handshake", headers=CSRF_HEADERS, json={
        "bridge_version": "1.0", "view_id": "missing",
    })
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "view_unavailable"
