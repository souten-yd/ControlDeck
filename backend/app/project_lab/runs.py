"""Project Labのdurable systemd user run、status、log、artifact差分。"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProjectRun, ProjectRunArtifact
from app.project_lab import service

RUNNING_STATES = {"QUEUED", "RUNNING", "CANCELING"}
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "INTERRUPTED"}
MAX_CONCURRENT_RUNS = 3
MAX_LOG_BYTES = 1024 * 1024
MAX_CHECKSUM_BYTES = 512 * 1024 * 1024
ALLOWED_EXECUTABLE = re.compile(
    r"^(python(?:3(?:\.\d+)?)?|pytest|node|npm|npx|pnpm|yarn|dotnet|cmake|ctest|make|ninja)$", re.I,
)


class ProjectRunError(RuntimeError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_executable(command: str, cwd: Path, project: Path) -> str:
    normalized = command.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if not ALLOWED_EXECUTABLE.fullmatch(basename):
        raise ProjectRunError(f"実行file '{basename}' はProject Labの許可SDKではありません")
    if "/" not in normalized:
        resolved = shutil.which(command)
        if not resolved:
            raise ProjectRunError(f"実行file '{command}' が見つかりません")
        return str(Path(resolved).resolve())
    if normalized.startswith(("/", "~")) or ".." in normalized.split("/"):
        raise ProjectRunError("実行file pathはproject内の相対pathで指定してください")
    candidate = (cwd / normalized).resolve(strict=False)
    if not _inside(candidate, project):
        raise ProjectRunError("実行fileがproject外です")
    try:
        resolved_target = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ProjectRunError("実行fileが見つかりません") from exc
    if not resolved_target.is_file() or not os.access(candidate, os.X_OK):
        raise ProjectRunError("実行fileに実行権限がありません")
    return str(candidate)


def _artifact_snapshot(project: Path, manifest) -> dict[str, dict[str, int]]:
    snapshot: dict[str, dict[str, int]] = {}
    for path in service._artifact_candidates(project, manifest):
        info = service.artifact_info(project, path)
        if info is None:
            continue
        try:
            stat = path.resolve(strict=True).stat()
        except OSError:
            continue
        snapshot[info["path"]] = {"size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
    return snapshot


def _checksum(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_CHECKSUM_BYTES:
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _systemd_tools() -> tuple[str, str, str]:
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl")
    journalctl = shutil.which("journalctl")
    if not systemd_run or not systemctl or not journalctl:
        raise ProjectRunError("systemd user managerの実行toolを利用できません")
    return systemd_run, systemctl, journalctl


def start_run(
    db: Session, *, project_id: str, profile_id: str, timeout_seconds: int, created_by: int | None,
) -> ProjectRun:
    project = service.resolve_project(project_id)
    manifest = service.load_manifest(project)
    profile = next((item for item in manifest.profiles if item.id == profile_id), None)
    if profile is None:
        raise ProjectRunError("実行profileが見つかりません")
    if profile.type not in {"cli", "test"}:
        raise ProjectRunError("このPhaseで実行できるprofileはcli/testだけです")
    if not profile.command:
        raise ProjectRunError("profile commandが空です")
    if profile.secret_refs:
        raise ProjectRunError("Secret注入はcredential分離Phaseまで利用できません")
    cwd = (project / profile.cwd).resolve()
    if not cwd.is_dir() or not _inside(cwd, project):
        raise ProjectRunError("profile cwdがproject外か、存在しません")
    executable = _resolve_executable(profile.command[0], cwd, project)
    systemd_run, _, _ = _systemd_tools()
    for row in db.execute(select(ProjectRun).where(ProjectRun.status.in_(RUNNING_STATES))).scalars().all():
        refresh_run(db, row)
    active = db.execute(select(ProjectRun).where(ProjectRun.status.in_(RUNNING_STATES))).scalars().all()
    if len(active) >= MAX_CONCURRENT_RUNS:
        raise ProjectRunError("Project Labの同時実行上限に達しています")
    if any(row.project_id == project_id for row in active):
        raise ProjectRunError("同じprojectの実行が既に進行中です")

    snapshot = _artifact_snapshot(project, manifest)
    row = ProjectRun(
        project_id=project_id, project_name=manifest.name, profile_id=profile.id,
        profile_type=profile.type, status="QUEUED", command_json=json.dumps(profile.command, ensure_ascii=False),
        environment_names_json=json.dumps(sorted(profile.environment), ensure_ascii=False),
        working_directory=str(cwd), timeout_seconds=timeout_seconds,
        initial_artifacts_json=json.dumps(snapshot, ensure_ascii=False), created_by=created_by,
    )
    db.add(row)
    db.commit()
    row.unit_name = f"control-deck-project-run-{row.id}"
    db.commit()

    argv = [
        systemd_run, "--user", "--quiet", f"--unit={row.unit_name}",
        "--property=Type=exec", f"--property=WorkingDirectory={cwd}",
        "--property=RemainAfterExit=yes",
        f"--property=RuntimeMaxSec={timeout_seconds}s", "--property=TimeoutStopSec=10s",
        "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes", "--property=ProtectSystem=strict",
        "--property=ProtectHome=read-only", f"--property=ReadWritePaths={project}",
        "--property=MemoryMax=2G", "--property=TasksMax=128", "--property=CPUQuota=200%",
        "--property=UMask=0077",
    ]
    for key, value in sorted(profile.environment.items()):
        argv.append(f"--setenv={key}={value}")
    argv.extend([executable, *profile.command[1:]])
    result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode != 0:
        row.status = "FAILED"
        row.result = "START_FAILED"
        row.error_redacted = service.redact_text((result.stderr or result.stdout or "systemd-run failed")[:4000])
        row.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise ProjectRunError(row.error_redacted)
    row.status = "RUNNING"
    db.commit()
    return row


def _show(unit_name: str) -> dict[str, str] | None:
    _, systemctl, _ = _systemd_tools()
    result = subprocess.run(
        [systemctl, "--user", "show", f"{unit_name}.service", "--no-pager",
         "--property=LoadState", "--property=ActiveState", "--property=SubState",
         "--property=Result", "--property=ExecMainStatus"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if result.returncode != 0:
        return None
    return {key: value for line in result.stdout.splitlines() if "=" in line for key, value in [line.split("=", 1)]}


def _finalize_artifacts(db: Session, row: ProjectRun) -> None:
    if db.execute(select(ProjectRunArtifact).where(ProjectRunArtifact.run_id == row.id)).first():
        return
    try:
        project = service.resolve_project(row.project_id)
        manifest = service.load_manifest(project)
        initial = json.loads(row.initial_artifacts_json or "{}")
    except (ValueError, json.JSONDecodeError):
        return
    current = _artifact_snapshot(project, manifest)
    for relative, state in current.items():
        old = initial.get(relative)
        if old == state:
            continue
        try:
            path = service.resolve_artifact(project, relative)
        except service.ProjectLabError:
            continue
        db.add(ProjectRunArtifact(
            run_id=row.id, path=relative, kind=service.ARTIFACT_KINDS.get(path.suffix.lower(), "file"),
            mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            size=state["size"], checksum=_checksum(path), change_type="created" if old is None else "modified",
        ))


def refresh_run(db: Session, row: ProjectRun) -> ProjectRun:
    if row.status in TERMINAL_STATES:
        return row
    state = _show(row.unit_name)
    if state is None or state.get("LoadState") == "not-found":
        row.status = "INTERRUPTED"
        row.result = "UNIT_NOT_FOUND"
        row.error_redacted = "systemd unitの状態を取得できません"
    elif state.get("ActiveState") in {"active", "activating", "reloading"} and state.get("SubState") != "exited":
        row.status = "RUNNING"
        db.commit()
        return row
    else:
        result = state.get("Result", "")
        try:
            row.exit_code = int(state.get("ExecMainStatus", "0"))
        except ValueError:
            row.exit_code = None
        if result == "timeout":
            row.status = "TIMED_OUT"
        elif result in {"success", ""} and (row.exit_code in {0, None}):
            row.status = "SUCCEEDED"
        else:
            row.status = "FAILED"
        row.result = result or row.status.lower()
    row.finished_at = datetime.now(timezone.utc)
    _finalize_artifacts(db, row)
    db.commit()
    return row


def cancel_run(db: Session, row: ProjectRun) -> ProjectRun:
    refresh_run(db, row)
    if row.status not in RUNNING_STATES:
        raise ProjectRunError("この実行は既に終了しています")
    _, systemctl, _ = _systemd_tools()
    result = subprocess.run(
        [systemctl, "--user", "stop", f"{row.unit_name}.service"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if result.returncode != 0:
        raise ProjectRunError("systemd unitを停止できません")
    row.status = "CANCELED"
    row.result = "canceled"
    row.finished_at = datetime.now(timezone.utc)
    _finalize_artifacts(db, row)
    db.commit()
    return row


def run_logs(row: ProjectRun) -> str:
    _, _, journalctl = _systemd_tools()
    result = subprocess.run(
        [journalctl, "--user", "--unit", f"{row.unit_name}.service", "--output=cat", "--no-pager", "--lines=2000"],
        capture_output=True, timeout=10, check=False,
    )
    raw = result.stdout[-MAX_LOG_BYTES:].decode("utf-8", errors="replace")
    return service.redact_text(raw)


def run_out(db: Session, row: ProjectRun, *, include_logs: bool = False) -> dict[str, Any]:
    refresh_run(db, row)
    artifacts = db.execute(select(ProjectRunArtifact).where(ProjectRunArtifact.run_id == row.id).order_by(ProjectRunArtifact.id)).scalars().all()
    elapsed_ms = None
    if row.finished_at:
        started_at = row.started_at if row.started_at.tzinfo else row.started_at.replace(tzinfo=timezone.utc)
        finished_at = row.finished_at if row.finished_at.tzinfo else row.finished_at.replace(tzinfo=timezone.utc)
        elapsed_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    return {
        "id": row.id, "projectId": row.project_id, "projectName": row.project_name,
        "profileId": row.profile_id, "profileType": row.profile_type, "status": row.status,
        "command": json.loads(row.command_json or "[]"), "environmentNames": json.loads(row.environment_names_json or "[]"),
        "workingDirectory": row.working_directory, "timeoutSeconds": row.timeout_seconds,
        "result": row.result, "exitCode": row.exit_code, "error": row.error_redacted,
        "startedAt": row.started_at.isoformat(), "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
        "elapsedMs": elapsed_ms,
        "artifacts": [{
            "id": item.id, "path": item.path, "kind": item.kind, "mimeType": item.mime_type,
            "size": item.size, "checksum": item.checksum, "changeType": item.change_type,
        } for item in artifacts],
        **({"logs": run_logs(row)} if include_logs else {}),
    }
