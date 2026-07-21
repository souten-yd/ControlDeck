"""Terminal automation units owned by Control Deck.

Scheduled definitions are durable user units; actual runs use transient user
services so the web process never owns a command process.
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import timezone
from pathlib import Path

from app.applications.systemd import _escape_exec_arg
from app.config import REPO_ROOT
from app.models import TerminalAutomationSchedule, TerminalCommandRun

UNIT_PREFIX = "control-deck-terminal-automation-"
UNIT_RE = re.compile(r"^control-deck-terminal-automation-(?:schedule|run)-[1-9][0-9]*\.(?:service|timer)$")


def _venv_python() -> Path:
    # Keep the absolute venv symlink path. Path.resolve() would turn it into
    # /usr/bin/python and lose the virtualenv's site-packages at process start.
    python = REPO_ROOT / ".venv" / "bin" / "python"
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise OSError("Control Deck Python virtualenvが見つかりません")
    return python


def _unit_dir() -> Path:
    root = (Path.home() / ".config" / "systemd" / "user").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, timeout=30,
    )


def _write_atomic(path: Path, content: str) -> None:
    root = _unit_dir()
    resolved_parent = path.parent.resolve()
    if resolved_parent != root or not UNIT_RE.fullmatch(path.name):
        raise ValueError("Control Deck管理外のunit pathです")
    temporary = root / f".{path.name}.tmp-{os.getpid()}"
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def schedule_unit_names(schedule_id: int) -> tuple[str, str]:
    if schedule_id < 1:
        raise ValueError("不正なschedule IDです")
    stem = f"{UNIT_PREFIX}schedule-{schedule_id}"
    return f"{stem}.service", f"{stem}.timer"


def install_schedule(schedule: TerminalAutomationSchedule) -> None:
    service_name, timer_name = schedule_unit_names(schedule.id)
    python = _venv_python()
    working = (REPO_ROOT / "backend").resolve()
    service = "\n".join([
        "[Unit]",
        f"Description=Control Deck terminal automation schedule {schedule.id}",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=exec",
        # WorkingDirectory is a path-valued directive, not an ExecStart argv.
        # ExecStart-style quotes are treated as literal path characters here.
        f"WorkingDirectory={working}",
        f"ExecStart={_escape_exec_arg(str(python))} -m app.terminals.automation_worker --schedule-id {schedule.id}",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "MemoryMax=512M",
        "TasksMax=128",
        "TimeoutStopSec=30s",
        "",
    ])
    run_at = schedule.next_run_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    calendar = run_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    timer = "\n".join([
        "[Unit]",
        f"Description=Control Deck terminal automation timer {schedule.id}",
        "",
        "[Timer]",
        f"OnCalendar={calendar}",
        "AccuracySec=1s",
        f"Persistent={'true' if schedule.run_if_missed else 'false'}",
        f"Unit={service_name}",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])
    root = _unit_dir()
    _write_atomic(root / service_name, service)
    _write_atomic(root / timer_name, timer)
    reload_result = _systemctl("daemon-reload")
    enable_result = _systemctl("enable", "--now", timer_name)
    active_result = _systemctl("is-active", "--quiet", timer_name)
    if reload_result.returncode != 0 or enable_result.returncode != 0 or active_result.returncode != 0:
        error = (
            active_result.stderr.strip() or enable_result.stderr.strip()
            or reload_result.stderr.strip() or "systemd timer did not become active"
        )
        raise RuntimeError(error)


def disable_schedule(schedule_id: int, *, remove_files: bool) -> None:
    service_name, timer_name = schedule_unit_names(schedule_id)
    _systemctl("disable", "--now", timer_name)
    if remove_files:
        root = _unit_dir()
        for name in (timer_name, service_name):
            if not UNIT_RE.fullmatch(name):
                raise ValueError("Control Deck管理外のunitです")
            (root / name).unlink(missing_ok=True)
        _systemctl("daemon-reload")


def launch_run(run: TerminalCommandRun) -> None:
    if run.id < 1:
        raise ValueError("不正なrun IDです")
    unit = f"{UNIT_PREFIX}run-{run.id}.service"
    if not UNIT_RE.fullmatch(unit):
        raise ValueError("不正なrun unitです")
    python = _venv_python()
    working = (REPO_ROOT / "backend").resolve()
    argv = [
        "systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
        "--property=Type=exec", "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes",
        "--property=MemoryMax=2G", "--property=CPUQuota=200%", "--property=TasksMax=512",
        "--property=TimeoutStopSec=30s", f"--working-directory={working}", "--",
        str(python), "-m", "app.terminals.automation_worker", "--run-id", str(run.id),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "systemd run launch failed")
    run.unit_name = unit.removesuffix(".service")
