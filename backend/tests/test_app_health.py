import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.conftest import CSRF_HEADERS


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ready-marker")

    def log_message(self, *args):
        return


def test_tcp_and_http_health_checks():
    from app.applications.health import run
    from app.schemas.apps import HealthCheckConfig

    tcp = socket.socket()
    tcp.bind(("127.0.0.1", 0))
    tcp.listen()
    try:
        result = run(HealthCheckConfig(type="tcp", port=tcp.getsockname()[1]), process_running=True)
        assert result.ok is True
    finally:
        tcp.close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run(HealthCheckConfig(
            type="http", url=f"http://127.0.0.1:{server.server_port}/health",
            expected_status=200, body_contains="ready-marker",
        ), process_running=True)
        assert result.ok is True
    finally:
        server.shutdown()


def test_file_health_check_enforces_allowed_root(tmp_path):
    from app.applications.health import run
    from app.schemas.apps import HealthCheckConfig
    from app.config import get_config

    allowed = get_config().files.allowed_roots[0]
    target = __import__("pathlib").Path(allowed) / "health.ready"
    target.write_text("ok")
    assert run(HealthCheckConfig(type="file", path=str(target)), process_running=True).ok
    denied = run(HealthCheckConfig(type="file", path="/etc/passwd"), process_running=True)
    assert denied.ok is False


def test_health_config_api_and_manual_check(admin_client, monkeypatch):
    from app.applications import router
    from app.schemas.apps import HealthCheckResult

    created = admin_client.post(
        "/api/v1/apps",
        json={
            "name": "Health API", "application_type": "systemd_service",
            "systemd_unit_name": "health-api.service",
            "health_check": {"type": "tcp", "host": "127.0.0.1", "port": 8765},
        },
        headers=CSRF_HEADERS,
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]
    try:
        assert created.json()["health_check"]["type"] == "tcp"
        expected = HealthCheckResult(ok=True, message="ok", checked_at=datetime.now(timezone.utc).isoformat(), latency_ms=1)
        monkeypatch.setattr(router.app_health, "check_app", lambda app: expected)
        checked = admin_client.post(f"/api/v1/apps/{app_id}/health-check", headers=CSRF_HEADERS)
        assert checked.status_code == 200
        assert checked.json()["ok"] is True
    finally:
        admin_client.delete(f"/api/v1/apps/{app_id}", headers=CSRF_HEADERS)


def test_failed_health_marks_running_app_degraded(monkeypatch):
    from app.applications import health, service
    from app.models import ManagedApplication
    from app.schemas.apps import HealthCheckResult

    app = ManagedApplication(
        id=999, name="degraded", application_type="systemd_service",
        systemd_unit_name="degraded.service", arguments_json="[]",
        health_check_json='{"type":"process"}',
    )
    monkeypatch.setattr(service.sd, "query_status", lambda unit: {
        "status": "RUNNING", "pid": None, "restart_count": 0,
    })
    monkeypatch.setattr(health, "cached", lambda app_id: HealthCheckResult(
        ok=False, message="unhealthy", checked_at=datetime.now(timezone.utc).isoformat(), latency_ms=1,
    ))
    runtime = service.runtime_info(app)
    assert runtime.status == "DEGRADED"
    assert runtime.health and runtime.health.ok is False
