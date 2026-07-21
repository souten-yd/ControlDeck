from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.config import data_dir
from app.database import get_db
from app.models import TerminalAutomationSchedule, TerminalCommandRun, TerminalSnippet, User
from app.schemas.terminal_automation import (
    ComposeRequest,
    ScheduleCreate,
    ScheduleUpdate,
    SnippetCreate,
    SnippetUpdate,
)
from app.security.crypto import encrypt_text
from app.security.deps import require_permission
from app.terminals import automation, automation_systemd

router = APIRouter(prefix="/terminal-automation", tags=["terminal-automation"])
use_terminal = require_permission("terminal.use")
manage_automation = require_permission("settings.manage")


def _validate_execution_policy(body: ComposeRequest) -> None:
    if body.mode == "detached" and body.condition_type != "always":
        raise HTTPException(status_code=422, detail="Detached runにはTerminal条件を指定できません")


def _validate_schedule_policy(body: ScheduleCreate) -> None:
    _validate_execution_policy(body)
    if body.mode == "terminal" and body.condition_type == "always":
        raise HTTPException(
            status_code=422,
            detail="Scheduleから対話セッションへ無条件送信はできません。Shell readyまたはProgram matchesを指定してください",
        )


@router.get("/snippets")
def list_snippets(
    user: User = Depends(use_terminal), db: Session = Depends(get_db),
):
    rows = db.execute(select(TerminalSnippet).order_by(TerminalSnippet.name, TerminalSnippet.id)).scalars()
    return {"snippets": [automation.snippet_dict(row) for row in rows]}


@router.post("/snippets", status_code=201)
def create_snippet(
    body: SnippetCreate, request: Request,
    user: User = Depends(manage_automation), db: Session = Depends(get_db),
):
    row = TerminalSnippet(
        name=body.name, description=body.description, content=body.content,
        variables_json=json.dumps([item.model_dump() for item in body.variables], ensure_ascii=False),
        tags_json=json.dumps(body.tags, ensure_ascii=False), created_by=user.id,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同じ名前のSnippetがあります") from exc
    db.refresh(row)
    audit.record(
        db, "terminal.snippet_create", user=user, request=request,
        resource_type="terminal_snippet", resource_id=str(row.id), metadata={"name": row.name},
    )
    return automation.snippet_dict(row)


@router.patch("/snippets/{snippet_id}")
def update_snippet(
    snippet_id: int, body: SnippetUpdate, request: Request,
    user: User = Depends(manage_automation), db: Session = Depends(get_db),
):
    row = db.get(TerminalSnippet, snippet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Snippetが見つかりません")
    merged = SnippetCreate(
        name=body.name if body.name is not None else row.name,
        description=body.description if body.description is not None else row.description,
        content=body.content if body.content is not None else row.content,
        variables=body.variables if body.variables is not None else automation._loads(row.variables_json, []),
        tags=body.tags if body.tags is not None else automation._loads(row.tags_json, []),
    )
    row.name = merged.name
    row.description = merged.description
    row.content = merged.content
    row.variables_json = json.dumps([item.model_dump() for item in merged.variables], ensure_ascii=False)
    row.tags_json = json.dumps(merged.tags, ensure_ascii=False)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同じ名前のSnippetがあります") from exc
    db.refresh(row)
    audit.record(
        db, "terminal.snippet_update", user=user, request=request,
        resource_type="terminal_snippet", resource_id=str(row.id), metadata={"name": row.name},
    )
    return automation.snippet_dict(row)


@router.delete("/snippets/{snippet_id}")
def delete_snippet(
    snippet_id: int, request: Request,
    user: User = Depends(manage_automation), db: Session = Depends(get_db),
):
    row = db.get(TerminalSnippet, snippet_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Snippetが見つかりません")
    schedules = automation.schedules_using_snippet(db, snippet_id)
    if schedules:
        raise HTTPException(status_code=409, detail="Scheduleで使用中です。先にScheduleを変更または削除してください")
    name = row.name
    db.delete(row)
    db.commit()
    audit.record(
        db, "terminal.snippet_delete", user=user, request=request,
        resource_type="terminal_snippet", resource_id=str(snippet_id), metadata={"name": name},
    )
    return {"ok": True}


@router.post("/preview")
def preview_compose(
    body: ComposeRequest, user: User = Depends(use_terminal), db: Session = Depends(get_db),
):
    return automation.preview(db, body)


@router.post("/runs", status_code=202)
def start_run(
    body: ComposeRequest, request: Request,
    user: User = Depends(use_terminal), db: Session = Depends(get_db),
):
    _validate_execution_policy(body)
    run = automation.create_run(db, body, user)
    audit.record(
        db, "terminal.automation_start", user=user, request=request,
        resource_type="terminal_run", resource_id=str(run.id),
        metadata={
            "snippet_ids": body.snippet_ids, "mode": body.mode,
            "target_session_id": body.target_session_id, "command_checksum": run.command_checksum,
        },
    )
    return automation.run_dict(run)


@router.get("/runs")
def list_runs(
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(use_terminal), db: Session = Depends(get_db),
):
    rows = db.execute(select(TerminalCommandRun).order_by(
        TerminalCommandRun.id.desc(),
    ).limit(limit)).scalars()
    return {"runs": [automation.run_dict(row) for row in rows]}


@router.get("/runs/{run_id}/output")
def run_output(
    run_id: int, user: User = Depends(use_terminal), db: Session = Depends(get_db),
):
    row = db.get(TerminalCommandRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Runが見つかりません")
    if not row.output_path:
        return {"output": "", "available": False}
    root = (data_dir() / "terminal-automation" / "runs").resolve()
    path = Path(row.output_path).resolve()
    if path.parent != root or path.name != f"{row.id}.log":
        raise HTTPException(status_code=409, detail="Run log pathが不正です")
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return {"output": "", "available": False}
    return {"output": content[-2 * 1024 * 1024:].decode("utf-8", errors="replace"), "available": True}


@router.get("/schedules")
def list_schedules(
    user: User = Depends(use_terminal), db: Session = Depends(get_db),
):
    rows = db.execute(select(TerminalAutomationSchedule).order_by(
        TerminalAutomationSchedule.enabled.desc(), TerminalAutomationSchedule.next_run_at,
    )).scalars()
    result = []
    for row in rows:
        item = automation.schedule_dict(row)
        item["parameters"] = automation.schedule_parameters(row)
        result.append(item)
    return {"schedules": result}


@router.post("/schedules", status_code=201)
def create_schedule(
    body: ScheduleCreate, request: Request,
    user: User = Depends(manage_automation), db: Session = Depends(get_db),
):
    _validate_schedule_policy(body)
    row = automation.create_schedule(db, body, user)
    audit.record(
        db, "terminal.schedule_create", user=user, request=request,
        resource_type="terminal_schedule", resource_id=str(row.id),
        metadata={"recurrence": row.recurrence, "mode": row.mode, "next_run_at": row.next_run_at.isoformat()},
    )
    return automation.schedule_dict(row)


@router.patch("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: int, body: ScheduleUpdate, request: Request,
    user: User = Depends(manage_automation), db: Session = Depends(get_db),
):
    row = db.get(TerminalAutomationSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Scheduleが見つかりません")
    values = body.model_dump(exclude_unset=True)
    current = {
        "name": row.name, "snippet_ids": automation._loads(row.snippet_ids_json, []),
        "parameters": automation.schedule_parameters(row), "mode": row.mode,
        "target_session_id": row.target_session_id, "working_directory": row.working_directory,
        "condition_type": row.condition_type, "condition_value": row.condition_value,
        "timeout_seconds": row.timeout_seconds, "recurrence": row.recurrence,
        "next_run_at": row.next_run_at.replace(tzinfo=timezone.utc) if row.next_run_at.tzinfo is None else row.next_run_at,
        "timezone": row.timezone, "run_if_missed": row.run_if_missed,
    }
    enabled = bool(values.pop("enabled", row.enabled))
    current.update(values)
    merged = ScheduleCreate(**current)
    _validate_schedule_policy(merged)
    automation.compose(db, merged.snippet_ids, merged.parameters, merged.working_directory)
    row.name = merged.name.strip()
    row.snippet_ids_json = json.dumps(merged.snippet_ids)
    row.parameters_encrypted = encrypt_text(json.dumps(merged.parameters, ensure_ascii=False)) if merged.parameters else None
    row.mode = merged.mode
    row.target_session_id = merged.target_session_id
    row.working_directory = str(automation.resolve_working_directory(merged.working_directory))
    row.condition_type = merged.condition_type
    row.condition_value = merged.condition_value.strip()
    row.timeout_seconds = merged.timeout_seconds
    row.recurrence = merged.recurrence
    row.next_run_at = merged.next_run_at.astimezone(timezone.utc)
    row.timezone = merged.timezone
    row.run_if_missed = merged.run_if_missed
    row.enabled = enabled
    row.status = "SCHEDULED" if enabled else "PAUSED"
    db.commit()
    try:
        if enabled:
            automation_systemd.install_schedule(row)
        else:
            automation_systemd.disable_schedule(row.id, remove_files=False)
    except (OSError, RuntimeError, ValueError) as exc:
        row.status = "INSTALL_FAILED"
        db.commit()
        raise HTTPException(status_code=503, detail="systemd user timerを更新できません") from exc
    audit.record(
        db, "terminal.schedule_update", user=user, request=request,
        resource_type="terminal_schedule", resource_id=str(row.id),
        metadata={"enabled": row.enabled, "recurrence": row.recurrence, "mode": row.mode},
    )
    return automation.schedule_dict(row)


@router.delete("/schedules/{schedule_id}")
def delete_schedule(
    schedule_id: int, request: Request,
    user: User = Depends(manage_automation), db: Session = Depends(get_db),
):
    row = db.get(TerminalAutomationSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Scheduleが見つかりません")
    automation_systemd.disable_schedule(row.id, remove_files=True)
    automation.detach_schedule_runs(db, row.id)
    db.delete(row)
    db.commit()
    audit.record(
        db, "terminal.schedule_delete", user=user, request=request,
        resource_type="terminal_schedule", resource_id=str(schedule_id),
    )
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run-now", status_code=202)
def run_schedule_now(
    schedule_id: int, request: Request,
    user: User = Depends(use_terminal), db: Session = Depends(get_db),
):
    row = db.get(TerminalAutomationSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Scheduleが見つかりません")
    run = automation.create_run(db, automation.schedule_compose(row), user, schedule_id=row.id)
    audit.record(
        db, "terminal.schedule_run_now", user=user, request=request,
        resource_type="terminal_schedule", resource_id=str(row.id), metadata={"run_id": run.id},
    )
    return automation.run_dict(run)
