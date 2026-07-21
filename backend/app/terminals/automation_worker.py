"""systemd user services for terminal snippet runs and schedule triggers."""
from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.audit import service as audit
from app.config import data_dir, get_config
from app.database import SessionLocal
from app.models import TerminalAutomationSchedule, TerminalCommandRun, utcnow
from app.security.crypto import decrypt_text
from app.terminals import automation
from app.terminals.manager import manager

LOG_LIMIT = 2 * 1024 * 1024


def _log_path(run_id: int) -> Path:
    root = (data_dir() / "terminal-automation" / "runs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    path = (root / f"{run_id}.log").resolve()
    if path.parent != root:
        raise ValueError("invalid automation log path")
    return path


def _bounded_output(stdout: bytes, stderr: bytes) -> bytes:
    content = b"[stdout]\n" + stdout + b"\n[stderr]\n" + stderr
    if len(content) <= LOG_LIMIT:
        return content
    marker = b"\n[Control Deck: output older than 2 MiB was truncated]\n"
    return marker + content[-(LOG_LIMIT - len(marker)):]


def _write_log(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(f".tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)
    os.replace(temporary, path)


def _finish(
    run: TerminalCommandRun, *, status: str, exit_code: int | None = None, error: str = "",
) -> None:
    run.status = status
    run.exit_code = exit_code
    run.error = error[:500]
    run.finished_at = utcnow()


def execute_run(run_id: int) -> int:
    db = SessionLocal()
    try:
        run = db.get(TerminalCommandRun, run_id)
        if run is None:
            return 2
        if run.status != "QUEUED":
            return 0 if run.status in {"SUCCEEDED", "SKIPPED"} else 1
        run.status = "RUNNING"
        run.started_at = utcnow()
        db.commit()
        try:
            command = decrypt_text(run.command_snapshot_encrypted)
        except Exception:
            _finish(run, status="FAILED", error="実行snapshotを復号できません")
            db.commit()
            return 1

        ready, reason, _session = automation.session_condition(
            run.mode, run.target_session_id, run.condition_type, run.condition_value,
        )
        if not ready:
            _finish(run, status="SKIPPED", error=reason)
            result_code = 0
        elif run.mode == "terminal":
            try:
                manager.inject_input(run.target_session_id or "", command, submit=True)
                _finish(run, status="SUCCEEDED", exit_code=0)
                result_code = 0
            except (KeyError, OSError, RuntimeError, ValueError) as exc:
                _finish(run, status="FAILED", error=str(exc))
                result_code = 1
        else:
            try:
                cwd = automation.resolve_working_directory(run.working_directory)
                shell = Path(get_config().terminal.shell).expanduser().resolve()
                if not shell.is_absolute() or not shell.is_file() or not os.access(shell, os.X_OK):
                    raise OSError("設定されたshellを実行できません")
                env = {
                    "HOME": str(Path.home()),
                    "USER": os.environ.get("USER", ""),
                    "LOGNAME": os.environ.get("LOGNAME", os.environ.get("USER", "")),
                    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                    "LANG": os.environ.get("LANG", "C.UTF-8"),
                    "SHELL": str(shell),
                    "TERM": "dumb",
                }
                completed = subprocess.run(
                    [str(shell), "-lc", command], cwd=cwd, env=env,
                    capture_output=True, timeout=run.timeout_seconds,
                )
                log_path = _log_path(run.id)
                _write_log(log_path, _bounded_output(completed.stdout, completed.stderr))
                run.output_path = str(log_path)
                _finish(
                    run, status="SUCCEEDED" if completed.returncode == 0 else "FAILED",
                    exit_code=completed.returncode,
                    error="" if completed.returncode == 0 else "Command exited with a non-zero status",
                )
                result_code = 0 if completed.returncode == 0 else 1
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode()
                stderr = exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode()
                log_path = _log_path(run.id)
                _write_log(log_path, _bounded_output(stdout, stderr))
                run.output_path = str(log_path)
                _finish(run, status="TIMED_OUT", error="実行時間の上限を超えました")
                result_code = 1
            except (OSError, RuntimeError, ValueError) as exc:
                _finish(run, status="FAILED", error=str(exc))
                result_code = 1

        if run.schedule_id is not None:
            schedule = db.get(TerminalAutomationSchedule, run.schedule_id)
            if schedule is not None:
                schedule.last_run_at = run.finished_at
                schedule.last_result = run.status
        db.commit()
        audit.record(
            db, "terminal.automation_execute", username=run.created_by_username,
            resource_type="terminal_run", resource_id=str(run.id),
            result="success" if run.status in {"SUCCEEDED", "SKIPPED"} else "failure",
            metadata={
                "mode": run.mode, "status": run.status, "schedule_id": run.schedule_id,
                "target_session_id": run.target_session_id, "command_checksum": run.command_checksum,
            },
        )
        return result_code
    finally:
        db.close()


def trigger_schedule(schedule_id: int) -> int:
    db = SessionLocal()
    try:
        row = db.get(TerminalAutomationSchedule, schedule_id)
        if row is None or not row.enabled:
            return 0
        try:
            body = automation.schedule_compose(row)
            run = automation.create_run(
                db, body, None, schedule_id=row.id, username=row.created_by_username,
            )
            row.last_run_at = datetime.now(timezone.utc)
            row.last_result = "QUEUED"
            automation.advance_schedule(row)
            db.commit()
            audit.record(
                db, "terminal.schedule_trigger", username=row.created_by_username,
                resource_type="terminal_schedule", resource_id=str(row.id),
                metadata={"run_id": run.id, "recurrence": row.recurrence, "mode": row.mode},
            )
            return 0
        except Exception as exc:
            row.status = "TRIGGER_FAILED"
            row.last_result = "FAILED"
            db.commit()
            audit.record(
                db, "terminal.schedule_trigger", username=row.created_by_username,
                resource_type="terminal_schedule", resource_id=str(row.id), result="failure",
                metadata={"reason": type(exc).__name__},
            )
            return 1
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", type=int)
    group.add_argument("--schedule-id", type=int)
    args = parser.parse_args()
    if args.run_id is not None and args.run_id > 0:
        return execute_run(args.run_id)
    if args.schedule_id is not None and args.schedule_id > 0:
        return trigger_schedule(args.schedule_id)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
