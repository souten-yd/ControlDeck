"""Snippet composition, validation and durable run/schedule orchestration."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.files import service as files
from app.models import TerminalAutomationSchedule, TerminalCommandRun, TerminalSnippet, User, utcnow
from app.schemas.terminal_automation import ComposeRequest, ScheduleCreate, SnippetCreate
from app.security.crypto import decrypt_text, encrypt_text
from app.terminals import automation_systemd
from app.terminals.manager import manager

PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
RECURRENCE_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14}


def _loads(value: str, fallback):
    try:
        parsed = json.loads(value)
        return parsed
    except (TypeError, json.JSONDecodeError):
        return fallback


def snippet_dict(row: TerminalSnippet) -> dict[str, object]:
    return {
        "id": row.id, "name": row.name, "description": row.description, "content": row.content,
        "variables": _loads(row.variables_json, []), "tags": _loads(row.tags_json, []),
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
    }


def schedule_dict(row: TerminalAutomationSchedule) -> dict[str, object]:
    return {
        "id": row.id, "name": row.name, "snippet_ids": _loads(row.snippet_ids_json, []),
        "mode": row.mode, "target_session_id": row.target_session_id,
        "working_directory": row.working_directory, "condition_type": row.condition_type,
        "condition_value": row.condition_value, "recurrence": row.recurrence,
        "next_run_at": row.next_run_at.isoformat(), "timezone": row.timezone,
        "run_if_missed": row.run_if_missed, "timeout_seconds": row.timeout_seconds,
        "enabled": row.enabled, "status": row.status,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "last_result": row.last_result, "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def run_dict(row: TerminalCommandRun) -> dict[str, object]:
    return {
        "id": row.id, "schedule_id": row.schedule_id,
        "snippet_ids": _loads(row.snippet_ids_json, []), "mode": row.mode,
        "target_session_id": row.target_session_id, "working_directory": row.working_directory,
        "condition_type": row.condition_type, "condition_value": row.condition_value,
        "timeout_seconds": row.timeout_seconds, "status": row.status, "unit_name": row.unit_name,
        "exit_code": row.exit_code, "error": row.error, "created_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def get_snippets_ordered(db: Session, snippet_ids: list[int]) -> list[TerminalSnippet]:
    rows = db.execute(select(TerminalSnippet).where(TerminalSnippet.id.in_(snippet_ids))).scalars().all()
    by_id = {row.id: row for row in rows}
    missing = [snippet_id for snippet_id in snippet_ids if snippet_id not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"Snippetが見つかりません: {missing[0]}")
    return [by_id[snippet_id] for snippet_id in snippet_ids]


def resolve_working_directory(value: str) -> Path:
    requested = value.strip() or str(Path.home())
    path = files.resolve(requested)
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="作業directoryではありません")
    return path.resolve()


def compose(
    db: Session, snippet_ids: list[int], parameters: dict[str, str], working_directory: str,
) -> tuple[str, list[TerminalSnippet], Path]:
    snippets = get_snippets_ordered(db, snippet_ids)
    cwd = resolve_working_directory(working_directory)
    declared: dict[str, dict[str, object]] = {}
    for snippet in snippets:
        for variable in _loads(snippet.variables_json, []):
            if isinstance(variable, dict) and isinstance(variable.get("name"), str):
                declared.setdefault(str(variable["name"]), variable)
    extras = sorted(set(parameters) - set(declared))
    if extras:
        raise HTTPException(status_code=422, detail=f"未宣言parameterです: {extras[0]}")
    now = datetime.now().astimezone()
    values: dict[str, str] = {
        "cwd": str(cwd), "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M:%S"),
    }
    for name, variable in declared.items():
        supplied = parameters.get(name, str(variable.get("default") or ""))
        if bool(variable.get("required")) and supplied == "":
            raise HTTPException(status_code=422, detail=f"parameterが必要です: {name}")
        values[name] = supplied

    def render(content: str) -> str:
        unknown = sorted(set(PLACEHOLDER_RE.findall(content)) - set(values))
        if unknown:
            raise HTTPException(status_code=422, detail=f"未宣言placeholderです: {unknown[0]}")
        return PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], content)

    command = "\n\n".join(render(snippet.content) for snippet in snippets)
    if not command.strip() or len(command.encode("utf-8")) > 256 * 1024 or "\x00" in command:
        raise HTTPException(status_code=422, detail="展開後commandが空または256 KiBを超えています")
    return command, snippets, cwd


def session_condition(
    mode: str, target_session_id: str | None, condition_type: str, condition_value: str,
) -> tuple[bool, str, dict[str, object] | None]:
    if mode != "terminal":
        if condition_type != "always":
            return False, "Detached runではTerminal条件を使用できません", None
        return True, "Ready", None
    session = next((item for item in manager.list_sessions() if item.get("id") == target_session_id), None)
    if session is None or not session.get("alive"):
        return False, "対象Terminalが存在しないか終了しています", session
    if condition_type == "shell_ready" and session.get("workload") != "idle":
        return False, f"Shell待機ではありません（{session.get('program') or 'unknown'}）", session
    if condition_type == "program_equals":
        expected = Path(condition_value.strip()).name
        if not expected or session.get("program") != expected:
            return False, f"Programが一致しません（現在: {session.get('program') or 'unknown'}）", session
    return True, "Ready", session


def preview(db: Session, body: ComposeRequest) -> dict[str, object]:
    command, snippets, cwd = compose(db, body.snippet_ids, body.parameters, body.working_directory)
    ready, reason, session = session_condition(
        body.mode, body.target_session_id, body.condition_type, body.condition_value,
    )
    return {
        "command": command, "command_bytes": len(command.encode()), "working_directory": str(cwd),
        "snippets": [{"id": row.id, "name": row.name} for row in snippets],
        "condition": {"ready": ready, "reason": reason, "session": session},
    }


def create_run(
    db: Session, body: ComposeRequest, user: User | None, *, schedule_id: int | None = None,
    username: str | None = None,
) -> TerminalCommandRun:
    command, _snippets, cwd = compose(db, body.snippet_ids, body.parameters, body.working_directory)
    run = TerminalCommandRun(
        schedule_id=schedule_id, snippet_ids_json=json.dumps(body.snippet_ids),
        command_snapshot_encrypted=encrypt_text(command),
        command_checksum=hashlib.sha256(command.encode()).hexdigest(), mode=body.mode,
        target_session_id=body.target_session_id, working_directory=str(cwd),
        condition_type=body.condition_type, condition_value=body.condition_value.strip(),
        timeout_seconds=body.timeout_seconds, status="QUEUED", created_by=user.id if user else None,
        created_by_username=username or (user.username if user else "scheduled"),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        automation_systemd.launch_run(run)
        db.commit()
    except (OSError, RuntimeError, ValueError) as exc:
        run.status = "FAILED"
        run.error = str(exc)[:500]
        run.finished_at = utcnow()
        db.commit()
        raise HTTPException(status_code=503, detail="systemd user serviceを開始できません") from exc
    return run


def schedule_parameters(row: TerminalAutomationSchedule) -> dict[str, str]:
    if not row.parameters_encrypted:
        return {}
    parsed = _loads(decrypt_text(row.parameters_encrypted), {})
    return {str(key): str(value) for key, value in parsed.items()} if isinstance(parsed, dict) else {}


def schedule_compose(row: TerminalAutomationSchedule) -> ComposeRequest:
    return ComposeRequest(
        snippet_ids=_loads(row.snippet_ids_json, []), parameters=schedule_parameters(row), mode=row.mode,
        target_session_id=row.target_session_id, working_directory=row.working_directory,
        condition_type=row.condition_type, condition_value=row.condition_value,
        timeout_seconds=row.timeout_seconds,
    )


def create_schedule(db: Session, body: ScheduleCreate, user: User) -> TerminalAutomationSchedule:
    # Validate all references/templates before committing a durable timer.
    compose(db, body.snippet_ids, body.parameters, body.working_directory)
    session_condition(body.mode, body.target_session_id, body.condition_type, body.condition_value)
    run_at = body.next_run_at.astimezone(timezone.utc)
    if run_at <= datetime.now(timezone.utc) - timedelta(minutes=1):
        raise HTTPException(status_code=422, detail="次回時刻が過去です")
    row = TerminalAutomationSchedule(
        name=body.name.strip(), snippet_ids_json=json.dumps(body.snippet_ids),
        parameters_encrypted=encrypt_text(json.dumps(body.parameters, ensure_ascii=False)) if body.parameters else None,
        mode=body.mode, target_session_id=body.target_session_id,
        working_directory=str(resolve_working_directory(body.working_directory)),
        condition_type=body.condition_type, condition_value=body.condition_value.strip(),
        recurrence=body.recurrence, next_run_at=run_at, timezone=body.timezone,
        run_if_missed=body.run_if_missed, timeout_seconds=body.timeout_seconds,
        enabled=True, status="SCHEDULED", created_by=user.id, created_by_username=user.username,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        automation_systemd.install_schedule(row)
    except (OSError, RuntimeError, ValueError) as exc:
        row.enabled = False
        row.status = "INSTALL_FAILED"
        db.commit()
        raise HTTPException(status_code=503, detail="systemd user timerを登録できません") from exc
    return row


def advance_schedule(row: TerminalAutomationSchedule, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    if row.recurrence == "once":
        row.enabled = False
        row.status = "COMPLETED"
        automation_systemd.disable_schedule(row.id, remove_files=False)
        return
    days = RECURRENCE_DAYS.get(row.recurrence)
    if days is None:
        row.enabled = False
        row.status = "INVALID"
        return
    next_at = row.next_run_at
    if next_at.tzinfo is None:
        next_at = next_at.replace(tzinfo=timezone.utc)
    while next_at <= now:
        next_at += timedelta(days=days)
    row.next_run_at = next_at
    row.status = "SCHEDULED"
    automation_systemd.install_schedule(row)


def schedules_using_snippet(db: Session, snippet_id: int) -> list[int]:
    rows = db.execute(select(TerminalAutomationSchedule)).scalars()
    return [row.id for row in rows if snippet_id in _loads(row.snippet_ids_json, [])]


def detach_schedule_runs(db: Session, schedule_id: int) -> None:
    db.execute(update(TerminalCommandRun).where(
        TerminalCommandRun.schedule_id == schedule_id,
    ).values(schedule_id=None))
