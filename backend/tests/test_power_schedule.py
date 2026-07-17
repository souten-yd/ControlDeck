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
