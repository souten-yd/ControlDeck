"""Flow App API。Workflowを配布できる実行ファイルへ書き出す。

.pyz（配布先はpython3のみ）は1〜2秒で終わるため同期、単一バイナリ（配布先は
追加インストール不要）はPyInstallerで1〜2分かかるためサーバー側jobで進める。
成果物はdata_dir配下へ置き、metadataを`<成果物名>.json`へ書く（DB schemaを増やさない）。
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.application_builder.flow_app import packager, portable
from app.audit import service as audit
from app.config import data_dir
from app.database import get_db
from app.jobs import service as jobs
from app.models import User, Workflow
from app.security.deps import require_permission
from app.workflows.contracts import build_input_schema, build_output_schema

router = APIRouter(prefix="/flow-apps", tags=["flow-app"])
MAX_EXPORTS_PER_WORKFLOW = 20
# .pyz と拡張子なしの単一バイナリの両方を受ける。
ARTIFACT_NAME = re.compile(r"^[a-zA-Z0-9._-]{1,120}$")


class FlowAppExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=500)
    format: Literal["pyz", "binary"] = "pyz"


def _exports_root() -> Path:
    root = (data_dir() / "flow-apps").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _workflow_dir(workflow_id: int) -> Path:
    root = _exports_root()
    directory = (root / str(int(workflow_id))).resolve()
    if not directory.is_relative_to(root):
        raise HTTPException(status_code=400, detail="書き出し先が不正です")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _workflow_or_404(db: Session, workflow_id: int) -> Workflow:
    row = db.get(Workflow, workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ワークフローが見つかりません")
    return row


def _definition(row: Workflow) -> dict[str, Any]:
    try:
        parsed = json.loads(row.definition_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="ワークフロー定義を解析できません") from exc
    return parsed if isinstance(parsed, dict) else {}


def _meta_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.json")


def _artifact_path(workflow_id: int, filename: str) -> Path:
    if not ARTIFACT_NAME.fullmatch(filename) or filename.endswith(".json"):
        raise HTTPException(status_code=404, detail="成果物が見つかりません")
    directory = _workflow_dir(workflow_id)
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(status_code=404, detail="成果物が見つかりません")
    return path


def _list_exports(workflow_id: int) -> list[dict[str, Any]]:
    directory = _workflow_dir(workflow_id)
    rows: list[dict[str, Any]] = []
    artifacts = [item for item in directory.iterdir() if item.is_file() and item.suffix != ".json"]
    for path in sorted(artifacts, key=lambda item: item.stat().st_mtime, reverse=True):
        meta_path = _meta_path(path)
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        stat = path.stat()
        rows.append({
            "filename": path.name,
            "size": stat.st_size,
            "createdAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "checksum": meta.get("checksum", ""),
            "format": meta.get("format", "pyz" if path.suffix == ".pyz" else "binary"),
            "name": meta.get("name", path.stem),
            "nodeCount": meta.get("nodeCount", 0),
            "inputs": meta.get("inputs", []),
            "outputs": meta.get("outputs", []),
            "requires": meta.get("requires", "python3.11+"),
        })
    return rows


@router.get("/capability")
def flow_app_capability(user: User = Depends(require_permission("application_builder.view"))):
    return packager.clean_environment()


@router.get("/{workflow_id}/preview")
def flow_app_preview(
    workflow_id: int, user: User = Depends(require_permission("application_builder.view")),
    db: Session = Depends(get_db),
):
    """書き出さずに、対応可否・入出力・診断だけを返す（副作用なし）。"""
    row = _workflow_or_404(db, workflow_id)
    definition = _definition(row)
    analysis = portable.analyze(definition)
    return {
        "workflowId": row.id,
        "name": row.name,
        "description": row.description or "",
        **analysis,
        "inputs": packager._schema_fields(build_input_schema(definition)),
        "outputs": packager._schema_fields(build_output_schema(definition)),
        "exports": _list_exports(row.id),
    }


@router.post("/{workflow_id}/exports", status_code=201)
def create_flow_app_export(
    workflow_id: int, body: FlowAppExportBody, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    """Workflowを単一の実行ファイルへ書き出す。

    .pyzは1〜2秒なので同期。単一バイナリは1〜2分かかるため、サーバー側jobで進める。
    """
    row = _workflow_or_404(db, workflow_id)
    definition = _definition(row)
    name = (body.name or row.name or f"workflow-{row.id}").strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    directory = _workflow_dir(row.id)

    if body.format == "binary":
        target = directory / f"{packager.slugify(name)}-{stamp}"
        description = (body.description or row.description or "").strip()

        async def run(job: jobs.Job) -> dict:
            job.set_progress("ビルド環境を準備中", 0, 3)
            meta = await asyncio.to_thread(
                packager.build_binary, name=name, description=description, definition=definition,
                workflow_id=row.id, output_path=target, progress=job.set_progress,
            )
            meta["name"] = name
            _meta_path(target).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            job.set_progress("完了", 3, 3)
            return {"filename": target.name, "size": meta["size"], "checksum": meta["checksum"]}

        job = jobs.create(
            "flow_app.binary", f"単一バイナリ書き出し: {name}", run, owner_user_id=user.id,
            idempotency_key=request.headers.get("idempotency-key"),
        )
        audit.record(
            db, "flow_app.export", user=user, resource_type="workflow", resource_id=str(row.id),
            request=request, metadata={"format": "binary", "job_id": job.id},
        )
        return {"job_id": job.id, "format": "binary", "filename": target.name}

    target = directory / f"{packager.slugify(name)}-{stamp}.pyz"
    try:
        meta = packager.build_flow_app(
            name=name, description=(body.description or row.description or "").strip(),
            definition=definition, workflow_id=row.id, output_path=target,
        )
    except packager.FlowAppError as exc:
        audit.record(
            db, "flow_app.export", user=user, resource_type="workflow", resource_id=str(row.id),
            request=request, result="failure", metadata={"reason": str(exc)[:200]},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    meta["name"] = name
    _meta_path(target).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    for stale in _list_exports(row.id)[MAX_EXPORTS_PER_WORKFLOW:]:
        stale_path = directory / stale["filename"]
        stale_path.unlink(missing_ok=True)
        _meta_path(stale_path).unlink(missing_ok=True)
    audit.record(
        db, "flow_app.export", user=user, resource_type="workflow", resource_id=str(row.id),
        request=request, metadata={"filename": target.name, "size": meta["size"], "format": "pyz"},
    )
    return {
        "filename": target.name, "size": meta["size"], "checksum": meta["checksum"], "format": "pyz",
        "name": name, "inputs": meta["inputs"], "outputs": meta["outputs"],
        "nodeCount": meta["nodeCount"], "diagnostics": meta["diagnostics"],
        "requires": meta["requires"], "runHint": meta["runHint"],
        "createdAt": meta["generatedAt"],
    }


@router.get("/{workflow_id}/exports")
def list_flow_app_exports(
    workflow_id: int, user: User = Depends(require_permission("application_builder.view")),
    db: Session = Depends(get_db),
):
    _workflow_or_404(db, workflow_id)
    return _list_exports(workflow_id)


@router.get("/{workflow_id}/exports/{filename}/download")
def download_flow_app_export(
    workflow_id: int, filename: str,
    user: User = Depends(require_permission("application_builder.view")), db: Session = Depends(get_db),
):
    _workflow_or_404(db, workflow_id)
    path = _artifact_path(workflow_id, filename)
    return FileResponse(
        path, media_type="application/octet-stream", filename=path.name,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{workflow_id}/exports/{filename}", status_code=204)
def delete_flow_app_export(
    workflow_id: int, filename: str, request: Request,
    user: User = Depends(require_permission("application_builder.edit")), db: Session = Depends(get_db),
):
    _workflow_or_404(db, workflow_id)
    path = _artifact_path(workflow_id, filename)
    path.unlink(missing_ok=True)
    _meta_path(path).unlink(missing_ok=True)
    audit.record(
        db, "flow_app.delete", user=user, resource_type="workflow", resource_id=str(workflow_id),
        request=request, metadata={"filename": filename},
    )
    return None


def purge_workflow_exports(workflow_id: int) -> None:
    """Workflow削除時に成果物も片付ける。"""
    shutil.rmtree(_workflow_dir(workflow_id), ignore_errors=True)
