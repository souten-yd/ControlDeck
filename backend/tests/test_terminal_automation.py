from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import get_config
from app.database import SessionLocal
from app.models import AuditLog, TerminalAutomationSchedule, TerminalCommandRun
from app.terminals import automation_systemd
from app.terminals.automation_worker import execute_run

CSRF = {"X-Requested-With": "ControlDeck"}


def _root() -> str:
    return str(Path(get_config().files.allowed_roots[0]).resolve())


def _create_snippet(admin_client, *, name: str = "Codex night task") -> dict:
    response = admin_client.post("/api/v1/terminal-automation/snippets", headers=CSRF, json={
        "name": name,
        "description": "Run after quota refresh",
        "content": "printf 'AUTOMATION_OK_%s' '{{task}}'",
        "variables": [{"name": "task", "label": "Task", "default": "default", "required": True}],
        "tags": ["codex", "night"],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_snippet_preview_and_detached_worker_keep_command_out_of_metadata(admin_client, monkeypatch):
    launched: list[int] = []
    monkeypatch.setattr(
        "app.terminals.automation_systemd.launch_run", lambda run: launched.append(run.id),
    )
    snippet = _create_snippet(admin_client)
    body = {
        "snippet_ids": [snippet["id"]], "parameters": {"task": "MIDNIGHT"},
        "mode": "detached", "working_directory": _root(), "timeout_seconds": 30,
    }
    preview = admin_client.post("/api/v1/terminal-automation/preview", headers=CSRF, json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["command"] == "printf 'AUTOMATION_OK_%s' 'MIDNIGHT'"
    assert preview.json()["condition"] == {"ready": True, "reason": "Ready", "session": None}

    started = admin_client.post("/api/v1/terminal-automation/runs", headers=CSRF, json=body)
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    assert launched == [run_id]
    with SessionLocal() as db:
        row = db.get(TerminalCommandRun, run_id)
        assert row is not None
        assert "MIDNIGHT" not in row.command_snapshot_encrypted
        audits = db.execute(select(AuditLog).where(AuditLog.resource_id == str(run_id))).scalars().all()
        assert all("MIDNIGHT" not in entry.metadata_json for entry in audits)

    assert execute_run(run_id) == 0
    runs = admin_client.get("/api/v1/terminal-automation/runs?limit=5")
    run = next(item for item in runs.json()["runs"] if item["id"] == run_id)
    assert run["status"] == "SUCCEEDED"
    assert run["exit_code"] == 0
    output = admin_client.get(f"/api/v1/terminal-automation/runs/{run_id}/output").json()
    assert output["available"] is True
    assert "AUTOMATION_OK_MIDNIGHT" in output["output"]


def test_terminal_condition_mismatch_is_skipped_without_injection(admin_client, monkeypatch):
    snippet = _create_snippet(admin_client, name="Safe session prompt")
    monkeypatch.setattr("app.terminals.automation_systemd.launch_run", lambda _run: None)
    monkeypatch.setattr("app.terminals.automation.manager.list_sessions", lambda: [{
        "id": "0123abcd", "alive": True, "program": "bash", "workload": "idle",
    }])
    injected: list[str] = []
    monkeypatch.setattr(
        "app.terminals.automation_worker.manager.inject_input",
        lambda _sid, text, submit=True: injected.append(text),
    )
    body = {
        "snippet_ids": [snippet["id"]], "parameters": {"task": "PROMPT"},
        "mode": "terminal", "target_session_id": "0123abcd",
        "working_directory": _root(), "condition_type": "program_equals",
        "condition_value": "codex", "timeout_seconds": 30,
    }
    preview = admin_client.post("/api/v1/terminal-automation/preview", headers=CSRF, json=body)
    assert preview.status_code == 200
    assert preview.json()["condition"]["ready"] is False
    started = admin_client.post("/api/v1/terminal-automation/runs", headers=CSRF, json=body)
    assert started.status_code == 202
    run_id = started.json()["id"]
    assert execute_run(run_id) == 0
    assert injected == []
    with SessionLocal() as db:
        run = db.get(TerminalCommandRun, run_id)
        assert run is not None and run.status == "SKIPPED"
        assert "一致しません" in run.error


def test_schedule_crud_uses_durable_timer_and_protects_referenced_snippet(admin_client, monkeypatch):
    installed: list[tuple[int, str]] = []
    disabled: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        "app.terminals.automation_systemd.install_schedule",
        lambda row: installed.append((row.id, row.recurrence)),
    )
    monkeypatch.setattr(
        "app.terminals.automation_systemd.disable_schedule",
        lambda schedule_id, remove_files: disabled.append((schedule_id, remove_files)),
    )
    snippet = _create_snippet(admin_client, name="Biweekly maintenance")
    next_run = datetime.now(timezone.utc) + timedelta(hours=2)
    response = admin_client.post("/api/v1/terminal-automation/schedules", headers=CSRF, json={
        "name": "After Codex quota refresh", "snippet_ids": [snippet["id"]],
        "parameters": {"task": "scheduled"}, "mode": "detached",
        "working_directory": _root(), "recurrence": "biweekly",
        "next_run_at": next_run.isoformat(), "timezone": "Asia/Tokyo",
        "run_if_missed": True, "timeout_seconds": 120,
    })
    assert response.status_code == 201, response.text
    schedule = response.json()
    assert installed == [(schedule["id"], "biweekly")]
    listing = admin_client.get("/api/v1/terminal-automation/schedules").json()["schedules"]
    saved = next(item for item in listing if item["id"] == schedule["id"])
    assert saved["parameters"] == {"task": "scheduled"}

    blocked = admin_client.delete(
        f"/api/v1/terminal-automation/snippets/{snippet['id']}", headers=CSRF,
    )
    assert blocked.status_code == 409
    paused = admin_client.patch(
        f"/api/v1/terminal-automation/schedules/{schedule['id']}", headers=CSRF,
        json={"enabled": False},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED"
    assert disabled[-1] == (schedule["id"], False)
    assert admin_client.delete(
        f"/api/v1/terminal-automation/schedules/{schedule['id']}", headers=CSRF,
    ).status_code == 200
    assert disabled[-1] == (schedule["id"], True)
    assert admin_client.delete(
        f"/api/v1/terminal-automation/snippets/{snippet['id']}", headers=CSRF,
    ).status_code == 200


def test_schedule_rejects_blind_interactive_session_injection(admin_client):
    snippet = _create_snippet(admin_client, name="Unsafe scheduled prompt")
    response = admin_client.post("/api/v1/terminal-automation/schedules", headers=CSRF, json={
        "name": "Blind terminal injection", "snippet_ids": [snippet["id"]],
        "parameters": {"task": "scheduled"}, "mode": "terminal",
        "target_session_id": "0123abcd", "working_directory": _root(),
        "condition_type": "always", "recurrence": "once",
        "next_run_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "timezone": "Asia/Tokyo", "run_if_missed": False, "timeout_seconds": 120,
    })
    assert response.status_code == 422
    assert "無条件送信" in response.json()["detail"]


def test_schedule_unit_contains_only_worker_reference_and_absolute_utc_time(tmp_path, monkeypatch):
    monkeypatch.setattr(automation_systemd, "_unit_dir", lambda: tmp_path.resolve())
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        automation_systemd, "_systemctl",
        lambda *args: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    row = TerminalAutomationSchedule(
        id=42, name="Night", snippet_ids_json="[1]", mode="detached",
        working_directory="/tmp", condition_type="always", condition_value="",
        recurrence="biweekly", next_run_at=datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        timezone="Asia/Tokyo", run_if_missed=True, timeout_seconds=60,
    )
    automation_systemd.install_schedule(row)
    service = (tmp_path / "control-deck-terminal-automation-schedule-42.service").read_text()
    timer = (tmp_path / "control-deck-terminal-automation-schedule-42.timer").read_text()
    assert "automation_worker --schedule-id 42" in service
    assert "Night" not in service
    assert "OnCalendar=2030-01-02 03:04:05 UTC" in timer
    assert "Persistent=true" in timer
    assert calls[-2] == ("enable", "--now", "control-deck-terminal-automation-schedule-42.timer")
    assert calls[-1] == ("is-active", "--quiet", "control-deck-terminal-automation-schedule-42.timer")


def test_worker_python_keeps_virtualenv_entrypoint_symlink():
    python = automation_systemd._venv_python()
    assert str(python).endswith("/.venv/bin/python")
    assert not str(python).startswith("/usr/bin/")
