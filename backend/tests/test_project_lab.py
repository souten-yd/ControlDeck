from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.project_lab import runs, service
from app.schemas.project_lab import ProjectManifest


def _purge_runs() -> None:
    """session共有DBへ実行記録を残さない（後続testの同時実行判定を汚さない）。"""
    from app.database import SessionLocal
    from app.models import ProjectRun, ProjectRunArtifact

    with SessionLocal() as db:
        db.query(ProjectRunArtifact).delete()
        db.query(ProjectRun).delete()
        db.commit()


def _project(root: Path, name: str = "demo") -> Path:
    project = root / name
    (project / ".controldeck").mkdir(parents=True)
    (project / "reports").mkdir()
    (project / "node_modules").mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (project / "package.json").write_text('{"dependencies":{"react":"latest","vite":"latest"}}', encoding="utf-8")
    (project / "index.html").write_text("<h1>安全な成果物</h1>", encoding="utf-8")
    (project / "cdn.html").write_text(
        '<html><head><script src="https://cdn.example.com/three.min.js"></script></head><body></body></html>',
        encoding="utf-8")
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
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())

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
    assert ".env" not in paths and "node_modules/ignored.json" not in paths
    assert "package.json" not in paths
    # source fileも閲覧・実行対象。runnableはPython／JavaScriptだけをtrueにする。
    assert {"main.py", "style.css"} <= paths
    source = next(item for item in detail["artifacts"] if item["path"] == "main.py")
    assert source["kind"] == "code" and source["language"] == "python" and source["runnable"] is True
    stylesheet = next(item for item in detail["artifacts"] if item["path"] == "style.css")
    assert stylesheet["kind"] == "code" and stylesheet["runnable"] is False
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
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())

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
    # HTMLはscriptを動かせるが、CSP sandboxで不透明originへ隔離しconnectを遮断する。
    html_policy = html.headers["content-security-policy"]
    assert "sandbox allow-scripts" in html_policy and "connect-src 'none'" in html_policy
    assert "allow-same-origin" not in html_policy and "form-action 'none'" in html_policy
    assert html.headers["x-content-type-options"] == "nosniff"
    # sandbox iframeで落ちるstorage APIは、preview配信時だけ互換実装へ差し替える。
    assert "Control Deck preview shim" in html.text
    assert html.text.index("preview shim") < html.text.index("安全な成果物")
    # 外部CDNは既定で遮断し、明示指定のときだけ許可する（sandboxは維持）。
    assert detail.json()["artifacts"] is not None
    cdn = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/cdn.html")
    assert "script-src 'self' 'unsafe-inline'" in cdn.headers["content-security-policy"]
    allowed = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/cdn.html?external=true")
    policy = allowed.headers["content-security-policy"]
    assert "script-src 'self' https:" in policy and "sandbox allow-scripts" in policy
    assert "allow-same-origin" not in policy
    external_flag = next(item for item in detail.json()["artifacts"] if item["path"] == "cdn.html")
    assert external_flag["external"] is True
    plain_flag = next(item for item in detail.json()["artifacts"] if item["path"] == "index.html")
    assert plain_flag["external"] is False

    downloaded = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/index.html?download=true")
    assert downloaded.status_code == 200 and "sandbox" not in downloaded.headers["content-security-policy"]
    assert "preview shim" not in downloaded.text
    assert (root / "demo" / "index.html").read_text(encoding="utf-8") == "<h1>安全な成果物</h1>"
    style = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/style.css")
    assert style.status_code == 200 and style.headers["content-type"].startswith("text/css")
    assert "default-src 'none'" in style.headers["content-security-policy"]
    source = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/main.py")
    assert source.status_code == 200 and "source is not an artifact" in source.text
    source_preview = admin_client.get("/api/v1/project-lab/projects/demo/previews/main.py")
    assert source_preview.status_code == 200 and "print(" in source_preview.json()["previewText"]
    missing = admin_client.get("/api/v1/project-lab/projects/missing")
    assert missing.status_code == 404


def test_external_preview_setting_applies_without_query(admin_client, tmp_path, monkeypatch):
    """設定で常時許可にすると、query指定なし（サブページ遷移含む）でも外部読み込みを通す。"""
    root = tmp_path / "CodeDEV"
    root.mkdir()
    _project(root)
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
    monkeypatch.setattr(service, "data_dir", lambda: tmp_path / "data")
    headers = {"X-Requested-With": "ControlDeck"}

    assert admin_client.get("/api/v1/project-lab/settings").json()["allow_external_preview"] is False
    strict = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/cdn.html")
    assert "script-src 'self' 'unsafe-inline'" in strict.headers["content-security-policy"]

    saved = admin_client.put("/api/v1/project-lab/settings", json={"allow_external_preview": True}, headers=headers)
    assert saved.status_code == 200 and saved.json()["allow_external_preview"] is True
    relaxed = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/cdn.html")
    policy = relaxed.headers["content-security-policy"]
    assert "script-src 'self' https:" in policy and "sandbox allow-scripts" in policy
    assert "allow-same-origin" not in policy

    admin_client.put("/api/v1/project-lab/settings", json={"allow_external_preview": False}, headers=headers)
    assert "script-src 'self' 'unsafe-inline'" in admin_client.get(
        "/api/v1/project-lab/projects/demo/artifacts/cdn.html").headers["content-security-policy"]


def test_split_app_subresources_are_served_with_correct_types(admin_client, tmp_path, monkeypatch):
    """HTML・JS・CSS・アセットに分割されたアプリを、相対パスのまま配信できる。"""
    root = tmp_path / "CodeDEV"
    (root / "split" / "assets").mkdir(parents=True)
    project = root / "split"
    (project / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="assets/app.css"></head>'
        '<body><script type="module" src="assets/main.mjs"></script></body></html>', encoding="utf-8")
    (project / "assets" / "app.css").write_text("body{margin:0}", encoding="utf-8")
    (project / "assets" / "main.mjs").write_text("export const ok = 1;\n", encoding="utf-8")
    (project / "assets" / "engine.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    (project / "assets" / "scene.glb").write_bytes(b"glTF")
    (project / "assets" / "sprites.map").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())

    expected = {
        "assets/app.css": "text/css",
        "assets/main.mjs": "text/javascript",
        "assets/engine.wasm": "application/wasm",
        "assets/scene.glb": "model/gltf-binary",
        "assets/sprites.map": "application/json",
    }
    for relative, content_type in expected.items():
        response = admin_client.get(f"/api/v1/project-lab/projects/split/artifacts/{relative}")
        assert response.status_code == 200, relative
        assert response.headers["content-type"].startswith(content_type), relative

    # 一覧には成果物とコードだけを出し、バイナリのアセットは並べない。
    listed = {item["path"] for item in admin_client.get("/api/v1/project-lab/projects/split").json()["artifacts"]}
    assert {"index.html", "assets/app.css", "assets/main.mjs"} <= listed
    assert "assets/engine.wasm" not in listed and "assets/scene.glb" not in listed
    # project外への相対脱出は従来どおり拒否する。
    assert admin_client.get("/api/v1/project-lab/projects/split/artifacts/../escape.js").status_code == 404


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
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
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


def test_file_run_executes_single_source_without_manifest(admin_client, tmp_path, monkeypatch, request):
    """manifestを持たないprojectでも、成果物のPython fileを1本だけ隔離実行できる。"""
    root = tmp_path / "CodeDEV"
    root.mkdir()
    project = root / "plain"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
    (project / "src" / "style.css").write_text("body{}", encoding="utf-8")
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
    monkeypatch.setattr(runs, "_systemd_tools", lambda: ("/usr/bin/systemd-run", "/usr/bin/systemctl", "/usr/bin/journalctl"))
    monkeypatch.setattr(runs.shutil, "which", lambda value: f"/usr/bin/{value}")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0].endswith("systemd-run"):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[0].endswith("systemctl"):
            return SimpleNamespace(returncode=0, stdout="LoadState=loaded\nActiveState=active\nSubState=running\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(runs.subprocess, "run", fake_run)
    started = admin_client.post(
        "/api/v1/project-lab/projects/plain/file-runs",
        json={"path": "src/main.py", "timeout_seconds": 30}, headers={"X-Requested-With": "ControlDeck"},
    )
    assert started.status_code == 201, started.text
    request.addfinalizer(_purge_runs)
    body = started.json()
    assert body["profileType"] == "file" and body["command"] == ["python3", "./main.py"]
    assert body["previewUrl"] is None
    launch = calls[0]
    assert f"--property=WorkingDirectory={project / 'src'}" in launch
    assert f"--property=ReadWritePaths={project}" in launch
    assert "--property=NoNewPrivileges=yes" in launch and "--property=ProtectHome=read-only" in launch
    assert Path(launch[-2]).name.startswith("python3") and launch[-1] == "./main.py"

    # 実行できないfile typeとproject外pathは開始前に拒否する。
    assert admin_client.post(
        "/api/v1/project-lab/projects/plain/file-runs",
        json={"path": "src/style.css"}, headers={"X-Requested-With": "ControlDeck"},
    ).status_code == 409
    assert admin_client.post(
        "/api/v1/project-lab/projects/plain/file-runs",
        json={"path": "../escape.py"}, headers={"X-Requested-With": "ControlDeck"},
    ).status_code == 404

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
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
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
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
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
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
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


def test_preview_token_serves_relative_assets_without_cookies(admin_client, monkeypatch, tmp_path):
    """sandboxの不透明originから読めるよう、token付きURLはcookie無しで配信する。

    HTMLからの相対参照（js等）はcross-site扱いでcookieが送られず、通常のartifact URLでは
    401になる。tokenをパスへ入れると相対解決でも引き継がれるので、その経路で配信する。
    """
    from tests.conftest import CSRF_HEADERS
    from app.project_lab import service

    root = tmp_path / "CodeDEV"
    project = root / "demo"
    project.mkdir(parents=True)
    (project / "index.html").write_text(
        '<meta charset="utf-8"><script src="lib.js"></script>', encoding="utf-8")
    (project / "lib.js").write_text("window.ok = true;", encoding="utf-8")
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())

    issued = admin_client.post("/api/v1/project-lab/projects/demo/preview-token", headers=CSRF_HEADERS)
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]

    # cookieを付けずに（=sandbox内からの要求と同じ条件で）読めること
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as anonymous:
        html = anonymous.get(f"/api/v1/project-lab/preview/{token}/index.html")
        assert html.status_code == 200
        assert "Control Deck preview shim" in html.text
        asset = anonymous.get(f"/api/v1/project-lab/preview/{token}/lib.js")
        assert asset.status_code == 200
        assert asset.text == "window.ok = true;"
        # 通常のartifact URLはcookieが無ければ従来どおり弾く
        assert anonymous.get("/api/v1/project-lab/projects/demo/artifacts/lib.js").status_code == 401


def test_preview_token_is_scoped_and_rejects_tampering(admin_client, monkeypatch, tmp_path):
    """tokenはプロジェクト単位。改竄や別プロジェクトへの流用はできない。"""
    from tests.conftest import CSRF_HEADERS
    from app.project_lab import service

    root = tmp_path / "CodeDEV"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "index.html").write_text("<p>ok</p>", encoding="utf-8")
    (root / "secret").mkdir(parents=True)
    (root / "secret" / "index.html").write_text("<p>secret</p>", encoding="utf-8")
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())

    token = admin_client.post("/api/v1/project-lab/projects/demo/preview-token",
                              headers=CSRF_HEADERS).json()["token"]
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as anonymous:
        # 配下から出られない
        assert anonymous.get(f"/api/v1/project-lab/preview/{token}/../secret/index.html").status_code == 404
        # 改竄したtokenは通らない
        assert anonymous.get(f"/api/v1/project-lab/preview/{token[:-2]}xx/index.html").status_code == 404


def test_a_preview_can_read_its_own_assets(admin_client, tmp_path, monkeypatch):
    """生成されたものが、自分で作った画像や音声を読めること。

    sandbox の中は不透明 origin なので、要求は Origin: null で出る。CSP の
    connect-src に自分の経路が無ければ XHR は全部落ち、CORS ヘッダが無ければ
    crossOrigin を付けて読む loader（THREE.TextureLoader の既定）は弾かれる。
    実測（2026-09-06）: 音声 18 本が全滅し、画像も読めなかった。
    """
    root = tmp_path / "CodeDEV"
    root.mkdir()
    _project(root)
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
    monkeypatch.setattr(service, "data_dir", lambda: tmp_path / "data")
    headers = {"X-Requested-With": "ControlDeck"}

    issued = admin_client.post("/api/v1/project-lab/projects/demo/preview-token", headers=headers)
    assert issued.status_code == 200
    token = issued.json()["token"]

    page = admin_client.get(f"/api/v1/project-lab/preview/{token}/index.html")
    assert page.status_code == 200
    policy = page.headers["content-security-policy"]
    # 自分の配信経路だけを通す。'self' ではない——Control Deck の API は同じ
    # origin にあるので、'self' を許すとそちらへも要求を出せてしまう。
    assert f"/api/v1/project-lab/preview/{token}/" in policy
    assert "connect-src 'none'" not in policy
    assert "connect-src 'self'" not in policy

    asset = admin_client.get(f"/api/v1/project-lab/preview/{token}/reports/chart.png")
    assert asset.status_code == 200
    assert asset.headers["access-control-allow-origin"] == "*"
    assert asset.headers["cross-origin-resource-policy"] == "cross-origin"
    # 指定が無いとブラウザが Last-Modified から勝手に鮮度を決める。応答ヘッダを
    # 直しても、既に持っている側には届かない（PC だけ直らない、という形で起きた）。
    assert asset.headers["cache-control"] == "no-cache"


def test_the_authenticated_artifact_route_stays_closed(admin_client, tmp_path, monkeypatch):
    """token を介さない経路は緩めない。緩める理由が preview にしか無い。"""
    root = tmp_path / "CodeDEV"
    root.mkdir()
    _project(root)
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
    monkeypatch.setattr(service, "data_dir", lambda: tmp_path / "data")

    direct = admin_client.get("/api/v1/project-lab/projects/demo/artifacts/reports/chart.png")
    assert direct.status_code == 200
    assert "access-control-allow-origin" not in direct.headers


def test_an_unchanged_preview_asset_is_not_sent_again(admin_client, tmp_path, monkeypatch):
    """no-cache は「毎回問い合わせる」であって「毎回受け取り直す」ではない。

    判定を返さないと、画像や音を開くたびに全量が流れる。唐揚げ防衛隊は 1 画面で
    画像 790KB と音声 18 本を読むので、モバイルではそのまま体感に出る。
    """
    root = tmp_path / "CodeDEV"
    root.mkdir()
    _project(root)
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
    monkeypatch.setattr(service, "data_dir", lambda: tmp_path / "data")
    headers = {"X-Requested-With": "ControlDeck"}

    token = admin_client.post(
        "/api/v1/project-lab/projects/demo/preview-token", headers=headers
    ).json()["token"]
    url = f"/api/v1/project-lab/preview/{token}/reports/chart.png"

    first = admin_client.get(url)
    assert first.status_code == 200 and first.headers["etag"]

    again = admin_client.get(url, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.content == b""
    # 変わっていないと答えるときも、CORS の判断材料は返す。返さないと、
    # 持っている側が使えないままになる。
    assert again.headers["access-control-allow-origin"] == "*"
