from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import workflow_artifacts_dir
from app.database import SessionLocal
from app.models import WorkflowArtifact, WorkflowExecutionEvent
from app.workflows import artifacts
from tests.conftest import CSRF_HEADERS


def _wait_for_completion(client, execution_id: int) -> dict:
    deadline = time.monotonic() + 10
    body: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/workflow-executions/{execution_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] not in {"QUEUED", "RUNNING"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"workflow did not finish: {body}")


def test_large_output_is_offloaded_downloaded_and_deleted(admin_client):
    large_value = "artifact-payload-" + ("あ" * 140_000)
    definition = {
        "nodes": [
            {"id": "t", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "out", "type": "signal.display", "config": {
                "signal": "large", "value": large_value,
            }},
        ],
        "edges": [{"source": "t", "target": "out"}],
    }
    created = admin_client.post(
        "/api/v1/workflows",
        json={"name": "artifact offload", "definition": definition},
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    assert admin_client.post(
        f"/api/v1/workflows/{workflow_id}/publish", headers=CSRF_HEADERS,
    ).status_code == 200
    started = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/run", json={}, headers=CSRF_HEADERS,
    )
    assert started.status_code == 200, started.text
    execution_id = started.json()["execution_id"]

    finished = _wait_for_completion(admin_client, execution_id)
    assert finished["status"] == "SUCCEEDED", finished
    reference = finished["outputs"]["large"]["value"]
    assert reference["offloaded"] is True
    assert reference["size_bytes"] > artifacts.OFFLOAD_THRESHOLD
    assert "storage_key" not in reference

    listed = admin_client.get(
        f"/api/v1/workflows/{workflow_id}/executions/{execution_id}/artifacts",
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    metadata = listed.json()[0]
    assert metadata["artifact_id"] == reference["artifact_id"]
    assert metadata["downloadable"] is True
    assert "storage_key" not in metadata

    downloaded = admin_client.get(
        f"/api/v1/workflow-artifacts/{reference['artifact_id']}/download",
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert hashlib.sha256(downloaded.content).hexdigest() == reference["checksum"]
    assert downloaded.json() == large_value

    with SessionLocal() as db:
        row = db.get(WorkflowArtifact, reference["artifact_id"])
        assert row is not None
        path = artifacts.artifact_path(row)
        assert path.stat().st_mode & 0o777 == 0o600
        event_types = db.execute(select(WorkflowExecutionEvent.event_type).where(
            WorkflowExecutionEvent.execution_id == execution_id,
        )).scalars().all()
    assert "artifact.created" in event_types

    deleted = admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS)
    assert deleted.status_code == 200, deleted.text
    assert not path.exists()


def test_artifact_path_rejects_escape_symlink_and_checksum_mismatch(tmp_path: Path):
    root = workflow_artifacts_dir().resolve()
    execution_dir = root / "path-validation"
    execution_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    link = execution_dir / "link.json"
    link.symlink_to(outside)

    symlink_artifact = WorkflowArtifact(
        execution_id=1, node_id="test", storage_key=link.relative_to(root).as_posix(),
        filename="link.json", size_bytes=len(b"outside"), checksum=hashlib.sha256(b"outside").hexdigest(),
    )
    with pytest.raises(artifacts.WorkflowArtifactError, match="symlink"):
        artifacts.artifact_path(symlink_artifact)

    escaped = WorkflowArtifact(
        execution_id=1, node_id="test", storage_key="../outside.json",
        filename="outside.json", size_bytes=0, checksum="",
    )
    with pytest.raises(artifacts.WorkflowArtifactError, match="不正"):
        artifacts.artifact_path(escaped)

    regular = execution_dir / "regular.json"
    regular.write_bytes(b"changed")
    os.chmod(regular, 0o600)
    mismatch = WorkflowArtifact(
        execution_id=1, node_id="test", storage_key=regular.relative_to(root).as_posix(),
        filename="regular.json", size_bytes=len(b"changed"), checksum="0" * 64,
    )
    with pytest.raises(artifacts.WorkflowArtifactError, match="checksum"):
        artifacts.artifact_path(mismatch)

    link.unlink()
    regular.unlink()
    execution_dir.rmdir()


def test_output_hard_limit_is_enforced():
    with pytest.raises(artifacts.WorkflowArtifactError, match="上限"):
        artifacts.ensure_output_size({"value": "x" * (artifacts.MAX_ARTIFACT_BYTES + 1)})
