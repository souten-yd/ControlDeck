"""Webプロセスから独立して電源予約を実行する systemd ユーザーtimer。"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from app.applications.systemd import _escape_exec_arg
from app.config import REPO_ROOT, data_dir

SERVICE = "control-deck-power-schedule.service"
TIMER = "control-deck-power-schedule.timer"


def unit_dir() -> Path:
    path = Path.home() / ".config" / "systemd" / "user"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    return data_dir() / "power-schedule.json"


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, timeout=30
    )


def _write_state(state: dict) -> None:
    target = state_path()
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)


def read_state() -> dict | None:
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if state.get("action") not in ("reboot", "shutdown") or not isinstance(state.get("at"), str):
        return None
    return state


def install(action: str, at: datetime, username: str) -> dict:
    if action not in ("reboot", "shutdown") or at.tzinfo is None:
        raise ValueError("不正な電源予約です")
    cancel(ignore_errors=True)
    python = (REPO_ROOT / ".venv" / "bin" / "python").resolve()
    working = (REPO_ROOT / "backend").resolve()
    service = "\n".join([
        "[Unit]",
        "Description=Control Deck scheduled power action",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        f"WorkingDirectory={working}",
        f"ExecStart={_escape_exec_arg(str(python))} -m app.power.worker",
        "",
    ])
    calendar = at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    timer = "\n".join([
        "[Unit]",
        "Description=Control Deck durable power schedule",
        "",
        "[Timer]",
        f"OnCalendar={calendar}",
        "AccuracySec=1s",
        # 期限中のWeb再起動には耐える一方、PC停止中に過ぎた予約を次回起動時に実行しない。
        "Persistent=false",
        f"Unit={SERVICE}",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])
    state = {"action": action, "at": at.isoformat(), "by": username, "status": "scheduled"}
    root = unit_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / SERVICE).write_text(service, encoding="utf-8")
    (root / TIMER).write_text(timer, encoding="utf-8")
    _write_state(state)
    reload_result = _systemctl("daemon-reload")
    enable_result = _systemctl("enable", "--now", TIMER)
    if reload_result.returncode != 0 or enable_result.returncode != 0:
        cancel(ignore_errors=True)
        error = enable_result.stderr.strip() or reload_result.stderr.strip() or "systemd timer setup failed"
        raise RuntimeError(error)
    return state


def cancel(*, ignore_errors: bool = False, keep_state: bool = False) -> None:
    result = _systemctl("disable", "--now", TIMER)
    root = unit_dir()
    (root / TIMER).unlink(missing_ok=True)
    (root / SERVICE).unlink(missing_ok=True)
    if not keep_state:
        state_path().unlink(missing_ok=True)
    _systemctl("daemon-reload")
    if result.returncode != 0 and not ignore_errors and "not loaded" not in result.stderr.lower():
        raise RuntimeError(result.stderr.strip() or "timer cancellation failed")


def update_status(status: str, error: str = "") -> None:
    state = read_state()
    if state is None:
        return
    state["status"] = status
    if error:
        state["error"] = error[:300]
    _write_state(state)
