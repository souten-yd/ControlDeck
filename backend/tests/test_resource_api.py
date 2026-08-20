from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models import AuditLog
from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from tests.conftest import CSRF_HEADERS


def body(owner: str, job_id: str, required: int = 60) -> dict:
    return {
        "owner": owner,
        "job_id": job_id,
        "device": "auto",
        "vram": {
            "resident_bytes": required,
            "execution_peak_bytes": required,
            "cold_load_peak_bytes": required,
            "headroom_bytes": 0,
            "confidence": "measured",
        },
        "compute_mode": "exclusive-required",
        "priority": 20,
        "class": "interactive",
        "max_wait_sec": 30,
        "on_insufficient": "queue",
    }


@pytest.fixture()
def resource_api(admin_client, monkeypatch):
    from app.resources import router

    value = ResourceBroker(fake_devices(100, 200))
    monkeypatch.setattr(router, "resource_broker", value)
    return admin_client, value


def test_resource_api_requires_auth_rbac_and_csrf(client, resource_api):
    admin, _broker = resource_api
    admin.cookies.clear()
    assert admin.get("/api/v1/resources").status_code == 401
    assert admin.post("/api/v1/resources/requests", json=body("addon:a", "a")).status_code == 403


def test_resource_api_submit_wait_cancel_release_and_audit_without_requirement_payload(resource_api):
    client, _broker = resource_api
    first = client.post("/api/v1/resources/requests", json=body("addon:a", "a", 100), headers=CSRF_HEADERS)
    assert first.status_code == 202
    assert first.json()["state"] == "granted"
    second = client.post("/api/v1/resources/requests", json=body("addon:b", "b", 100), headers=CSRF_HEADERS)
    assert second.status_code == 202
    # gpu1 admits the second request independently.
    assert second.json()["state"] == "granted"
    third = client.post("/api/v1/resources/requests", json={**body("addon:c", "c", 1), "device": "gpu0"}, headers=CSRF_HEADERS)
    assert third.json()["state"] == "waiting"

    canceled = client.delete(f"/api/v1/resources/requests/{third.json()['request_id']}", headers=CSRF_HEADERS)
    assert canceled.status_code == 200 and canceled.json()["state"] == "canceled"
    released = client.post(f"/api/v1/resources/leases/{first.json()['lease_id']}/release", headers=CSRF_HEADERS)
    assert released.status_code == 200 and released.json()["state"] == "released"
    snapshot = client.get("/api/v1/resources").json()
    assert [item["id"] for item in snapshot["devices"]] == ["gpu0", "gpu1"]
    assert snapshot["telemetry"]["counters"]["lease.granted"] == 2

    from app.database import SessionLocal
    with SessionLocal() as db:
        rows = db.execute(select(AuditLog).where(AuditLog.action.like("resource.%"))).scalars().all()
    assert rows
    assert all("execution_peak_bytes" not in row.metadata_json for row in rows)
    assert any(json.loads(row.metadata_json).get("required_bytes") == 100 for row in rows)


def test_resource_api_unknown_and_finished_lease_fail_closed(resource_api):
    client, _broker = resource_api
    assert client.get("/api/v1/resources/requests/missing").status_code == 404
    first = client.post("/api/v1/resources/requests", json=body("addon:a", "a"), headers=CSRF_HEADERS).json()
    path = f"/api/v1/resources/leases/{first['lease_id']}/release"
    assert client.post(path, headers=CSRF_HEADERS).status_code == 200
    assert client.post(path, headers=CSRF_HEADERS).status_code == 409
