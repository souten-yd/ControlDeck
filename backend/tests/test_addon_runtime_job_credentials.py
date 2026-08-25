from __future__ import annotations

from tests.test_addon_runtime_resources_jobs import (
    create_host_job,
    headers,
    runtime_api,
)

# Re-export the imported fixture for pytest collection in this module.
__all__ = ["runtime_api"]


def test_active_host_job_refresh_rotates_same_scoped_short_lived_credential(runtime_api):
    client, _registry, tokens, _broker, user_id = runtime_api
    subject = "workflow:voice-session"
    token = tokens.issue(
        "fake-addon",
        subject=subject,
        kind="service",
        actor_user_id=user_id,
        grant_ids=["grant:input"],
    )
    created = client.post(
        "/api/v1/addon-runtime/fake-addon/jobs",
        json={"title": "long CPU-only meeting"},
        headers=headers(token),
    )
    assert created.status_code == 201, created.text
    job = created.json()["job"]

    refreshed = client.post(
        f"/api/v1/addon-runtime/fake-addon/jobs/{job['id']}/credential/refresh",
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
        item
        for item in client.get("/api/v1/audit").json()
        if item["action"] == "addon.runtime.job.credential.refresh"
    ]
    assert len(audit_entries) == 1
    assert audit_entries[0]["metadata"]["status"] in {"queued", "running"}
    serialized = str(audit_entries[0])
    assert token not in serialized
    assert body["access_token"] not in serialized
    assert "grant:input" not in serialized


def test_job_refresh_fails_closed_for_terminal_job(runtime_api):
    client, _registry, tokens, _broker, user_id = runtime_api
    job, token = create_host_job(client, tokens, user_id)
    terminal = client.patch(
        f"/api/v1/addon-runtime/fake-addon/jobs/{job['id']}",
        json={"phase": "complete", "status": "succeeded"},
        headers=headers(token),
    )
    assert terminal.status_code == 200, terminal.text

    refreshed = client.post(
        f"/api/v1/addon-runtime/fake-addon/jobs/{job['id']}/credential/refresh",
        headers=headers(token),
    )
    assert refreshed.status_code in {403, 409}
