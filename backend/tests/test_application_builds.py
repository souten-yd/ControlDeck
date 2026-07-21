import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application_builder import build_worker, builds
from app.application_builder.source_generator import SourceBundle
from app.config import application_builds_dir
from app.database import SessionLocal
from app.models import ApplicationBuild, ApplicationBuildArtifact, ApplicationProject, AuditLog, User
from tests.conftest import CSRF_HEADERS


def _bundle(entries: list[tuple[str, bytes]]) -> SourceBundle:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, content)
    payload = stream.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    return SourceBundle(
        archive_name="Build-source.zip", archive_bytes=payload,
        archive_checksum=digest, source_checksum="a" * 64,
        manifest={"generator": {"id": "test.generator", "version": "1.0.0"}}, files=(),
    )


def _minimal_bundle() -> SourceBundle:
    return _bundle([
        ("Build/src/Build/Build.csproj", b"<Project />"),
        ("Build/tests/Build.GeneratedTests/Build.GeneratedTests.csproj", b"<Project />"),
        ("Build/tests/Build.GeneratedTests/Program.cs", b"return 0;"),
    ])


def test_dotnet_sdk_path_uses_explicit_config_allowlist(tmp_path, monkeypatch):
    sdk = tmp_path / "sdk" / "dotnet"
    sdk.parent.mkdir()
    sdk.write_text("", encoding="utf-8")
    sdk.chmod(0o700)
    monkeypatch.delenv("CONTROL_DECK_DOTNET", raising=False)
    monkeypatch.setattr(builds, "get_config", lambda: SimpleNamespace(
        application_builder=SimpleNamespace(dotnet_path=str(sdk)),
    ))
    assert builds.dotnet_sdk_path() == sdk.resolve()

    wrong_name = tmp_path / "sdk" / "not-dotnet"
    wrong_name.write_text("", encoding="utf-8")
    wrong_name.chmod(0o700)
    monkeypatch.setattr(builds, "get_config", lambda: SimpleNamespace(
        application_builder=SimpleNamespace(dotnet_path=str(wrong_name)),
    ))
    assert builds.dotnet_sdk_path() is None


def test_safe_extract_rejects_escape_duplicate_and_non_regular(tmp_path):
    destination = tmp_path / "source"
    destination.mkdir()
    with pytest.raises(builds.ApplicationBuildError, match="unsafe path"):
        builds._safe_extract(_bundle([("../escape.txt", b"no")]), destination)

    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = _bundle([("same.txt", b"one"), ("same.txt", b"two")])
    with pytest.raises(builds.ApplicationBuildError, match="duplicate"):
        builds._safe_extract(duplicate, destination)

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "outside")
    payload = stream.getvalue()
    symlink_bundle = SourceBundle("unsafe.zip", payload, hashlib.sha256(payload).hexdigest(), "b" * 64, {}, ())
    with pytest.raises(builds.ApplicationBuildError, match="non-regular"):
        builds._safe_extract(symlink_bundle, destination)


def test_artifact_path_rejects_parent_escape_and_symlink(tmp_path):
    owner = application_builds_dir().resolve()
    root = owner / f"build-artifact-test-{os.getpid()}"
    root.mkdir(mode=0o700, exist_ok=False)
    try:
        valid = root / "output.bin"
        valid.write_bytes(b"ok")
        row = ApplicationBuild(id=101, build_root=str(root))
        artifact = ApplicationBuildArtifact(id=201, build_id=101, path="output.bin")
        assert builds.artifact_path(row, artifact) == valid

        artifact.path = "../outside.bin"
        with pytest.raises(builds.ApplicationBuildError, match="path is invalid"):
            builds.artifact_path(row, artifact)

        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"secret")
        link = root / "linked.bin"
        link.symlink_to(outside)
        artifact.path = "linked.bin"
        with pytest.raises(builds.ApplicationBuildError, match="unavailable"):
            builds.artifact_path(row, artifact)
    finally:
        if root.exists():
            for item in root.iterdir():
                item.unlink()
            root.rmdir()


def test_build_worker_uses_fixed_dotnet_argv_and_private_environment(tmp_path, monkeypatch):
    owner = application_builds_dir().resolve()
    root = owner / f"build-worker-test-{os.getpid()}"
    source = root / "source" / "Build"
    app_project = source / "src" / "Build" / "Build.csproj"
    test_project = source / "tests" / "Build.GeneratedTests" / "Build.GeneratedTests.csproj"
    app_project.parent.mkdir(parents=True)
    test_project.parent.mkdir(parents=True)
    app_project.write_text("<Project />", encoding="utf-8")
    test_project.write_text("<Project />", encoding="utf-8")
    dotnet = tmp_path / "dotnet"
    dotnet.write_text("", encoding="utf-8")
    dotnet.chmod(0o700)
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    class FakeProcess:
        stdout = iter(["safe build output\n"])

        @staticmethod
        def wait() -> int:
            return 0

    def fake_popen(argv, *, cwd, env, **_kwargs):
        calls.append((argv, cwd, env))
        return FakeProcess()

    monkeypatch.setattr(build_worker.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("CONTROL_DECK_E2E_SECRET", "must-not-reach-generated-code")
    try:
        build_worker.run(str(root), str(dotnet))
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        assert state["phase"] == "completed" and state["result"] == "success"
        assert [call[0][1] for call in calls] == ["restore", "build", "run"]
        assert all(call[0][0] == str(dotnet) for call in calls)
        assert "--warnaserror" in calls[1][0] and "--no-build" in calls[2][0]
        assert all(call[2]["DOTNET_CLI_HOME"] == str(root / ".dotnet-home") for call in calls)
        assert all(call[2]["HOME"] == str(root / ".build-home") for call in calls)
        assert all(call[2]["TMPDIR"] == str(root / ".tmp") for call in calls)
        assert all(call[2]["PATH"] == "/usr/local/bin:/usr/bin:/bin" for call in calls)
        assert all("CONTROL_DECK_E2E_SECRET" not in call[2] for call in calls)
        assert all("CONTROL_DECK_CONFIG" not in call[2] for call in calls)
        assert all("PYTHONPATH" not in call[2] for call in calls)
        assert "DOTNET_CLI_HOME" not in os.environ
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def test_build_worker_network_probe_requires_ipv4_and_ipv6_denial(monkeypatch):
    calls: list[int] = []

    def denied(family, _kind):
        calls.append(family)
        raise PermissionError("denied")

    monkeypatch.setattr(build_worker.socket, "socket", denied)
    build_worker._verify_network_denied()
    assert calls == [build_worker.socket.AF_INET, build_worker.socket.AF_INET6]

    class OpenSocket:
        def close(self):
            pass

    def ipv6_open(family, _kind):
        if family == build_worker.socket.AF_INET:
            raise PermissionError("denied")
        return OpenSocket()

    monkeypatch.setattr(build_worker.socket, "socket", ipv6_open)
    with pytest.raises(build_worker.BuildWorkerError, match="IPv6"):
        build_worker._verify_network_denied()


def test_start_refresh_and_delete_build_are_durable_and_isolated(client, monkeypatch):
    owner = application_builds_dir().resolve()
    fake_dotnet = owner / "test-sdk" / "dotnet"
    fake_dotnet.parent.mkdir(mode=0o700, exist_ok=True)
    fake_dotnet.write_text("", encoding="utf-8")
    fake_dotnet.chmod(0o700)
    systemd_argv: list[str] = []

    def fake_run(argv, **_kwargs):
        if argv and argv[0] == "/usr/bin/systemd-run":
            systemd_argv.extend(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(builds, "dotnet_sdk_path", lambda: fake_dotnet.resolve())
    monkeypatch.setattr(builds, "_tools", lambda: ("/usr/bin/systemd-run", "/usr/bin/systemctl", "/usr/bin/journalctl"))
    monkeypatch.setattr(builds.subprocess, "run", fake_run)
    monkeypatch.setattr(builds, "_show", lambda _unit: {"LoadState": "loaded", "ActiveState": "active", "SubState": "running"})
    row_id = 0
    project_id = 0
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").one()
        project = ApplicationProject(name="Build orchestration test", created_by=admin.id)
        db.add(project)
        db.commit()
        db.refresh(project)
        project_id = project.id
        row = builds.start_build(
            db, project_id=project.id, target_id="console", framework="csharp-console",
            timeout_seconds=600, bundle=_minimal_bundle(), created_by=admin.id,
        )
        row_id = row.id
        root = builds.build_root(row)
        assert row.status == "queued" and root.parent == owner and root.name == f"build-{row.id}"
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert (root / "source.zip").is_file()
        assert "--property=NoNewPrivileges=yes" in systemd_argv
        assert "--property=ProtectSystem=strict" in systemd_argv
        assert "--property=IPAddressDeny=any" in systemd_argv
        assert "--property=RestrictAddressFamilies=AF_UNIX" in systemd_argv
        assert "--property=MemoryMax=2G" in systemd_argv
        assert "--require-network-denied" in systemd_argv
        expected_launcher = Path(os.path.abspath(sys.executable))
        assert str(expected_launcher) in systemd_argv
        assert ".." not in expected_launcher.parts
        if expected_launcher.is_symlink():
            assert str(expected_launcher.resolve()) not in systemd_argv
        assert not any("shell=True" in part for part in systemd_argv)

        (root / "state.json").write_text(json.dumps({"phase": "completed", "result": "success", "exitCode": 0}), encoding="utf-8")
        monkeypatch.setattr(builds, "_show", lambda _unit: {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "Result": "success", "ExecMainStatus": "0"})
        builds.refresh_build(db, row)
        assert row.status == "completed" and row.exit_code == 0 and row.finished_at is not None
        artifacts = db.query(ApplicationBuildArtifact).filter(ApplicationBuildArtifact.build_id == row.id).all()
        assert [(item.kind, item.path) for item in artifacts] == [("source", "source.zip")]
        builds.delete_build(db, row)
        assert db.get(ApplicationBuild, row_id) is None
        assert not root.exists()
        db.delete(project)
        db.commit()
    assert row_id > 0 and project_id > 0
    fake_dotnet.unlink(missing_ok=True)
    fake_dotnet.parent.rmdir()


def test_failed_build_with_uncreated_durable_root_can_be_deleted(client, monkeypatch):
    monkeypatch.setattr(builds, "_show", lambda _unit: None)
    monkeypatch.setattr(builds, "_tools", lambda: ("/usr/bin/systemd-run", "/usr/bin/systemctl", "/usr/bin/journalctl"))
    monkeypatch.setattr(
        builds.subprocess, "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )
    with SessionLocal() as db:
        admin = db.query(User).filter(User.username == "admin").one()
        project = ApplicationProject(name="Missing build root cleanup", created_by=admin.id)
        db.add(project)
        db.commit()
        db.refresh(project)
        row = ApplicationBuild(
            project_id=project.id, target_id="console", framework="csharp-console",
            status="failed", unit_name=f"pending-{os.getpid()}", created_by=admin.id,
        )
        db.add(row)
        db.flush()
        row.unit_name = f"control-deck-application-build-{row.id}"
        missing = application_builds_dir().resolve() / f"build-{row.id}"
        assert not missing.exists()
        row.build_root = str(missing)
        db.commit()
        row_id = row.id

        builds.delete_build(db, row)
        assert db.get(ApplicationBuild, row_id) is None
        db.delete(project)
        db.commit()


def test_build_routes_require_authentication(client):
    # The session-scoped TestClient is also used by authenticated tests. Clear
    # its cookie jar so this test proves the route dependency, independent of
    # test order.
    client.cookies.clear()
    csrf = {"X-Requested-With": "ControlDeck"}
    assert client.get("/api/v1/application-projects/1/builds").status_code == 401
    assert client.get("/api/v1/application-builds/1").status_code == 401
    assert client.post("/api/v1/application-builds/1/cancel", headers=csrf).status_code == 401
    assert client.delete("/api/v1/application-builds/1", headers=csrf).status_code == 401


def test_build_api_lifecycle_artifact_and_audit(admin_client, monkeypatch):
    owner = application_builds_dir().resolve()
    fake_dotnet = owner / "api-test-sdk" / "dotnet"
    fake_dotnet.parent.mkdir(mode=0o700, exist_ok=True)
    fake_dotnet.write_text("", encoding="utf-8")
    fake_dotnet.chmod(0o700)

    def fake_run(argv, **kwargs):
        if argv and argv[0] == "/usr/bin/journalctl":
            return subprocess.CompletedProcess(argv, 0, stdout=b"restore ok\nbuild ok\ntest ok\n", stderr=b"")
        text = kwargs.get("text") is True
        return subprocess.CompletedProcess(argv, 0, stdout="" if text else b"", stderr="" if text else b"")

    monkeypatch.setattr(builds, "dotnet_sdk_path", lambda: fake_dotnet.resolve())
    monkeypatch.setattr(builds, "_tools", lambda: ("/usr/bin/systemd-run", "/usr/bin/systemctl", "/usr/bin/journalctl"))
    monkeypatch.setattr(builds.subprocess, "run", fake_run)
    monkeypatch.setattr(builds, "_show", lambda _unit: {
        "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
    })

    definition = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "config": {"mode": "manual"}},
            {"id": "output", "type": "output.render", "config": {"name": "answer", "value": "ok"}},
        ],
        "edges": [{"source": "trigger", "target": "output"}],
    }
    workflow = admin_client.post(
        "/api/v1/workflows", json={"name": "Build API lifecycle", "definition": definition}, headers=CSRF_HEADERS,
    )
    assert workflow.status_code == 201
    workflow_id = workflow.json()["id"]
    created = admin_client.post(
        f"/api/v1/workflows/{workflow_id}/application-projects",
        json={"source": "draft", "name": "Build API App"}, headers=CSRF_HEADERS,
    )
    assert created.status_code == 201
    project = created.json()
    spec = project["spec"]
    spec["targets"] = [{"id": "console", "platforms": ["linux", "windows"], "framework": "csharp-console"}]
    updated = admin_client.patch(
        f"/api/v1/application-projects/{project['id']}", json={"spec": spec}, headers=CSRF_HEADERS,
    )
    assert updated.status_code == 200, updated.text

    started = admin_client.post(
        f"/api/v1/application-projects/{project['id']}/builds",
        json={"targetId": "console", "timeoutSeconds": 600}, headers=CSRF_HEADERS,
    )
    assert started.status_code == 202, started.text
    payload = started.json()
    build_id = payload["id"]
    assert payload["status"] in builds.ACTIVE_STATES
    assert payload["isolation"] == {
        "systemdUser": True, "network": "denied", "memoryMax": "2G", "tasksMax": 128, "cpuQuota": "200%",
    }
    listed = admin_client.get(f"/api/v1/application-projects/{project['id']}/builds")
    assert listed.status_code == 200 and listed.json()[0]["id"] == build_id
    logs = admin_client.get(f"/api/v1/application-builds/{build_id}/logs")
    assert logs.status_code == 200 and "build ok" in logs.json()["logs"]

    cancelled = admin_client.post(f"/api/v1/application-builds/{build_id}/cancel", headers=CSRF_HEADERS)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    source_artifact = next(item for item in cancelled.json()["artifacts"] if item["kind"] == "source")
    downloaded = admin_client.get(f"/api/v1/application-builds/{build_id}/artifacts/{source_artifact['id']}")
    assert downloaded.status_code == 200 and downloaded.content.startswith(b"PK")
    assert downloaded.headers["x-content-type-options"] == "nosniff"

    deleted = admin_client.delete(f"/api/v1/application-builds/{build_id}", headers=CSRF_HEADERS)
    assert deleted.status_code == 204
    assert admin_client.get(f"/api/v1/application-builds/{build_id}").status_code == 404
    with SessionLocal() as db:
        actions = [item.action for item in db.query(AuditLog).filter(
            AuditLog.resource_id == str(build_id),
            AuditLog.action.in_([
                "application_build.start", "application_build.cancel",
                "application_build.artifact_download", "application_build.delete",
            ]),
        ).all()]
        assert set(actions) == {
            "application_build.start", "application_build.cancel",
            "application_build.artifact_download", "application_build.delete",
        }
    assert admin_client.delete(f"/api/v1/application-projects/{project['id']}", headers=CSRF_HEADERS).status_code == 204
    assert admin_client.delete(f"/api/v1/workflows/{workflow_id}", headers=CSRF_HEADERS).status_code == 200
    fake_dotnet.unlink(missing_ok=True)
    fake_dotnet.parent.rmdir()
