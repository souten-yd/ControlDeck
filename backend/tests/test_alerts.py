import asyncio
import json
import logging

from tests.conftest import CSRF_HEADERS


def test_channel_crud_and_masking(admin_client):
    r = admin_client.post(
        "/api/v1/alert-channels",
        json={"name": "test-discord", "channel_type": "discord", "url": "https://discord.com/api/webhooks/123/abcdefghijklmnop"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "…" in body["url_preview"]  # URL はマスクされる
    assert "abcdefghijklmnop" not in body["url_preview"]
    cid = body["id"]
    assert any(c["id"] == cid for c in admin_client.get("/api/v1/alert-channels").json())
    assert admin_client.delete(f"/api/v1/alert-channels/{cid}", headers=CSRF_HEADERS).status_code == 200


def test_email_channel_is_encrypted_masked_and_validated(admin_client):
    from app.database import SessionLocal
    from app.models import NotificationChannel
    from app.security.crypto import decrypt_text

    password = "smtp-secret-value"
    response = admin_client.post(
        "/api/v1/alert-channels",
        json={
            "name": "operations email",
            "channel_type": "email",
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "smtp_username": "mailer@example.com",
            "smtp_password": password,
            "from_address": "deck@example.com",
            "to_addresses": ["admin@example.com", "oncall@example.com"],
        },
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["channel_type"] == "email"
    assert body["url_preview"] == "d***@example.com → 2件"
    assert password not in response.text

    db = SessionLocal()
    try:
        channel = db.get(NotificationChannel, body["id"])
        assert channel is not None
        assert password not in channel.url_encrypted
        settings = json.loads(decrypt_text(channel.url_encrypted))
        assert settings["password"] == password
        assert settings["to_addresses"] == ["admin@example.com", "oncall@example.com"]
    finally:
        db.close()

    invalid = admin_client.post(
        "/api/v1/alert-channels",
        json={
            "name": "invalid email",
            "channel_type": "email",
            "smtp_host": "smtp.example.com",
            "smtp_password": "must-not-be-reflected",
            "from_address": "bad\n@example.com",
            "to_addresses": ["admin@example.com"],
        },
        headers=CSRF_HEADERS,
    )
    assert invalid.status_code == 422
    assert "must-not-be-reflected" not in invalid.text
    assert admin_client.delete(f"/api/v1/alert-channels/{body['id']}", headers=CSRF_HEADERS).status_code == 200


def test_email_notification_uses_starttls_login_and_message(monkeypatch):
    from app.alerts import notify

    calls = []

    class SMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context):
            calls.append(("starttls", context is not None))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(("message", message["Subject"], message["From"], message["To"], message.get_content().strip()))

    monkeypatch.setattr(notify.smtplib, "SMTP", SMTP)
    destination = json.dumps(
        {
            "host": "smtp.example.com",
            "port": 587,
            "security": "starttls",
            "username": "mailer",
            "password": "secret",
            "from_address": "deck@example.com",
            "to_addresses": ["admin@example.com"],
        }
    )
    assert asyncio.run(notify.send_notification("email", destination, "Alert", "CPU is high")) is True
    assert calls[0] == ("connect", "smtp.example.com", 587, 15)
    assert ("starttls", True) in calls
    assert ("login", "mailer", "secret") in calls
    assert ("message", "Alert", "deck@example.com", "admin@example.com", "CPU is high") in calls


def test_notification_failure_log_does_not_include_destination_secret(monkeypatch, caplog):
    from app.alerts import notify

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json):
            raise notify.httpx.RequestError("failed https://example.test/hook/super-secret-token")

    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda **_kwargs: Client())
    caplog.set_level(logging.WARNING, logger="control_deck.alerts")
    assert asyncio.run(notify.send_notification("webhook", "https://example.test/hook/super-secret-token", "x", "y")) is False
    assert "super-secret-token" not in caplog.text


def test_dispatch_reports_external_delivery_failure(client, monkeypatch):
    from sqlalchemy import delete

    from app.alerts import engine
    from app.database import SessionLocal
    from app.models import AlertEvent, AlertRule, NotificationChannel
    from app.security.crypto import encrypt_text

    db = SessionLocal()
    try:
        db.execute(delete(AlertEvent))
        channel = NotificationChannel(
            name="failure", channel_type="webhook",
            url_encrypted=encrypt_text("https://example.test/private-token"), enabled=True,
        )
        db.add(channel)
        db.flush()
        rule = AlertRule(
            name="delivery", metric="cpu_percent", operator="gt", threshold=50,
            duration_seconds=0, cooldown_seconds=0, channel_ids_json=json.dumps([channel.id]),
        )
        db.add(rule)
        db.commit()
        rule_id = rule.id
        channel_id = channel.id
    finally:
        db.close()

    async def failed(*_args):
        return False

    monkeypatch.setattr("app.alerts.notify.send_notification", failed)
    monkeypatch.setattr("app.monitoring.collector.collector.latest", {"cpu": {"percent": 99}, "memory": {}, "gpu": None})
    engine._breach_since.clear()
    asyncio.run(engine.evaluate_once())

    db = SessionLocal()
    try:
        event = db.query(AlertEvent).filter(AlertEvent.rule_id == rule_id).one()
        assert event.notified is False
        db.delete(event)
        db.delete(db.get(AlertRule, rule_id))
        db.delete(db.get(NotificationChannel, channel_id))
        db.commit()
    finally:
        db.close()


def test_rule_crud(admin_client):
    r = admin_client.post(
        "/api/v1/alert-rules",
        json={"name": "CPU 高負荷", "metric": "cpu_percent", "operator": "gt", "threshold": 90, "duration_seconds": 300},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["metric_label"] == "CPU 使用率"
    r = admin_client.patch(
        f"/api/v1/alert-rules/{rid}",
        json={"name": "CPU 高負荷", "metric": "cpu_percent", "operator": "gt", "threshold": 95, "duration_seconds": 300},
        headers=CSRF_HEADERS,
    )
    assert r.json()["threshold"] == 95
    assert admin_client.delete(f"/api/v1/alert-rules/{rid}", headers=CSRF_HEADERS).status_code == 200


def test_app_down_rule_requires_app(admin_client):
    r = admin_client.post(
        "/api/v1/alert-rules",
        json={"name": "x", "metric": "app_down"},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 422


def test_application_alert_rules_require_registered_target(admin_client):
    from app.database import SessionLocal
    from app.models import ManagedApplication

    db = SessionLocal()
    try:
        app = ManagedApplication(name="alert target", application_type="url_shortcut", url="https://example.test")
        db.add(app)
        db.commit()
        app_id = app.id
    finally:
        db.close()

    rule_ids = []
    try:
        for metric, label, threshold in (
            ("app_down", "アプリ停止", 90),
            ("app_health_failed", "ヘルスチェック失敗", 90),
            ("app_restart_loop", "再起動回数", 3),
            ("app_log_error", "ログ ERROR", 1),
        ):
            response = admin_client.post(
                "/api/v1/alert-rules",
                json={"name": metric, "metric": metric, "app_id": app_id, "threshold": threshold},
                headers=CSRF_HEADERS,
            )
            assert response.status_code == 201, response.text
            assert response.json()["metric_label"] == label
            rule_ids.append(response.json()["id"])
        missing = admin_client.post(
            "/api/v1/alert-rules",
            json={"name": "missing", "metric": "app_restart_loop", "app_id": 999999},
            headers=CSRF_HEADERS,
        )
        assert missing.status_code == 422
        invalid_threshold = admin_client.post(
            "/api/v1/alert-rules",
            json={"name": "invalid", "metric": "app_restart_loop", "app_id": app_id, "threshold": 0},
            headers=CSRF_HEADERS,
        )
        assert invalid_threshold.status_code == 422
    finally:
        for rule_id in rule_ids:
            admin_client.delete(f"/api/v1/alert-rules/{rule_id}", headers=CSRF_HEADERS)
        db = SessionLocal()
        try:
            db.delete(db.get(ManagedApplication, app_id))
            db.commit()
        finally:
            db.close()


def test_log_error_rule_tracks_only_new_lines_and_survives_restart(admin_client, monkeypatch):
    from sqlalchemy import delete, select

    from app.alerts import engine
    from app.database import SessionLocal
    from app.logs import service as logs
    from app.models import AlertEvent, AlertLogCursor, AlertRule, ManagedApplication

    db = SessionLocal()
    try:
        db.execute(delete(AlertEvent))
        app = ManagedApplication(name="log alert target", application_type="url_shortcut", url="https://example.test")
        db.add(app)
        db.commit()
        app_id = app.id
    finally:
        db.close()

    stdout = logs.log_path(app_id, "stdout")
    stderr = logs.log_path(app_id, "stderr")
    stdout.write_text("ERROR historical-secret-must-not-alert\n", encoding="utf-8")
    response = admin_client.post(
        "/api/v1/alert-rules",
        json={
            "name": "new log errors", "metric": "app_log_error", "app_id": app_id,
            "duration_seconds": 999, "cooldown_seconds": 0,
        },
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 201, response.text
    assert response.json()["duration_seconds"] == 0
    rule_id = response.json()["id"]

    db = SessionLocal()
    try:
        cursors = db.scalars(select(AlertLogCursor).where(AlertLogCursor.rule_id == rule_id)).all()
        assert {cursor.stream for cursor in cursors} == {"stdout", "stderr"}
        assert next(cursor for cursor in cursors if cursor.stream == "stdout").offset == stdout.stat().st_size
    finally:
        db.close()

    monkeypatch.setattr("app.monitoring.collector.collector.latest", {})
    engine._breach_since.clear()
    asyncio.run(engine.evaluate_once())
    db = SessionLocal()
    try:
        assert db.scalar(select(AlertEvent).where(AlertEvent.rule_id == rule_id)) is None
    finally:
        db.close()

    with stderr.open("a", encoding="utf-8") as handle:
        handle.write("INFO ready\nERROR private-runtime-value\nCRITICAL another-private-value\n")
    asyncio.run(engine.evaluate_once())
    db = SessionLocal()
    try:
        event = db.scalar(select(AlertEvent).where(AlertEvent.rule_id == rule_id))
        assert event is not None and event.status == "active" and event.value == 2
        assert event.message == "ログ ERROR = 2.0"
        assert "private-runtime-value" not in event.message
        persisted_offset = db.scalar(
            select(AlertLogCursor.offset).where(
                AlertLogCursor.rule_id == rule_id, AlertLogCursor.stream == "stderr",
            )
        )
        assert persisted_offset == stderr.stat().st_size
    finally:
        db.close()

    # プロセス内状態を失っても永続offsetを正として同じ行を再通知せず、静穏時に解消する。
    engine._breach_since.clear()
    engine._active_event.clear()
    asyncio.run(engine.evaluate_once())
    db = SessionLocal()
    try:
        events = db.scalars(select(AlertEvent).where(AlertEvent.rule_id == rule_id)).all()
        assert len(events) == 1 and events[0].status == "resolved"
    finally:
        db.close()

    # ローテーションでinodeとサイズが変わっても新ファイル先頭を読む。
    stderr.unlink()
    stderr.write_text("ERROR after-rotation-private-value\n", encoding="utf-8")
    asyncio.run(engine.evaluate_once())
    db = SessionLocal()
    try:
        events = db.scalars(
            select(AlertEvent).where(AlertEvent.rule_id == rule_id).order_by(AlertEvent.id)
        ).all()
        assert len(events) == 2 and events[-1].value == 1
        assert all("private-value" not in event.message for event in events)
    finally:
        db.close()

    assert admin_client.delete(f"/api/v1/alert-rules/{rule_id}", headers=CSRF_HEADERS).status_code == 200
    db = SessionLocal()
    try:
        assert db.scalar(select(AlertLogCursor).where(AlertLogCursor.rule_id == rule_id)) is None
        db.delete(db.get(ManagedApplication, app_id))
        db.commit()
    finally:
        db.close()


def test_application_alert_metric_values(monkeypatch):
    from types import SimpleNamespace

    from app.alerts.engine import _metric_value
    from app.models import AlertRule

    app = object()

    class DB:
        def get(self, _model, app_id):
            return app if app_id == 7 else None

    runtimes = {
        "app_down": SimpleNamespace(status="FAILED", health=None, restart_count=2),
        "app_health_failed": SimpleNamespace(
            status="DEGRADED", health=SimpleNamespace(ok=False), restart_count=2,
        ),
        "app_restart_loop": SimpleNamespace(status="RUNNING", health=None, restart_count=6),
    }
    monkeypatch.setattr(
        "app.applications.service.runtime_info",
        lambda _app, include_health=True: runtimes[current_metric],
    )
    for current_metric, expected in (("app_down", 1.0), ("app_health_failed", 1.0), ("app_restart_loop", 6.0)):
        rule = AlertRule(metric=current_metric, app_id=7)
        assert _metric_value(current_metric, {"cpu": {}}, rule, DB()) == expected


def test_legacy_app_down_threshold_still_fires(client, monkeypatch):
    from sqlalchemy import delete, select

    from app.alerts import engine
    from app.database import SessionLocal
    from app.models import AlertEvent, AlertRule, ManagedApplication

    db = SessionLocal()
    try:
        db.execute(delete(AlertEvent))
        app = ManagedApplication(name="stopped target", application_type="url_shortcut", url="https://example.test")
        db.add(app)
        db.flush()
        rule = AlertRule(
            name="legacy stopped", metric="app_down", app_id=app.id,
            operator="gt", threshold=90, duration_seconds=0, cooldown_seconds=0,
        )
        db.add(rule)
        db.commit()
        app_id, rule_id = app.id, rule.id
    finally:
        db.close()

    monkeypatch.setattr(
        "app.applications.service.runtime_info",
        lambda _app, include_health=True: __import__("types").SimpleNamespace(
            status="STOPPED", health=None, restart_count=0,
        ),
    )
    monkeypatch.setattr("app.monitoring.collector.collector.latest", {"cpu": {}})
    engine._breach_since.clear()
    asyncio.run(engine.evaluate_once())

    db = SessionLocal()
    try:
        event = db.scalar(select(AlertEvent).where(AlertEvent.rule_id == rule_id))
        assert event is not None and event.status == "active"
        db.delete(event)
        db.delete(db.get(AlertRule, rule_id))
        db.delete(db.get(ManagedApplication, app_id))
        db.commit()
    finally:
        db.close()


def test_alert_evaluation_fires_and_resolves(client, monkeypatch):
    import time as _time

    from app.alerts import engine
    from app.database import SessionLocal
    from app.models import AlertEvent, AlertRule
    from sqlalchemy import delete, select

    db = SessionLocal()
    try:
        db.execute(delete(AlertEvent))
        rule = AlertRule(name="CPU テスト", metric="cpu_percent", operator="gt", threshold=50, duration_seconds=0, cooldown_seconds=0, channel_ids_json="[]")
        db.add(rule)
        db.commit()
        rule_id = rule.id
    finally:
        db.close()

    # 収集スナップショットをモック（CPU 99%）
    from app.monitoring.collector import collector

    monkeypatch.setattr(collector, "latest", {"cpu": {"percent": 99.0}, "memory": {"percent": 10}, "gpu": None})
    engine._breach_since.clear()
    engine._active_event.clear()
    asyncio.run(engine.evaluate_once())

    db = SessionLocal()
    try:
        events = db.execute(select(AlertEvent).where(AlertEvent.rule_id == rule_id)).scalars().all()
        assert len(events) == 1
        assert events[0].status == "active"
    finally:
        db.close()

    # CPU が下がると解消
    monkeypatch.setattr(collector, "latest", {"cpu": {"percent": 5.0}, "memory": {"percent": 10}, "gpu": None})
    asyncio.run(engine.evaluate_once())
    db = SessionLocal()
    try:
        event = db.execute(select(AlertEvent).where(AlertEvent.rule_id == rule_id)).scalar_one()
        assert event.status == "resolved"
        assert event.resolved_at is not None
        db.delete(event)
        db.delete(db.get(AlertRule, rule_id))
        db.commit()
    finally:
        db.close()


def test_operator_and_metric_helpers():
    from app.alerts.engine import OPERATORS, _metric_value
    from app.models import AlertRule

    assert OPERATORS["gt"](91, 90) is True
    assert OPERATORS["lte"](90, 90) is True
    snap = {"gpu": {"vram_used_bytes": 8_000_000_000, "vram_total_bytes": 16_000_000_000, "temperature_c": 80}}
    rule = AlertRule(metric="vram_percent", operator="gt", threshold=50)
    assert _metric_value("vram_percent", snap, rule, None) == 50.0
    assert _metric_value("gpu_temp_c", snap, rule, None) == 80


def _ensure_viewer():
    from app.database import SessionLocal
    from app.models import Role, User
    from app.security.passwords import hash_password
    from sqlalchemy import select

    db = SessionLocal()
    try:
        if not db.execute(select(User).where(User.username == "ro")).scalar_one_or_none():
            role = db.execute(select(Role).where(Role.name == "viewer")).scalar_one()
            db.add(User(username="ro", password_hash=hash_password("viewer-pass-123"), role_id=role.id))
            db.commit()
    finally:
        db.close()


def test_viewer_can_read_but_not_edit_alerts(client):
    _ensure_viewer()
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"username": "ro", "password": "viewer-pass-123"}, headers=CSRF_HEADERS)
    assert r.status_code == 200
    assert client.get("/api/v1/alert-rules").status_code == 200  # system.view で閲覧可
    r = client.post("/api/v1/alert-rules", json={"name": "x", "metric": "cpu_percent"}, headers=CSRF_HEADERS)
    assert r.status_code == 403  # settings.manage が必要
    client.cookies.clear()
