from __future__ import annotations

import time
from copy import deepcopy

import pytest

from app.resources.broker import ResourceBroker
from app.resources.devices import fake_devices
from tests.conftest import CSRF_HEADERS
from tests.test_addon_contract import addon_manifest


@pytest.fixture()
def runtime_api(admin_client, monkeypatch, tmp_path):
    from app.addon_runtime import resources as runtime_resources
    from app.addons import health, registry, router as addon_router, tokens
    from app.addons.schema import AddonHealthReport
    from app.database import SessionLocal
    from app.models import User

    data = tmp_path / "runtime-api-data"
    monkeypatch.setattr(registry, "data_dir", lambda: data)
    monkeypatch.setattr(tokens, "data_dir", lambda: data)
    registry.reset_runtime_state_for_tests()
    health.reset_for_tests()
    broker = ResourceBroker(fake_devices(100))
    monkeypatch.setattr(runtime_resources, "broker", broker)

    async def healthy(addon_id: str, client=None):
        return registry.update_health(addon_id, AddonHealthReport.model_validate({
            "status": "healthy", "contract_version": "2.0",
        }))

    async def no_grace():
        return None

    monkeypatch.setattr(health, "recheck", healthy)
    monkeypatch.setattr(addon_router, "_wait_for_disable_grace", no_grace)
    monkeypatch.setattr(addon_router, "resource_broker", broker)
    manifest = deepcopy(addon_manifest())
    assert admin_client.post("/api/v1/addons", json=manifest, headers=CSRF_HEADERS).status_code == 201
    assert admin_client.post(
        "/api/v1/addons/fake-addon/enable",
        json={"granted_capabilities": ["jobs.write", "resources.acquire"]},
        headers=CSRF_HEADERS,
    ).status_code == 200
    with SessionLocal() as db:
        user_id = db.query(User).filter(User.username == "admin").one().id
    return admin_client, registry, tokens, broker, user_id


def headers(token: str, addon_id: str = "fake-addon") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Control-Deck-Addon-ID": addon_id}


def resource_body(job_id: str, *, priority: int = 0, workload_class: str = "interactive") -> dict:
    return {
        "job_id": job_id,
        "device": "auto",
        "vram": {
            "resident_bytes": 80,
            "execution_peak_bytes": 80,
            "cold_load_peak_bytes": 80,
            "headroom_bytes": 0,
            "confidence": "measured",
        },
        "compute_mode": "exclusive-preferred",
        "priority": priority,
        "class": workload_class,
        "estimated_runtime_sec": 12,
    }


def create_host_job(client, tokens, user_id: int):
    token = tokens.issue("fake-addon", subject=str(user_id), kind="service")
    response = client.post(
        "/api/v1/addon-runtime/fake-addon/jobs",
        json={"title": "画像生成"},
        headers=headers(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["job"], token


def test_user_subject_creates_job_and_job_subject_attaches_without_duplicate(runtime_api):
    client, _registry, tokens, _broker, user_id = runtime_api
    job, _user_token = create_host_job(client, tokens, user_id)
    attached = client.post(
        "/api/v1/addon-runtime/fake-addon/jobs",
        json={"title": "ignored on attach"},
        headers=headers(tokens.issue("fake-addon", subject=f"job:{job['id']}", kind="service")),
    )
    assert attached.status_code == 201
    assert attached.json()["created"] is False
    assert attached.json()["job"]["id"] == job["id"]


def test_job_subject_can_create_detached_durable_job(runtime_api):
    client, _registry, tokens, _broker, user_id = runtime_api
    parent, _user_token = create_host_job(client, tokens, user_id)
    from app.jobs import service as jobs

    jobs.get(parent["id"]).kind = "addon.agent_tool.fake-addon.generate"
    detached = client.post(
        "/api/v1/addon-runtime/fake-addon/jobs",
        json={"title": "durable child", "detached": True},
        headers=headers(
            tokens.issue("fake-addon", subject=f"job:{parent['id']}", kind="service")
        ),
    )
    assert detached.status_code == 201, detached.text
    body = detached.json()
    assert body["created"] is True
    assert body["job"]["id"] != parent["id"]
    assert body["job"]["kind"] == "addon.runtime.fake-addon"


@pytest.mark.parametrize("subject", ["workflow:42", "context:7"])
def test_delegated_actor_subject_creates_and_operates_scoped_job(runtime_api, subject):
    client, _registry, tokens, _broker, user_id = runtime_api
    token = tokens.issue(
        "fake-addon",
        subject=subject,
        kind="service",
        actor_user_id=user_id,
    )
    created = client.post(
        "/api/v1/addon-runtime/fake-addon/jobs",
        json={"title": "delegated execution"},
        headers=headers(token),
    )
    assert created.status_code == 201, created.text
    job = created.json()["job"]
    assert job["owner_user_id"] == user_id
    resource = client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json=resource_body(job["id"]),
        headers=headers(token),
    )
    assert resource.status_code == 202, resource.text
    assert resource.json()["job_id"] == job["id"]

    other_actor = tokens.issue(
        "fake-addon",
        subject=subject,
        kind="service",
        actor_user_id=user_id + 999,
    )
    denied = client.get(
        f"/api/v1/addon-runtime/fake-addon/jobs/{job['id']}/control",
        headers=headers(other_actor),
    )
    assert denied.status_code == 403


def test_resource_api_forces_owner_binds_host_job_and_enforces_priority_ceiling(runtime_api):
    client, _registry, tokens, broker, user_id = runtime_api
    job, user_token = create_host_job(client, tokens, user_id)
    job_token = tokens.issue("fake-addon", subject=f"job:{job['id']}", kind="service")
    created = client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json=resource_body(job["id"], priority=30),
        headers=headers(job_token),
    )
    assert created.status_code == 202, created.text
    assert created.json()["owner"] == "addon:fake-addon"
    assert created.json()["job_id"] == job["id"]
    assert broker.leases.current()[0].owner == "addon:fake-addon"

    forged = {**resource_body(job["id"]), "owner": "llm:qwen"}
    assert client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests", json=forged, headers=headers(user_token),
    ).status_code == 422
    assert client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json=resource_body(job["id"], priority=31),
        headers=headers(user_token),
    ).status_code == 422
    wrong_job = client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json=resource_body("other-job"),
        headers=headers(job_token),
    )
    assert wrong_job.status_code in {403, 404}


def test_active_lease_refresh_rotates_same_scoped_short_lived_credential(runtime_api):
    client, _registry, tokens, _broker, user_id = runtime_api
    subject = "workflow:42"
    token = tokens.issue(
        "fake-addon",
        subject=subject,
        kind="service",
        actor_user_id=user_id,
        grant_ids=["grant:input"],
    )
    created = client.post(
        "/api/v1/addon-runtime/fake-addon/jobs",
        json={"title": "long add-on job"},
        headers=headers(token),
    )
    assert created.status_code == 201, created.text
    job = created.json()["job"]
    resource = client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json=resource_body(job["id"]),
        headers=headers(token),
    ).json()

    refreshed = client.post(
        f"/api/v1/addon-runtime/fake-addon/resources/leases/{resource['lease_id']}/credential/refresh",
        headers=headers(token),
    )

    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"] != token
    claims = tokens.verify(body["access_token"], addon_id="fake-addon", kind="service")
    assert claims["sub"] == subject
    assert claims["actor_user_id"] == user_id
    assert claims["grant_ids"] == ["grant:input"]
    assert claims["exp"] == body["expires_at"]
    audit_entries = [
        item for item in client.get("/api/v1/audit").json()
        if item["action"] == "addon.runtime.resource.credential.refresh"
    ]
    assert len(audit_entries) == 1
    assert audit_entries[0]["metadata"] == {"job_id": job["id"]}
    serialized_audit = str(audit_entries[0])
    assert token not in serialized_audit
    assert body["access_token"] not in serialized_audit
    assert "grant:input" not in serialized_audit


def test_lease_refresh_rejects_wrong_scope_disabled_addon_and_terminal_job(runtime_api):
    client, _registry, tokens, _broker, user_id = runtime_api
    job, token = create_host_job(client, tokens, user_id)
    resource = client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json=resource_body(job["id"]),
        headers=headers(token),
    ).json()
    url = f"/api/v1/addon-runtime/fake-addon/resources/leases/{resource['lease_id']}/credential/refresh"
    wrong_subject = tokens.issue("fake-addon", subject=str(user_id + 999), kind="service")
    assert client.post(url, headers=headers(wrong_subject)).status_code == 403

    terminal = client.patch(
        f"/api/v1/addon-runtime/fake-addon/jobs/{job['id']}",
        json={"phase": "complete", "status": "succeeded"},
        headers=headers(token),
    )
    assert terminal.status_code == 200, terminal.text
    assert client.post(url, headers=headers(token)).status_code == 409


def test_lease_refresh_fails_closed_after_addon_disable(runtime_api):
    client, _registry, tokens, _broker, user_id = runtime_api
    job, token = create_host_job(client, tokens, user_id)
    resource = client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json=resource_body(job["id"]),
        headers=headers(token),
    ).json()
    disabled = client.post("/api/v1/addons/fake-addon/disable", headers=CSRF_HEADERS)
    assert disabled.status_code == 200
    refreshed = client.post(
        f"/api/v1/addon-runtime/fake-addon/resources/leases/{resource['lease_id']}/credential/refresh",
        headers=headers(token),
    )
    assert refreshed.status_code == 409


def test_job_updates_require_phase_are_monotonic_rate_limited_and_terminal(runtime_api):
    client, _registry, tokens, _broker, user_id = runtime_api
    job, _user_token = create_host_job(client, tokens, user_id)
    token = tokens.issue("fake-addon", subject=f"job:{job['id']}", kind="service")
    url = f"/api/v1/addon-runtime/fake-addon/jobs/{job['id']}"
    assert client.patch(url, json={"progress": {"completed": 1, "total": 10}}, headers=headers(token)).status_code == 422
    oversized = client.patch(url, json={
        "phase": "failed", "status": "failed", "error": "large", "result": {"detail": "x" * (17 * 1024)},
    }, headers=headers(token))
    assert oversized.status_code == 413
    first = client.patch(url, json={
        "phase": "generating", "progress": {"completed": 2, "total": 10}, "message": "生成中",
    }, headers=headers(token))
    assert first.status_code == 200, first.text
    assert client.patch(url, json={
        "phase": "generating", "progress": {"completed": 3, "total": 10},
    }, headers=headers(token)).status_code == 429
    time.sleep(0.51)
    backwards = client.patch(url, json={
        "phase": "generating", "progress": {"completed": 1, "total": 10},
    }, headers=headers(token))
    assert backwards.status_code == 422
    terminal = client.patch(url, json={
        "phase": "package", "progress": {"completed": 10, "total": 10},
        "status": "succeeded", "result": {"asset_id": "asset:result"},
    }, headers=headers(token))
    assert terminal.status_code == 200, terminal.text
    assert terminal.json()["status"] == "succeeded"


def test_disable_cancels_waiting_and_job_but_keeps_active_lease_until_release(runtime_api):
    client, registry, tokens, broker, user_id = runtime_api
    job, _user_token = create_host_job(client, tokens, user_id)
    token = tokens.issue("fake-addon", subject=f"job:{job['id']}", kind="service")
    first = client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json=resource_body(job["id"]), headers=headers(token),
    ).json()
    second = client.post(
        "/api/v1/addon-runtime/fake-addon/resources/requests",
        json={**resource_body(job["id"]), "vram": {**resource_body(job["id"])["vram"], "resident_bytes": 20, "execution_peak_bytes": 20, "cold_load_peak_bytes": 20}},
        headers=headers(token),
    ).json()
    assert first["state"] == "granted" and second["state"] == "waiting"

    disabled = client.post("/api/v1/addons/fake-addon/disable", headers=CSRF_HEADERS)
    assert disabled.status_code == 200
    assert registry.status("fake-addon")["enabled"] is False
    assert (client.get(
        f"/api/v1/addon-runtime/fake-addon/resources/requests/{second['request_id']}", headers=headers(token),
    ).json()["state"] == "canceled")
    lease = broker.leases.get(first["lease_id"])
    assert lease is not None and lease.state.value == "granted"
    assert client.post(
        f"/api/v1/addon-runtime/fake-addon/resources/leases/{first['lease_id']}/renew", headers=headers(token),
    ).status_code == 409
    released = client.post(
        f"/api/v1/addon-runtime/fake-addon/resources/leases/{first['lease_id']}/release", headers=headers(token),
    )
    assert released.status_code == 200, released.text
    control = client.get(
        f"/api/v1/addon-runtime/fake-addon/jobs/{job['id']}/control", headers=headers(token),
    )
    assert control.json()["cancel_requested"] is True


def test_external_jobs_have_per_addon_user_active_limit(monkeypatch):
    from app.jobs import service as jobs

    monkeypatch.setattr(jobs, "_db_write", lambda *args, **kwargs: None)
    created = []
    try:
        for number in range(jobs.MAX_EXTERNAL_ACTIVE_PER_OWNER):
            created.append(jobs.create_external("quota-addon", f"job {number}", owner_user_id=987654))
        with pytest.raises(RuntimeError, match="上限"):
            jobs.create_external("quota-addon", "one too many", owner_user_id=987654)
    finally:
        for job in created:
            jobs._jobs.pop(job.id, None)
