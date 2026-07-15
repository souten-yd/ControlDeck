import gzip
from datetime import datetime, timedelta, timezone

from tests.conftest import CSRF_HEADERS


def test_log_rotation_copytruncate(tmp_path):
    from app.maintenance.service import rotate_log_file

    log = tmp_path / "stdout.log"
    log.write_bytes(b"A" * 1000)

    # サイズ以下ならローテーションしない
    assert rotate_log_file(log, max_bytes=2000, generations=3) is False

    # 1 回目: .1.gz へ退避 + 元ファイル truncate
    assert rotate_log_file(log, max_bytes=500, generations=3) is True
    assert log.stat().st_size == 0
    gz1 = tmp_path / "stdout.log.1.gz"
    assert gzip.open(gz1, "rb").read() == b"A" * 1000

    # 2 回目: 世代がシフトする
    log.write_bytes(b"B" * 1000)
    assert rotate_log_file(log, max_bytes=500, generations=3) is True
    assert gzip.open(tmp_path / "stdout.log.1.gz", "rb").read() == b"B" * 1000
    assert gzip.open(tmp_path / "stdout.log.2.gz", "rb").read() == b"A" * 1000

    # 世代上限を超えた分は破棄される
    for ch in (b"C", b"D", b"E"):
        log.write_bytes(ch * 1000)
        rotate_log_file(log, max_bytes=500, generations=3)
    assert not (tmp_path / "stdout.log.4.gz").exists()
    assert gzip.open(tmp_path / "stdout.log.3.gz", "rb").read() == b"C" * 1000


def test_session_and_audit_purge(client):
    from app.database import SessionLocal
    from app.maintenance.service import _purge_audit_logs, _purge_sessions
    from app.models import AuditLog, User, UserSession
    from sqlalchemy import select

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.username == "admin")).scalar_one()
        old = datetime.now(timezone.utc) - timedelta(days=30)
        db.add(
            UserSession(
                user_id=user.id, session_token_hash="x" * 64,
                expires_at=old, created_at=old, last_seen_at=old,
            )
        )
        db.add(AuditLog(action="old.action", timestamp=datetime.now(timezone.utc) - timedelta(days=400)))
        db.commit()
    finally:
        db.close()

    assert _purge_sessions()["purged"] >= 1
    assert _purge_audit_logs()["purged"] >= 1


def test_run_maintenance_all_tasks_ok(client):
    from app.maintenance.service import run_maintenance

    results = run_maintenance()
    assert set(results) == {
        "rotate_app_logs", "purge_sessions", "purge_audit_logs", "purge_file_trash",
        "optimize_db", "check_disk",
    }
    assert all(r["ok"] for r in results.values()), results


def test_health_checks(client):
    from app.maintenance.watchdog import beat, health_checks, is_healthy

    beat("collector")
    beat("scheduler")
    checks = health_checks()
    assert checks["database"]["ok"] is True
    assert is_healthy() is True


def test_self_status_api(admin_client):
    r = admin_client.get("/api/v1/system/self-status")
    assert r.status_code == 200
    body = r.json()
    assert "watchdog_enabled" in body
    assert body["checks"]["database"]["ok"] is True

    # 権限なしユーザーは 403（viewer は system.view を持つので未認証で確認）
    admin_client.cookies.clear()
    assert admin_client.get("/api/v1/system/self-status").status_code == 401


def test_sd_notify_without_socket(monkeypatch):
    from app.maintenance.watchdog import sd_notify, watchdog_enabled

    # systemd サービス配下でテストを実行しても親の通知環境を継承しない。
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert sd_notify("READY=1") is False
    assert watchdog_enabled() is False
