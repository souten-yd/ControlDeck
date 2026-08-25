import socket
import subprocess
import threading
from types import SimpleNamespace
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


def test_allowed_command_health_uses_fixed_systemd_unit_and_hides_output(monkeypatch):
    from app.applications import health
    from app.config import HealthCommandDefinition
    from app.schemas.apps import HealthCheckConfig

    definition = HealthCommandDefinition(label="Fixed self-check", argv=["/usr/bin/true", "--fixed"])
    monkeypatch.setattr(health, "get_config", lambda: SimpleNamespace(
        applications=SimpleNamespace(health_commands={"self-check": definition}),
    ))
    monkeypatch.setattr(health.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 7, stdout="secret-output", stderr="secret-error")

    monkeypatch.setattr(health.subprocess, "run", fake_run)
    result = health.run(
        HealthCheckConfig(type="command", command_id="self-check", timeout_seconds=2),
        process_running=True,
    )

    assert result.ok is False
    assert "終了コード 7" in result.message
    assert "secret" not in result.message
    argv, kwargs = calls[0]
    assert argv[0] == "/usr/bin/systemd-run"
    assert "--user" in argv and "--wait" in argv and "--collect" in argv
    assert "--property=StandardOutput=null" in argv
    assert "--property=StandardError=null" in argv
    assert argv[-2:] == ["/usr/bin/true", "--fixed"]
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert "shell" not in kwargs


def test_command_health_is_fail_closed_without_registered_id(monkeypatch):
    from app.applications import health, service
    from app.schemas.apps import AppCreate, HealthCheckConfig

    monkeypatch.setattr(health, "get_config", lambda: SimpleNamespace(
        applications=SimpleNamespace(health_commands={}),
    ))
    result = health.run(
        HealthCheckConfig(type="command", command_id="missing"), process_running=True,
    )
    assert result.ok is False
    assert result.message == "許可コマンドが見つかりません"

    try:
        service.validate_fields(AppCreate(
            name="invalid command health", application_type="systemd_service",
            systemd_unit_name="valid.service",
            health_check=HealthCheckConfig(type="command", command_id="missing"),
        ))
    except service.AppValidationError as exc:
        assert "登録済みの許可コマンド" in str(exc)
    else:
        raise AssertionError("unknown command ID must be rejected")


def test_health_command_catalog_does_not_expose_argv(admin_client):
    response = admin_client.get("/api/v1/apps/health-commands")
    assert response.status_code == 200
    assert response.json() == []


# ── CPU ファン ───────────────────────────────────────────────────────────
#
# マザーボードのファンは Super-I/O チップが持つ。そのドライバが無い環境では
# hwmon に何も出ないので、CPU ファンは本当に読めない。読めないことと、
# 0 rpm（止まっている）は別なので、推測で埋めない。

def test_the_cpu_fan_is_only_reported_when_something_names_it(tmp_path, monkeypatch):
    from app.monitoring import cpu_fan

    root = tmp_path / "hwmon"
    chip = root / "hwmon9"
    chip.mkdir(parents=True)
    (chip / "name").write_text("nct6799\n", encoding="utf-8")
    (chip / "fan1_input").write_text("620\n", encoding="utf-8")
    (chip / "pwm1").write_text("128\n", encoding="utf-8")
    monkeypatch.setattr(cpu_fan, "HWMON_ROOT", root)
    monkeypatch.setattr(cpu_fan, "_from_sensors", lambda: (None, None))

    # 番号しかない状態では採用しない。fan1 が CPU とは限らず、当てにすると
    # 筐体ファンや水冷ポンプを CPU ファンとして出してしまう。
    assert cpu_fan.read_cpu_fan() == (None, None)

    (chip / "fan1_label").write_text("CPU Fan\n", encoding="utf-8")
    assert cpu_fan.read_cpu_fan() == (620, 50)


def test_a_non_super_io_chip_is_never_read_as_the_cpu_fan(tmp_path, monkeypatch):
    """PSU や GPU の fan を CPU fan として出さない。"""
    from app.monitoring import cpu_fan

    root = tmp_path / "hwmon"
    chip = root / "hwmon1"
    chip.mkdir(parents=True)
    (chip / "name").write_text("amdgpu\n", encoding="utf-8")
    (chip / "fan1_label").write_text("CPU Fan\n", encoding="utf-8")
    (chip / "fan1_input").write_text("900\n", encoding="utf-8")
    monkeypatch.setattr(cpu_fan, "HWMON_ROOT", root)
    monkeypatch.setattr(cpu_fan, "_from_sensors", lambda: (None, None))

    assert cpu_fan.read_cpu_fan() == (None, None)


def test_the_sensors_naming_is_used_when_the_driver_gives_no_label(monkeypatch):
    """ドライバが番号しか出さない板でも、/etc/sensors.d が名付けていれば拾える。"""
    import json
    import subprocess

    from app.monitoring import cpu_fan

    payload = json.dumps({"nct6799-isa-0a20": {"CPU Fan": {"fan2_input": 705.0}}})
    monkeypatch.setattr(cpu_fan, "_from_hwmon", lambda: (None, None))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: type("R", (), {"stdout": payload})(),
    )
    assert cpu_fan.read_cpu_fan() == (705, None)
