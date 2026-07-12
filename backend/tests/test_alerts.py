import asyncio

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
