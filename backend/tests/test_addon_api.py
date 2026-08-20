from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from tests.conftest import CSRF_HEADERS
from tests.test_addon_contract import addon_manifest


@pytest.fixture()
def addon_api(admin_client, monkeypatch, tmp_path):
    from app.addons import health, registry
    from app.addons.schema import AddonHealthReport

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "addon-api-data")
    registry.reset_runtime_state_for_tests()
    health.reset_for_tests()

    async def healthy(addon_id: str, client=None):
        return registry.update_health(addon_id, AddonHealthReport.model_validate({
            "status": "healthy", "contract_version": "2.0",
        }))

    monkeypatch.setattr(health, "recheck", healthy)
    return admin_client, registry


def test_addon_api_requires_auth_and_does_not_leak_from_public_meta(client, addon_api):
    admin_client, _registry = addon_api
    admin_client.cookies.clear()
    assert admin_client.get("/api/v1/addons/effective").status_code == 401
    meta = admin_client.get("/api/v1/meta")
    assert meta.status_code == 200
    assert "addons" not in meta.json()


def test_addon_api_install_enable_effective_etag_disable_and_audit(addon_api, monkeypatch):
    client, registry = addon_api
    from app.addons import router
    canceled_owners: list[str] = []

    async def cancel_owner(owner: str):
        canceled_owners.append(owner)
        return {"requests": 1, "leases": 1}

    monkeypatch.setattr(router.resource_broker, "cancel_owner", cancel_owner)
    value = deepcopy(addon_manifest())
    value["contributions"]["commands"][0]["hint"] = "future presentation"
    installed = client.post("/api/v1/addons", json=value, headers=CSRF_HEADERS)
    assert installed.status_code == 201, installed.text
    assert installed.json()["state"] == "installed_disabled"
    assert installed.json()["warnings"] == [
        "contributions.commands[0].hint: このhostでは未対応の表示fieldを無視しました"
    ]

    enabled = client.post("/api/v1/addons/fake-addon/enable", headers=CSRF_HEADERS)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "healthy"

    effective = client.get("/api/v1/addons/effective")
    assert effective.status_code == 200
    assert effective.json()["addons"][0]["id"] == "fake-addon"
    assert effective.headers["etag"] == effective.json()["etag"]
    cached = client.get("/api/v1/addons/effective", headers={"If-None-Match": effective.headers["etag"]})
    assert cached.status_code == 304 and cached.content == b""

    registry.record_activity("fake-addon", "host.context.get", "success", {"field_count": 2})
    activity = client.get("/api/v1/addons/fake-addon/activity")
    assert activity.status_code == 200
    assert activity.json()[0]["method"] == "host.context.get"
    assert activity.json()[0]["metadata"] == {"field_count": 2}

    async def observe_grace():
        assert router.DISABLE_GRACE_SECONDS == 2.0
        pending = registry.status("fake-addon")
        assert pending["state"] == "disable_pending"
        assert registry.effective_for_permissions({"apps.view"})["addons"][0]["state"] == "disable_pending"

    monkeypatch.setattr(router, "_wait_for_disable_grace", observe_grace)
    disabled = client.post("/api/v1/addons/fake-addon/disable", headers=CSRF_HEADERS)
    assert disabled.status_code == 200
    assert canceled_owners == ["addon:fake-addon"]
    assert client.get("/api/v1/addons/effective").json()["addons"] == []

    audits = client.get("/api/v1/audit").json()
    assert any(item["action"] == "addon.install" and item["resource_id"] == "fake-addon" for item in audits)
    assert any(item["action"] == "addon.enable" and item["resource_id"] == "fake-addon" for item in audits)
    assert any(item["action"] == "addon.disable" and item["resource_id"] == "fake-addon" for item in audits)


def test_addon_api_recheck_disabled_and_invalid_manifest_fail_closed(addon_api):
    client, _registry = addon_api
    invalid = addon_manifest()
    invalid["contributions"]["arbitrary_python"] = []
    assert client.post("/api/v1/addons", json=invalid, headers=CSRF_HEADERS).status_code == 422

    assert client.post("/api/v1/addons", json=addon_manifest(), headers=CSRF_HEADERS).status_code == 201
    recheck = client.post("/api/v1/addons/fake-addon/recheck", headers=CSRF_HEADERS)
    assert recheck.status_code == 409


def test_addon_api_rejects_csrf(addon_api):
    client, _registry = addon_api
    assert client.post("/api/v1/addons", json=addon_manifest()).status_code == 403


def test_effective_event_stream_emits_revision_and_user_specific_etag(addon_api):
    _client, registry = addon_api
    from app.addons.router import _effective_event_stream

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    async def first_event():
        stream = _effective_event_stream(ConnectedRequest(), {"apps.view"})
        try:
            return await anext(stream)
        finally:
            await stream.aclose()

    event = asyncio.run(first_event())
    assert event.startswith("event: addons.effective.changed\ndata: ")
    payload = json.loads(event.split("data: ", 1)[1])
    assert payload["revision"] == registry.revision()
    assert payload["etag"] == registry.effective_for_permissions({"apps.view"})["etag"]
