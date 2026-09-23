"""Surviving agent observation and explicit, owner-scoped cancellation."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from app.database import SessionLocal
from app.integrations.opencode import recovery
from app.jobs import service as jobs
from app.models import AuditLog, Job, JobControl, User
from tests.conftest import CSRF_HEADERS


@pytest.fixture
def stored(admin_client):
    ids: list[str] = []

    def create(*, foreign: bool = False, status: str = "running") -> dict:
        from app.bootstrap import create_admin
        with SessionLocal() as db:
            if foreign:
                name = "recovery-" + uuid.uuid4().hex[:10]
                owner = create_admin(db, name, "test-only-password-123").id
            else:
                owner = db.query(User).filter_by(username="admin").one().id
            job_id = "recovery" + uuid.uuid4().hex[:12]
            ids.append(job_id)
            db.add(Job(id=job_id, kind="opencode.run", title="surviving agent",
                       status=status, owner_user_id=owner))
            db.flush()
            db.add(JobControl(job_id=job_id, owner_user_id=owner, kind="opencode.run", revision=4))
            db.commit()
        return {"id": job_id, "owner_user_id": owner, "kind": "opencode.run",
                "status": "interrupted", "persisted": True}

    yield create
    with SessionLocal() as db:
        db.query(JobControl).filter(JobControl.job_id.in_(ids)).delete(synchronize_session=False)
        db.query(Job).filter(Job.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def unit_output(unit: str, state: str) -> str:
    values = {
        "running": ("loaded", "active", "running", "123"),
        "stopping": ("loaded", "deactivating", "stop-sigterm", "123"),
        "stopped": ("not-found", "inactive", "dead", "0"),
        "exited": ("loaded", "active", "exited", "0"),
        "unknown": ("error", "inactive", "dead", "0"),
    }[state]
    return "\n".join(f"{key}={value}" for key, value in zip(
        ["Id", "LoadState", "ActiveState", "SubState", "MainPID"], [unit, *values],
    ))


def test_restart_survivor_is_observed_without_stop_or_db_rewrite(admin_client, stored, monkeypatch):
    item = stored()
    assert jobs.recover_on_startup() >= 1
    unit = recovery._unit(item)
    command = AsyncMock(return_value=(0, unit_output(unit, "running")))
    monkeypatch.setattr(recovery, "_command", command)
    detail = admin_client.get(f"/api/v1/jobs/{item['id']}").json()
    listing = admin_client.get("/api/v1/jobs?kind=opencode.run").json()
    listed = next(row for row in listing if row["id"] == item["id"])
    assert detail["status"] == listed["status"] == "running"
    assert detail["phase"] == "external_running" and detail["finished_at"] is None
    assert detail["error"] == "" and detail["result"] is None
    assert all(call.args[0][0] == "show" for call in command.call_args_list)
    with SessionLocal() as db:
        row = db.get(Job, item["id"])
        assert row.status == "interrupted" and row.finished_at is not None


@pytest.mark.parametrize("state,phase", [
    ("unknown", "external_status_unknown"),
    ("stopped", "external_result_unavailable"),
    ("exited", "external_result_unavailable"),
])
def test_missing_result_and_unknown_state_are_never_success(state, phase, monkeypatch):
    item = {"id": "test", "kind": "opencode.run", "persisted": True, "status": "interrupted"}
    command = AsyncMock(return_value=(0, unit_output(recovery._unit(item), state)))
    monkeypatch.setattr(recovery, "_command", command)
    asyncio.run(recovery.reconcile([item]))
    assert item["status"] == "interrupted" and item["phase"] == phase
    assert item["error"]
    assert command.call_args.args[0][0] == "show"


def test_unavailable_systemctl_is_unknown_without_stop(monkeypatch):
    item = {"id": "test", "kind": "opencode.run", "persisted": True, "status": "interrupted"}
    command = AsyncMock(side_effect=recovery.RecoveryError("cannot query"))
    monkeypatch.setattr(recovery, "_command", command)
    asyncio.run(recovery.reconcile([item]))
    assert item["phase"] == "external_status_unknown"
    assert command.await_count == 1


@pytest.mark.parametrize("change", [
    {"id": "../other"}, {"id": "a" * 25}, {"id": "--all"},
    {"kind": "chat.completion"}, {"kind": "opencode.run.other"},
    {"status": "succeeded"}, {"status": "canceled"}, {"persisted": False},
])
def test_only_eligible_saved_jobs_are_observed(change, monkeypatch):
    # Leading dashes are valid internal unit suffixes, never CLI options.
    item = {"id": "test", "kind": "opencode.run", "persisted": True,
            "status": "interrupted", **change}
    command = AsyncMock(return_value=(0, ""))
    monkeypatch.setattr(recovery, "_command", command)
    asyncio.run(recovery.reconcile([item]))
    if change == {"id": "--all"}:
        assert command.call_args.args[0][-2:] == ["--", "cdfeature-opencode---all.service"]
    else:
        command.assert_not_awaited()
        assert "phase" not in item


def test_explicit_cancel_stops_only_owned_unit_and_records_audit(admin_client, stored, monkeypatch):
    item, other = stored(), stored()
    jobs.recover_on_startup()
    unit = recovery._unit(item)
    state = "running"
    calls: list[list[str]] = []

    async def command(arguments: list[str], **kwargs):
        nonlocal state
        calls.append(arguments)
        if arguments[0] == "stop":
            assert arguments == ["stop", "--", unit]
            state = "stopped"
            return 0, ""
        return 0, unit_output(unit, state)

    monkeypatch.setattr(recovery, "_command", command)
    response = admin_client.post(f"/api/v1/jobs/{item['id']}/cancel", headers=CSRF_HEADERS)
    assert response.status_code == 200, response.text
    assert len(calls) == 3
    with SessionLocal() as db:
        assert db.get(Job, item["id"]).status == "canceled"
        assert db.get(JobControl, item["id"]).revision == 5
        assert db.get(Job, other["id"]).status == "interrupted"
        assert db.query(AuditLog).filter_by(action="job.cancel", resource_id=item["id"]).count() == 1
    command_count = len(calls)
    assert admin_client.post(f"/api/v1/jobs/{item['id']}/cancel", headers=CSRF_HEADERS).status_code == 409
    assert len(calls) == command_count


@pytest.mark.parametrize("state,stop_code", [("unknown", 0), ("running", 1), ("running", 0)])
def test_unconfirmed_stop_is_not_recorded_as_canceled(admin_client, stored, monkeypatch, state, stop_code):
    item = stored()
    jobs.recover_on_startup()
    unit = recovery._unit(item)
    calls = []

    async def command(arguments, **kwargs):
        calls.append(arguments)
        return (stop_code, "") if arguments[0] == "stop" else (0, unit_output(unit, state))

    monkeypatch.setattr(recovery, "_command", command)
    response = admin_client.post(f"/api/v1/jobs/{item['id']}/cancel", headers=CSRF_HEADERS)
    assert response.status_code == 503
    with SessionLocal() as db:
        assert db.get(Job, item["id"]).status == "interrupted"
    if state == "unknown":
        assert all(arguments[0] == "show" for arguments in calls)


def test_foreign_job_is_rejected_before_any_systemctl(admin_client, stored, monkeypatch):
    item = stored(foreign=True)
    jobs.recover_on_startup()
    command = AsyncMock()
    monkeypatch.setattr(recovery, "_command", command)
    assert admin_client.get(f"/api/v1/jobs/{item['id']}").status_code == 404
    assert admin_client.post(f"/api/v1/jobs/{item['id']}/cancel", headers=CSRF_HEADERS).status_code == 404
    assert not any(row["id"] == item["id"] for row in admin_client.get("/api/v1/jobs?kind=opencode.run").json())
    command.assert_not_awaited()


def test_stream_observes_unit_exit_without_db_revision(admin_client, stored, monkeypatch):
    item = stored()
    jobs.recover_on_startup()
    unit = recovery._unit(item)
    state = "running"

    async def command(arguments, **kwargs):
        return 0, unit_output(unit, state)

    monkeypatch.setattr(recovery, "_command", command)
    with admin_client.websocket_connect("/api/v1/jobs/stream?kind=opencode.run") as websocket:
        snapshot = websocket.receive_json()
        row = next(row for row in snapshot["jobs"] if row["id"] == item["id"])
        assert row["status"] == "running"
        state = "stopped"
        update = websocket.receive_json()
        assert update["type"] == "update" and update["job"]["id"] == item["id"]
        assert update["job"]["revision"] == row["revision"]
        assert update["job"]["phase"] == "external_result_unavailable"


def test_read_only_role_cannot_stop_survivor(admin_client, stored, monkeypatch):
    from app.bootstrap import create_admin
    from app.models import Role
    item = stored()
    jobs.recover_on_startup()
    name = "recovery-reader-" + uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        role = Role(name=name, permissions_json='["workflows.run"]')
        db.add(role)
        db.flush()
        user = create_admin(db, name, "test-only-password-123")
        user.role_id = role.id
        db.get(Job, item["id"]).owner_user_id = user.id
        db.commit()
    command = AsyncMock()
    monkeypatch.setattr(recovery, "_command", command)
    try:
        response = admin_client.post("/api/v1/auth/login", json={"username": name, "password": "test-only-password-123"}, headers=CSRF_HEADERS)
        assert response.status_code == 200
        assert admin_client.post(f"/api/v1/jobs/{item['id']}/cancel", headers=CSRF_HEADERS).status_code == 403
        command.assert_not_awaited()
    finally:
        admin_client.post("/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"}, headers=CSRF_HEADERS)


def test_query_timeout_reaps_only_systemctl_client(monkeypatch):
    class Process:
        returncode = None
        killed = False

        async def communicate(self):
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(recovery.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(recovery.shutil, "which", lambda _: "/usr/bin/systemctl")
    with pytest.raises(recovery.RecoveryError):
        asyncio.run(recovery._command(["show", "--", "cdfeature-opencode-test.service"], timeout=.01))
    assert process.killed
    assert spawn.await_count == 1
    assert spawn.call_args.args == ("/usr/bin/systemctl", "--user", "show", "--", "cdfeature-opencode-test.service")
