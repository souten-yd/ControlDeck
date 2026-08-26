from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.conftest import CSRF_HEADERS
from tests.test_addon_contract import addon_manifest


@pytest.fixture()
def runtime_files(admin_client, monkeypatch, tmp_path):
    from app.addon_runtime import grants
    from app.addons import health, registry, tokens
    from app.addons.schema import AddonHealthReport
    from app.config import get_config
    from app.database import SessionLocal
    from app.models import User

    data = tmp_path / "runtime-files-data"
    monkeypatch.setattr(registry, "data_dir", lambda: data)
    monkeypatch.setattr(tokens, "data_dir", lambda: data)
    monkeypatch.setattr(grants, "data_dir", lambda: data)
    registry.reset_runtime_state_for_tests()
    health.reset_for_tests()

    async def healthy(addon_id: str, client=None):
        return registry.update_health(addon_id, AddonHealthReport.model_validate({
            "status": "healthy", "contract_version": "2.0",
        }))

    monkeypatch.setattr(health, "recheck", healthy)
    manifest = deepcopy(addon_manifest())
    manifest["host_capabilities"].extend(["files.pick", "files.export"])
    assert admin_client.post("/api/v1/addons", json=manifest, headers=CSRF_HEADERS).status_code == 201
    assert admin_client.post(
        "/api/v1/addons/fake-addon/enable",
        json={"granted_capabilities": ["files.pick", "files.export", "jobs.write"]},
        headers=CSRF_HEADERS,
    ).status_code == 200
    with SessionLocal() as db:
        user_id = db.query(User).filter(User.username == "admin").one().id
    allowed_root = Path(get_config().files.allowed_roots[0])
    return admin_client, tokens, user_id, allowed_root


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Control-Deck-Addon-ID": "fake-addon"}


def create_job(client, tokens, user_id: int):
    user_token = tokens.issue("fake-addon", subject=str(user_id), kind="service")
    response = client.post(
        "/api/v1/addon-runtime/fake-addon/jobs",
        json={"title": "Media output"}, headers=headers(user_token),
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["job"]["id"]
    return job_id, tokens.issue("fake-addon", subject=f"job:{job_id}", kind="service")


def test_read_grant_metadata_and_content_never_expose_host_path(runtime_files):
    client, tokens, user_id, root = runtime_files
    source = root / "runtime-grant-source.png"
    source.write_bytes(b"fake-png-bytes")
    grant = client.post(
        "/api/v1/addons/fake-addon/file-grants",
        json={"path": str(source), "kind": "read"}, headers=CSRF_HEADERS,
    )
    assert grant.status_code == 201, grant.text
    assert str(root) not in grant.text and "path" not in grant.json()
    grant_id = grant.json()["grant_id"]
    token = tokens.issue("fake-addon", subject=str(user_id), kind="service")
    metadata = client.get(
        f"/api/v1/addon-runtime/fake-addon/grants/{grant_id}", headers=headers(token),
    )
    assert metadata.status_code == 200
    assert metadata.json()["name"] == source.name and str(root) not in metadata.text
    content = client.get(
        f"/api/v1/addon-runtime/fake-addon/grants/{grant_id}/content", headers=headers(token),
    )
    assert content.status_code == 200 and content.content == b"fake-png-bytes"
    assert str(root) not in json.dumps(dict(content.headers))


def test_context_actor_can_read_only_delegated_grant(runtime_files):
    client, tokens, user_id, root = runtime_files
    first = root / "runtime-context-first.txt"
    second = root / "runtime-context-second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    first_grant = client.post(
        "/api/v1/addons/fake-addon/file-grants",
        json={"path": str(first), "kind": "read"},
        headers=CSRF_HEADERS,
    ).json()["grant_id"]
    second_grant = client.post(
        "/api/v1/addons/fake-addon/file-grants",
        json={"path": str(second), "kind": "read"},
        headers=CSRF_HEADERS,
    ).json()["grant_id"]
    token = tokens.issue(
        "fake-addon",
        subject=f"context:{user_id}",
        kind="service",
        actor_user_id=user_id,
        grant_ids=[first_grant],
    )
    allowed = client.get(
        f"/api/v1/addon-runtime/fake-addon/grants/{first_grant}/content",
        headers=headers(token),
    )
    denied = client.get(
        f"/api/v1/addon-runtime/fake-addon/grants/{second_grant}/content",
        headers=headers(token),
    )
    assert allowed.status_code == 200 and allowed.content == b"first"
    assert denied.status_code == 404
    deny_all_token = tokens.issue(
        "fake-addon",
        subject=f"context:{user_id}",
        kind="service",
        actor_user_id=user_id,
        grant_ids=[],
    )
    assert client.get(
        f"/api/v1/addon-runtime/fake-addon/grants/{first_grant}/content",
        headers=headers(deny_all_token),
    ).status_code == 404


def test_read_grant_rejects_inode_swap_and_symlink_escape(runtime_files):
    client, tokens, user_id, root = runtime_files
    source = root / "runtime-grant-swap.txt"
    source.write_text("safe", encoding="utf-8")
    grant_id = client.post(
        "/api/v1/addons/fake-addon/file-grants",
        json={"path": str(source), "kind": "read"}, headers=CSRF_HEADERS,
    ).json()["grant_id"]
    source.unlink()
    source.symlink_to("/etc/passwd")
    token = tokens.issue("fake-addon", subject=str(user_id), kind="service")
    response = client.get(
        f"/api/v1/addon-runtime/fake-addon/grants/{grant_id}/content", headers=headers(token),
    )
    assert response.status_code == 404
    source.unlink()


def test_output_staging_commit_is_job_and_export_grant_scoped(runtime_files):
    client, tokens, user_id, root = runtime_files
    destination = root / "runtime-output-destination"
    destination.mkdir(exist_ok=True)
    grant = client.post(
        "/api/v1/addons/fake-addon/file-grants",
        json={"path": str(destination), "kind": "export"}, headers=CSRF_HEADERS,
    ).json()
    job_id, job_token = create_job(client, tokens, user_id)
    payload = b"generated image"
    created = client.post(
        "/api/v1/addon-runtime/fake-addon/files/outputs",
        json={
            "job_id": job_id,
            "grant_id": grant["grant_id"],
            "filename": "result.png",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "content_type": "image/png",
        },
        headers=headers(job_token),
    )
    assert created.status_code == 201, created.text
    output_id = created.json()["output_id"]
    uploaded = client.put(
        f"/api/v1/addon-runtime/fake-addon/files/outputs/{output_id}/content",
        content=payload, headers=headers(job_token),
    )
    assert uploaded.status_code == 200, uploaded.text
    committed = client.post(
        f"/api/v1/addon-runtime/fake-addon/files/outputs/{output_id}/commit",
        headers=headers(job_token),
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["asset_id"].startswith("asset:")
    assert str(root) not in committed.text and "path" not in committed.json()
    assert (destination / "result.png").read_bytes() == payload


def test_output_commit_does_not_cross_device_replace_the_central_staging_file(
    runtime_files, monkeypatch
):
    client, tokens, user_id, root = runtime_files
    destination = root / "runtime-output-another-device"
    destination.mkdir(exist_ok=True)
    grant = client.post(
        "/api/v1/addons/fake-addon/file-grants",
        json={"path": str(destination), "kind": "export"}, headers=CSRF_HEADERS,
    ).json()
    job_id, job_token = create_job(client, tokens, user_id)
    payload = b"portable atomic output"
    created = client.post(
        "/api/v1/addon-runtime/fake-addon/files/outputs",
        json={
            "job_id": job_id, "grant_id": grant["grant_id"], "filename": "portable.bin",
            "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
        },
        headers=headers(job_token),
    ).json()
    client.put(
        f"/api/v1/addon-runtime/fake-addon/files/outputs/{created['output_id']}/content",
        content=payload, headers=headers(job_token),
    )

    import app.addon_runtime.grants as grants

    original_replace = grants.os.replace

    def reject_cross_device_shape(source, destination_name, *args, **kwargs):
        if str(source).endswith(".part") and kwargs.get("dst_dir_fd") is not None:
            raise OSError(18, "Invalid cross-device link")
        return original_replace(source, destination_name, *args, **kwargs)

    monkeypatch.setattr(grants.os, "replace", reject_cross_device_shape)
    committed = client.post(
        f"/api/v1/addon-runtime/fake-addon/files/outputs/{created['output_id']}/commit",
        headers=headers(job_token),
    )

    assert committed.status_code == 200, committed.text
    assert (destination / "portable.bin").read_bytes() == payload
    assert list(destination.glob(".control-deck-output-*.part")) == []


def test_output_rejects_path_filename_and_size_mismatch(runtime_files):
    client, tokens, user_id, root = runtime_files
    destination = root / "runtime-output-rejected"
    destination.mkdir(exist_ok=True)
    grant_id = client.post(
        "/api/v1/addons/fake-addon/file-grants",
        json={"path": str(destination), "kind": "export"}, headers=CSRF_HEADERS,
    ).json()["grant_id"]
    job_id, job_token = create_job(client, tokens, user_id)
    invalid = client.post(
        "/api/v1/addon-runtime/fake-addon/files/outputs",
        json={"job_id": job_id, "grant_id": grant_id, "filename": "../escape.png", "size": 1},
        headers=headers(job_token),
    )
    assert invalid.status_code == 422
    created = client.post(
        "/api/v1/addon-runtime/fake-addon/files/outputs",
        json={"job_id": job_id, "grant_id": grant_id, "filename": "short.png", "size": 3},
        headers=headers(job_token),
    ).json()
    mismatch = client.put(
        f"/api/v1/addon-runtime/fake-addon/files/outputs/{created['output_id']}/content",
        content=b"xx", headers=headers(job_token),
    )
    assert mismatch.status_code == 409
    assert not (destination / "short.png").exists()
