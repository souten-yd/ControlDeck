from datetime import datetime, timezone
from types import SimpleNamespace

from tests.conftest import CSRF_HEADERS


def test_durable_power_timer_units_do_not_run_missed_action(tmp_path, monkeypatch):
    from app.power import scheduler

    units = tmp_path / "units"
    state = tmp_path / "state.json"
    monkeypatch.setattr(scheduler, "unit_dir", lambda: units)
    monkeypatch.setattr(scheduler, "state_path", lambda: state)
    calls = []

    def fake_systemctl(*args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(scheduler, "_systemctl", fake_systemctl)
    at = datetime(2026, 7, 16, 3, 30, tzinfo=timezone.utc)
    result = scheduler.install("reboot", at, "admin")

    assert result["status"] == "scheduled"
    timer = (units / scheduler.TIMER).read_text()
    assert "Persistent=false" in timer
    assert "Persistent=true" not in timer
    service = (units / scheduler.SERVICE).read_text()
    assert "app.power.worker" in service
    assert "/bin/sh" not in service
    assert scheduler.read_state()["action"] == "reboot"
    assert ("enable", "--now", scheduler.TIMER) in calls

    scheduler.cancel()
    assert not state.exists()
    assert not (units / scheduler.TIMER).exists()


def test_power_schedule_api_uses_persistent_backend(admin_client, monkeypatch):
    from app.power import router

    expected = {
        "action": "shutdown", "at": "2026-07-16T00:00:00+00:00",
        "by": "admin", "status": "scheduled",
    }
    monkeypatch.setattr(router.scheduler, "install", lambda action, at, username: {**expected, "action": action, "by": username})
    monkeypatch.setattr(router.scheduler, "read_state", lambda: expected)
    cancelled = []
    monkeypatch.setattr(router.scheduler, "cancel", lambda: cancelled.append(True))

    r = admin_client.post(
        "/api/v1/system/power/schedule",
        json={"action": "shutdown", "delay_minutes": 30},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"
    assert admin_client.get("/api/v1/system/power/schedule").json() == expected
    assert admin_client.delete("/api/v1/system/power/schedule", headers=CSRF_HEADERS).status_code == 200
    assert cancelled == [True]


def test_platform_reload_is_scheduled_as_user_systemd_unit(admin_client, monkeypatch):
    from app.power import router

    calls = []
    monkeypatch.setattr(router.subprocess, "run", lambda argv, **kwargs: (
        calls.append((argv, kwargs)) or SimpleNamespace(returncode=0, stdout="", stderr="")
    ))
    response = admin_client.post("/api/v1/system/platform/reload", headers=CSRF_HEADERS)
    assert response.status_code == 202, response.text
    argv, kwargs = calls[0]
    assert argv[:2] == ["systemd-run", "--user"]
    assert argv[-4:] == ["/usr/bin/systemctl", "--user", "restart", "control-deck-web.service"]
    assert kwargs["timeout"] == 10
    assert "/bin/sh" not in argv


def test_power_worker_records_failure(monkeypatch):
    from app.power import worker

    states = []
    audits = []
    monkeypatch.setattr(worker.scheduler, "read_state", lambda: {
        "action": "reboot", "at": "2026-07-16T00:00:00+00:00", "by": "admin",
    })
    monkeypatch.setattr(worker.scheduler, "update_status", lambda status, error="": states.append((status, error)))
    cleaned = []
    monkeypatch.setattr(worker.scheduler, "cancel", lambda **kwargs: cleaned.append(kwargs))
    monkeypatch.setattr(worker, "_execute", lambda action: (False, "denied"))
    monkeypatch.setattr(worker.audit, "record", lambda db, action, **kwargs: audits.append((action, kwargs.get("result"))))

    assert worker.main() == 1
    assert states[-1] == ("failed", "denied")
    assert ("power.reboot", "failure") in audits
    assert cleaned == [{"ignore_errors": True, "keep_state": True}]


def test_immediate_power_uses_fixed_force_argv(monkeypatch):
    from app.power import router

    calls = []
    monkeypatch.setattr(router.subprocess, "run", lambda argv, **kwargs: (
        calls.append((argv, kwargs)) or SimpleNamespace(returncode=0, stdout="", stderr="")
    ))
    assert router._execute("reboot", "graceful") == (True, "")
    assert router._execute("shutdown", "immediate") == (True, "")
    assert calls[0][0] == ["systemctl", "reboot"]
    assert calls[1][0] == ["systemctl", "--force", "poweroff"]
    assert all(call[1]["timeout"] == 15 for call in calls)


def test_power_api_without_body_remains_graceful(admin_client, monkeypatch):
    from app.power import router

    calls = []
    monkeypatch.setattr(router, "_execute", lambda action, mode="graceful": (calls.append((action, mode)) or (True, "")))
    response = admin_client.post("/api/v1/system/reboot", headers=CSRF_HEADERS)
    assert response.status_code == 200
    assert calls == [("reboot", "graceful")]


def test_power_safety_returns_counts_without_session_details(admin_client, monkeypatch):
    from app.power import router
    from app.remote_desktop import activity
    from app.terminals.router import streams

    monkeypatch.setattr(streams, "stream_count", lambda: 3)
    monkeypatch.setattr(activity, "count", lambda: 2)
    monkeypatch.setattr(router.application_service, "runtime_info", lambda item, **kwargs: SimpleNamespace(status="STOPPED"))
    response = admin_client.get("/api/v1/system/power/safety")
    assert response.status_code == 200
    body = response.json()
    assert body["connected_terminals"] == 3
    assert body["connected_remote_desktops"] == 2
    assert isinstance(body["running_apps"], int) and isinstance(body["running_workflows"], int)
    assert set(body) == {
        "running_apps", "running_workflows", "connected_terminals", "connected_remote_desktops",
        "totp_required", "totp_enabled",
    }
    assert "sessions" not in response.text and "connection_id" not in response.text


def test_power_totp_reauthentication_blocks_invalid_code(admin_client, monkeypatch):
    import pyotp
    from sqlalchemy import select

    from app.auth import totp
    from app.database import SessionLocal
    from app.models import User
    from app.power import router

    secret = totp.generate_secret()
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
        previous = (user.totp_enabled, user.totp_secret_encrypted, user.recovery_codes_encrypted)
        user.totp_enabled = True
        totp.store_secret(user, secret)
        db.commit()
    monkeypatch.setattr(router, "get_config", lambda: SimpleNamespace(
        security=SimpleNamespace(require_totp_for_power=True)
    ))
    calls = []
    monkeypatch.setattr(router, "_execute", lambda action, mode="graceful": (calls.append((action, mode)) or (True, "")))
    try:
        denied = admin_client.post(
            "/api/v1/system/reboot", json={"mode": "immediate", "totp_code": "000000"}, headers=CSRF_HEADERS,
        )
        assert denied.status_code == 403 and calls == []
        allowed = admin_client.post(
            "/api/v1/system/reboot",
            json={"mode": "immediate", "totp_code": pyotp.TOTP(secret).now()}, headers=CSRF_HEADERS,
        )
        assert allowed.status_code == 200 and calls == [("reboot", "immediate")]
    finally:
        with SessionLocal() as db:
            user = db.execute(select(User).where(User.username == "admin")).scalar_one()
            user.totp_enabled, user.totp_secret_encrypted, user.recovery_codes_encrypted = previous
            db.commit()
