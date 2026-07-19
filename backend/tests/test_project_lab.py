from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.project_lab import runs, service
from app.schemas.project_lab import ProjectManifest


def _project(root: Path, name: str = "demo") -> Path:
    project = root / name
    (project / ".controldeck").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "node_modules").mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (project / "package.json").write_text('{"dependencies":{"react":"latest","vite":"latest"}}', encoding="utf-8")
    (project / "index.html").write_text("<h1>安全な成果物</h1>", encoding="utf-8")
    (project / "style.css").write_text("body { color: #123; }", encoding="utf-8")
    (project / "reports" / "result.json").write_text('{"score": 98, "api_token": "must-not-leak"}', encoding="utf-8")
    (project / "reports" / "credentials.json").write_text('{"password": "must-not-leak"}', encoding="utf-8")
    (project / "reports" / "metrics.csv").write_text("name,value\ncpu,42\n", encoding="utf-8")
    (project / "reports" / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (project / "main.py").write_text("print('source is not an artifact')", encoding="utf-8")
    (project / ".env").write_text("TOKEN=must-not-leak", encoding="utf-8")
    (project / "node_modules" / "ignored.json").write_text("{}", encoding="utf-8")
    manifest = {
        "schemaVersion": 1, "name": "Demo Dashboard", "description": "成果物評価用",
        "profiles": [{
            "id": "preview", "label": "Static preview", "type": "static_html",
            "command": [], "cwd": ".", "environment": {"MODE": "test"},
            "secret_refs": ["OPTIONAL_API_TOKEN"], "artifacts": ["reports/*"],
        }],
    }
    (project / ".controldeck" / "project.json").write_text(json.dumps(manifest), encoding="utf-8")
    return project


def test_manifest_rejects_shell_string_secret_literal_and_escape():
    base = {
        "schemaVersion": 1, "name": "bad", "profiles": [{
            "id": "run", "label": "Run", "type": "cli", "command": "python main.py",
        }],
    }
    with pytest.raises(Exception):
        ProjectManifest.model_validate(base)
    base["profiles"][0]["command"] = ["python", "main.py"]
    base["profiles"][0]["cwd"] = "../outside"
    with pytest.raises(Exception):
        ProjectManifest.model_validate(base)
    base["profiles"][0]["cwd"] = "."
    base["profiles"][0]["environment"] = {"API_TOKEN": "literal"}
    with pytest.raises(Exception):
        ProjectManifest.model_validate(base)
    base["profiles"][0]["environment"] = {}
    base["profiles"][0]["command"] = ["bash", "-c", "python main.py"]
    with pytest.raises(Exception):
        ProjectManifest.model_validate(base)
    base["profiles"][0]["command"] = ["python", "main.py", "--api_token=literal"]
    with pytest.raises(Exception):
        ProjectManifest.model_validate(base)


def test_project_discovery_manifest_artifacts_and_containment(tmp_path, monkeypatch):
    root = tmp_path / "CodeDEV"
    root.mkdir()
    project = _project(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text('{"secret": true}', encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (project / "reports" / "escape.json").symlink_to(outside / "secret.json")
    bad = root / "bad-manifest"
    (bad / ".controldeck").mkdir(parents=True)
    (bad / ".controldeck" / "project.json").write_text(json.dumps({
        "schemaVersion": 1, "name": "bad", "profiles": [{
            "id": "bad", "label": "bad", "type": "cli", "command": ["python"],
            "environment": {"API_TOKEN": "diagnostic-must-not-leak"},
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(service, "PROJECT_ROOT", root)

    rows = service.list_projects()
    assert {item["id"] for item in rows} == {"demo", "bad-manifest"}
    bad_payload = json.dumps(service.project_detail("bad-manifest"))
    assert "diagnostic-must-not-leak" not in bad_payload
    detail = service.project_detail("demo")
    assert detail["name"] == "Demo Dashboard"
    assert {"python", "node", "vite", "react", "static-web"} <= set(detail["technologies"])
    assert detail["capabilities"] == {
        "discovery": True, "artifactPreview": True, "execution": True,
        "webProxy": True, "llmEvaluation": False,
    }
    paths = {item["path"] for item in detail["artifacts"]}
    assert {"index.html", "reports/result.json", "reports/metrics.csv", "reports/chart.png"} <= paths
    assert "main.py" not in paths and ".env" not in paths and "node_modules/ignored.json" not in paths
    assert "package.json" not in paths
    assert "style.css" not in paths
    assert "reports/escape.json" not in paths
    result_path = service.resolve_artifact(project, "reports/result.json")
    result = service.artifact_info(project, result_path, include_preview=True)
    assert result["structuredPreview"] == {"score": 98, "api_token": "***"}
    assert "credentials.json" not in paths
    table = service.artifact_info(project, service.resolve_artifact(project, "reports/metrics.csv"), include_preview=True)
    assert table["structuredPreview"]["headers"] == ["name", "value"]
    assert table["structuredPreview"]["rows"] == [["cpu", "42"]]
    with pytest.raises(service.ProjectLabError):
        service.resolve_project("escape")
    with pytest.raises(service.ProjectLabError):
        service.resolve_artifact(project, "../outside/secret.json")


def test_project_lab_api_is_read_only_authenticated_and_safe(admin_client, tmp_path, monkeypatch):
    root = tmp_path / "CodeDEV"
    root.mkdir()
    _project(root)
    monkeypatch.setattr(service, "PROJECT_ROOT", root)

    listed = admin_client.get("/api/v1/project-lab/projects")
    assert listed.status_code == 200 and listed.json()[0]["name"] == "Demo Dashboard"
    detail = admin_client.get("/api/v1/project-lab/projects/demo")
    assert detail.status_code == 200
    payload = json.dumps(detail.json()).lower()
    assert "must-not-leak" not in payload and "token=must" not in payload and '"mode": "test"' not in payload
    assert detail.json()["manifest"]["profiles"][0]["environmentNames"] == ["MODE"]
    preview = admin_client.get("/api/v1/project-lab/projects/demo/previews/reports/result.json")
    assert preview.status_code == 200 and preview.json()["structuredPreview"]["api_token"] == "***"
    html = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/index.html")
    assert html.status_code == 200 and "安全な成果物" in html.text
    assert html.headers["x-frame-options"] == "SAMEORIGIN"
    assert "default-src 'none'" in html.headers["content-security-policy"]
    style = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/style.css")
    assert style.status_code == 200 and style.headers["content-type"].startswith("text/css")
    source = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/main.py")
    assert source.status_code == 404
    missing = admin_client.get("/api/v1/project-lab/projects/missing")
    assert missing.status_code == 404


def test_project_lab_permission_is_available_to_operator_only():
    from app.security.permissions import ROLE_PRESETS

    assert "project_lab.view" in ROLE_PRESETS["administrator"]
    assert "project_lab.view" in ROLE_PRESETS["operator"]
    assert "project_lab.run" in ROLE_PRESETS["operator"]
    assert "project_lab.view" not in ROLE_PRESETS["viewer"]
    assert "project_lab.run" not in ROLE_PRESETS["viewer"]


def test_project_run_uses_systemd_argv_tracks_artifacts_and_redacts_logs(admin_client, tmp_path, monkeypatch):
    from app.database import SessionLocal
    from app.models import ProjectRun, ProjectRunArtifact

    root = tmp_path / "CodeDEV"
    root.mkdir()
    project = _project(root)
    manifest_path = project / ".controldeck" / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profiles"] = [{
        "id": "test", "label": "Test", "type": "test",
        "command": ["python3", "main.py"], "cwd": ".",
        "environment": {"MODE": "test"}, "secret_refs": [], "artifacts": ["reports/*"],
    }]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(service, "PROJECT_ROOT", root)
    monkeypatch.setattr(runs, "_systemd_tools", lambda: ("/usr/bin/systemd-run", "/usr/bin/systemctl", "/usr/bin/journalctl"))
    monkeypatch.setattr(runs.shutil, "which", lambda value: f"/usr/bin/{value}")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0].endswith("systemd-run"):
            assert kwargs.get("shell") is None
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[0].endswith("systemctl") and "show" in argv:
            return SimpleNamespace(returncode=0, stdout="LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nExecMainStatus=0\n", stderr="")
        if argv[0].endswith("journalctl"):
            return SimpleNamespace(returncode=0, stdout=b"done api_token=must-not-leak\n", stderr=b"")
        raise AssertionError(argv)

    monkeypatch.setattr(runs.subprocess, "run", fake_run)
    with SessionLocal() as db:
        row = runs.start_run(db, project_id="demo", profile_id="test", timeout_seconds=45, created_by=None)
        (project / "reports" / "new.json").write_text('{"ok": true}', encoding="utf-8")
        payload = runs.run_out(db, row, include_logs=True)
        assert payload["status"] == "SUCCEEDED"
        assert payload["logs"] == "done api_token=***\n"
        assert payload["artifacts"][0]["path"] == "reports/new.json"
        assert payload["artifacts"][0]["changeType"] == "created"
        db.query(ProjectRunArtifact).filter(ProjectRunArtifact.run_id == row.id).delete()
        db.delete(row)
        db.commit()
    launch = calls[0]
    assert isinstance(launch, list)
    assert "--property=NoNewPrivileges=yes" in launch
    assert "--property=ProtectSystem=strict" in launch
    assert "--property=RemainAfterExit=yes" in launch
    assert f"--property=ReadWritePaths={project}" in launch
    assert "--setenv=MODE=test" in launch
    assert Path(launch[-2]).name.startswith("python3") and launch[-1] == "main.py"


def test_project_run_rejects_secrets_and_non_sdk(tmp_path, monkeypatch, admin_client):
    from app.database import SessionLocal

    root = tmp_path / "CodeDEV"
    root.mkdir()
    project = _project(root)
    manifest_path = project / ".controldeck" / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profiles"] = [
        {"id": "secret", "label": "Secret", "type": "cli", "command": ["python3", "main.py"], "secret_refs": ["API_TOKEN"]},
        {"id": "binary", "label": "Binary", "type": "cli", "command": ["curl", "https://example.invalid"]},
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(service, "PROJECT_ROOT", root)
    with SessionLocal() as db:
        with pytest.raises(runs.ProjectRunError, match="Secret"):
            runs.start_run(db, project_id="demo", profile_id="secret", timeout_seconds=10, created_by=None)
        with pytest.raises(runs.ProjectRunError, match="許可SDK"):
            runs.start_run(db, project_id="demo", profile_id="binary", timeout_seconds=10, created_by=None)


def test_web_run_allocates_localhost_port_and_substitutes_argv(admin_client, tmp_path, monkeypatch):
    from app.database import SessionLocal
    from app.models import ProjectRun

    root = tmp_path / "CodeDEV"
    root.mkdir()
    project = _project(root)
    manifest_path = project / ".controldeck" / "project.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["profiles"] = [{
        "id": "web", "label": "Web", "type": "web",
        "command": ["python3", "-m", "http.server", "{port}", "--bind", "{host}"],
        "cwd": ".", "environment": {}, "secret_refs": [], "artifacts": [],
    }]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(service, "PROJECT_ROOT", root)
    monkeypatch.setattr(runs, "_systemd_tools", lambda: ("/usr/bin/systemd-run", "/usr/bin/systemctl", "/usr/bin/journalctl"))
    monkeypatch.setattr(runs.shutil, "which", lambda value: f"/usr/bin/{value}")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runs.subprocess, "run", fake_run)
    with SessionLocal() as db:
        row = runs.start_run(db, project_id="demo", profile_id="web", timeout_seconds=120, created_by=None)
        assert row.profile_type == "web" and row.web_port
        command = json.loads(row.command_json)
        assert command[-3:] == [str(row.web_port), "--bind", "127.0.0.1"]
        launch = calls[0]
        assert f"--setenv=PORT={row.web_port}" in launch
        assert "--setenv=HOST=127.0.0.1" in launch
        db.delete(row)
        db.commit()


def test_web_preview_requires_unit_owned_listen_port(monkeypatch):
    from app.models import ProjectRun

    row = ProjectRun(id=42, profile_type="web", web_port=32123, status="RUNNING", unit_name="unit")
    monkeypatch.setattr(runs, "_show", lambda name: {"ActiveState": "active", "MainPID": "123"})

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def children(self, recursive=False):
            return []

        def net_connections(self, kind="tcp"):
            return [SimpleNamespace(status=runs.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=32123))]

    monkeypatch.setattr(runs.psutil, "Process", FakeProcess)
    assert runs.web_preview_ready(row) is True
    row.web_port = 32124
    assert runs.web_preview_ready(row) is False


def test_project_web_proxy_strips_control_deck_credentials():
    from starlette.requests import Request

    from app.project_lab.webview import _upstream_headers

    request = Request({
        "type": "http", "method": "GET", "path": "/", "query_string": b"",
        "headers": [
            (b"cookie", b"control_deck_session=secret; app=value"),
            (b"authorization", b"Bearer secret"), (b"x-csrf-token", b"secret"),
            (b"accept", b"text/html"),
        ],
    })
    assert _upstream_headers(request) == {"accept": "text/html"}


def test_project_lab_api_enforces_operator_and_viewer_permissions(client, tmp_path, monkeypatch):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog, Role, User, UserSession
    from app.security.passwords import hash_password
    from tests.conftest import CSRF_HEADERS

    root = tmp_path / "CodeDEV"
    root.mkdir()
    monkeypatch.setattr(service, "PROJECT_ROOT", root)
    usernames = ["lab-operator", "lab-viewer"]
    with SessionLocal() as db:
        for role_name, username in (("operator", usernames[0]), ("viewer", usernames[1])):
            role = db.execute(select(Role).where(Role.name == role_name)).scalar_one()
            db.add(User(username=username, display_name=username, password_hash=hash_password("Project-Lab-Test!"), role_id=role.id))
        db.commit()
    try:
        operator_login = client.post("/api/v1/auth/login", json={"username": usernames[0], "password": "Project-Lab-Test!"}, headers=CSRF_HEADERS)
        assert operator_login.status_code == 200
        assert client.get("/api/v1/project-lab/projects").status_code == 200
        viewer_login = client.post("/api/v1/auth/login", json={"username": usernames[1], "password": "Project-Lab-Test!"}, headers=CSRF_HEADERS)
        assert viewer_login.status_code == 200
        assert client.get("/api/v1/project-lab/projects").status_code == 403
    finally:
        client.cookies.clear()
        with SessionLocal() as db:
            users = db.execute(select(User).where(User.username.in_(usernames))).scalars().all()
            ids = [user.id for user in users]
            if ids:
                db.query(UserSession).filter(UserSession.user_id.in_(ids)).delete(synchronize_session=False)
                db.query(AuditLog).filter(AuditLog.user_id.in_(ids)).delete(synchronize_session=False)
            for user in users:
                db.delete(user)
            db.commit()
