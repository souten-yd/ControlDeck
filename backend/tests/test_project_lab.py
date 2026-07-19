from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.project_lab import service
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
        "discovery": True, "artifactPreview": True, "execution": False,
        "webProxy": False, "llmEvaluation": False,
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
    assert "project_lab.view" not in ROLE_PRESETS["viewer"]


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
