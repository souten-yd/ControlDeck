"""Bounded, application-owned Workflow artifact storage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import workflow_artifacts_dir
from app.models import WorkflowArtifact, WorkflowNodeRun

OFFLOAD_THRESHOLD = 256 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_ARTIFACTS_PER_NODE = 20
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class WorkflowArtifactError(ValueError):
    pass


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode()


def ensure_output_size(value: Any) -> None:
    size = len(_json_bytes(value))
    if size > MAX_ARTIFACT_BYTES:
        raise WorkflowArtifactError(f"ノード出力が上限（{MAX_ARTIFACT_BYTES // 1024 // 1024}MB）を超えました")


def _root() -> Path:
    root = workflow_artifacts_dir().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _safe_filename(node_id: str, field: str) -> str:
    base = _SAFE_NAME.sub("-", f"{node_id}-{field}").strip(".-")[:180] or "output"
    return f"{base}.json"


def _store_json(
    db: Session, *, execution_id: int, node_run_id: int | None, node_id: str,
    field: str, payload: bytes, sensitive: bool,
) -> tuple[WorkflowArtifact, Path]:
    root = _root()
    relative = Path(str(execution_id)) / f"{uuid.uuid4().hex}.json"
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise WorkflowArtifactError("artifact pathがstorage root外です")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    temporary = target.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except Exception:
        if temporary.exists() and temporary.is_file():
            temporary.unlink()
        raise
    artifact = WorkflowArtifact(
        execution_id=execution_id, node_run_id=node_run_id, node_id=node_id,
        storage_key=relative.as_posix(), filename=_safe_filename(node_id, field),
        mime_type="application/json", size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(), sensitive=sensitive,
    )
    db.add(artifact)
    db.flush()
    return artifact, target


def reference(artifact: WorkflowArtifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.id, "filename": artifact.filename,
        "mime_type": artifact.mime_type, "size_bytes": artifact.size_bytes,
        "checksum": artifact.checksum, "sensitive": artifact.sensitive,
    }


def compact_output(
    db: Session, *, execution_id: int, node_run_id: int | None, node_id: str,
    output: dict[str, Any], sensitive: bool = False,
) -> tuple[dict[str, Any], list[WorkflowArtifact], list[Path]]:
    """Keep the output contract, offloading only oversized immediate values."""
    compact = dict(output)
    artifacts: list[WorkflowArtifact] = []
    created_paths: list[Path] = []
    for field, value in list(compact.items()):
        payload = _json_bytes(value)
        if len(payload) <= OFFLOAD_THRESHOLD:
            continue
        if len(artifacts) >= MAX_ARTIFACTS_PER_NODE:
            raise WorkflowArtifactError("1ノードのartifact数が上限を超えました")
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise WorkflowArtifactError("artifactが32MB上限を超えました")
        try:
            artifact, path = _store_json(
                db, execution_id=execution_id, node_run_id=node_run_id, node_id=node_id,
                field=str(field), payload=payload, sensitive=sensitive,
            )
        except (OSError, RuntimeError, SQLAlchemyError) as exc:
            raise WorkflowArtifactError("artifactを安全に保存できませんでした") from exc
        compact[field] = {"offloaded": True, **reference(artifact)}
        artifacts.append(artifact)
        created_paths.append(path)
    return compact, artifacts, created_paths


def compact_execution_context(db: Session, execution_id: int, context: dict[str, Any]) -> dict[str, Any]:
    latest: dict[str, WorkflowNodeRun] = {}
    rows = db.execute(select(WorkflowNodeRun).where(
        WorkflowNodeRun.execution_id == execution_id,
    ).order_by(WorkflowNodeRun.id)).scalars().all()
    for row in rows:
        latest[row.node_id] = row
    compact = dict(context)
    for node_id, entry in list(compact.items()):
        if not isinstance(entry, dict) or "output" not in entry:
            continue
        if len(_json_bytes(entry.get("output"))) <= OFFLOAD_THRESHOLD:
            continue
        node_run = latest.get(str(node_id))
        replacement: Any = None
        if node_run is not None:
            try:
                replacement = json.loads(node_run.outputs_json or "{}")
            except json.JSONDecodeError:
                replacement = None
        updated = dict(entry)
        updated["output"] = replacement if isinstance(replacement, dict) else {
            "truncated": True, "message": "大容量出力の保存に失敗しました",
        }
        compact[node_id] = updated
    return compact


def artifact_path(artifact: WorkflowArtifact) -> Path:
    root = _root()
    relative = Path(artifact.storage_key)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkflowArtifactError("artifact pathが不正です")
    candidate = root / relative
    if candidate.is_symlink():
        raise WorkflowArtifactError("artifact pathがsymlinkです")
    path = candidate.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise WorkflowArtifactError("artifactが見つかりません")
    if path.stat().st_size != artifact.size_bytes:
        raise WorkflowArtifactError("artifact sizeがmetadataと一致しません")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != artifact.checksum:
        raise WorkflowArtifactError("artifact checksumが一致しません")
    return path


def remove_artifact_file(artifact: WorkflowArtifact) -> None:
    root = _root()
    relative = Path(artifact.storage_key)
    if relative.is_absolute() or ".." in relative.parts:
        return
    candidate = root / relative
    if candidate.is_symlink():
        candidate.unlink(missing_ok=True)
        return
    path = candidate.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return
    path.unlink(missing_ok=True)
    parent = path.parent
    root = _root()
    if parent != root:
        try:
            parent.rmdir()
        except OSError:
            pass
