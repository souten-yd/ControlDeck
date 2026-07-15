import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace


def test_feature_default_is_disabled_and_external_uninstall_is_preserved(monkeypatch, tmp_path):
    from app.features import registry

    external = tmp_path / "bin" / "opencode"
    external.parent.mkdir()
    external.write_text("#!/bin/sh\necho 1.2.3\n", encoding="utf-8")
    external.chmod(0o755)
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")

    def which(name):
        if name == "opencode":
            return str(external)
        if name == "npm":
            return "/usr/bin/npm"
        return None

    monkeypatch.setattr(registry.shutil, "which", which)
    current = registry.status("opencode")
    assert current["installed"] is True and current["managed"] is False and current["enabled"] is False
    registry.enable("opencode")
    assert registry.status("opencode")["enabled"] is True
    # user serviceのPATHが対話shellと異なっても、enable時の実体を再利用する。
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    assert registry.status("opencode")["enabled"] is True
    after = registry.uninstall("opencode")
    assert external.exists() and after["installed"] is True and after["enabled"] is False


def test_managed_install_uses_private_prefix_and_does_not_enable(monkeypatch, tmp_path):
    from app.features import registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        binary = tmp_path / "data" / "features" / "opencode" / "node_modules" / ".bin" / "opencode"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho 9.9.9\n", encoding="utf-8")
        binary.chmod(0o755)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", run)
    installed = registry.install("opencode")
    assert calls[0][:4] == ["/usr/bin/npm", "install", "--prefix", str(tmp_path / "data" / "features" / "opencode")]
    assert installed["managed"] is True and installed["enabled"] is False


def test_disabled_feature_has_no_router_or_workflow_node(admin_client):
    from app.workflows.catalog import valid_types
    from app.workflows.nodes import NODE_EXECUTORS

    assert admin_client.get("/api/v1/opencode/status").status_code == 404
    assert "code.agent" not in valid_types()
    assert "code.agent" not in NODE_EXECUTORS
    meta = admin_client.get("/api/v1/meta").json()
    assert "opencode" not in meta["enabled_features"]


def test_opencode_provider_builds_array_argv_and_parses_json(monkeypatch, tmp_path):
    from app.features import registry
    from app.integrations.opencode import provider as op
    from app.jobs.service import Job

    project = tmp_path / "project"
    project.mkdir()
    binary = tmp_path / "opencode"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: True)
    monkeypatch.setattr(registry, "executable", lambda feature_id: binary)
    monkeypatch.setattr(op.files, "resolve", lambda path: project.resolve())
    monkeypatch.setattr(op.shutil, "which", lambda name: f"/usr/bin/{name}")
    captured = []

    class Process:
        returncode = 0

        async def communicate(self):
            return (b'{"type":"text","text":"analysis complete"}\n', b"")

        async def wait(self):
            return 0

    async def spawn(*argv, **kwargs):
        captured.append((argv, kwargs))
        return Process()

    monkeypatch.setattr(op.asyncio, "create_subprocess_exec", spawn)
    job = Job(id="safe-job-1", kind="opencode.run", title="test")
    result = asyncio.run(op.provider.run(
        job, operation="analyze", project_path=str(project), instruction="check this",
        base_url="http://127.0.0.1:8090/v1", model="llama",
    ))
    argv = captured[0][0]
    assert "--working-directory=" + str(project.resolve()) in argv
    assert "--file" in argv and "check this" not in argv
    assert result["output"] == "analysis complete"
    assert not list((tmp_path / "data" / "integrations" / "opencode").glob("prompt-*.txt"))
    assert not list((tmp_path / "data" / "integrations" / "opencode").glob("runtime-config-*.json"))


def test_opencode_provider_stops_transient_unit_when_cancelled(monkeypatch, tmp_path):
    from app.features import registry
    from app.integrations.opencode import provider as op
    from app.jobs.service import Job

    project = tmp_path / "project"
    project.mkdir()
    binary = tmp_path / "opencode"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: True)
    monkeypatch.setattr(registry, "executable", lambda feature_id: binary)
    monkeypatch.setattr(op.files, "resolve", lambda path: project.resolve())
    monkeypatch.setattr(op.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    class RunningProcess:
        returncode = None

        async def communicate(self):
            await asyncio.Future()

    class StopProcess:
        async def wait(self):
            return 0

    async def spawn(*argv, **kwargs):
        calls.append(argv)
        return RunningProcess() if len(calls) == 1 else StopProcess()

    monkeypatch.setattr(op.asyncio, "create_subprocess_exec", spawn)

    async def scenario():
        job = Job(id="cancel-job-1", kind="opencode.run", title="test")
        task = asyncio.create_task(op.provider.run(
            job, operation="analyze", project_path=str(project), instruction="wait",
        ))
        while not calls:
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert calls[1] == ("/usr/bin/systemctl", "--user", "stop", "cdfeature-opencode-cancel-job-1.service")
    integration = tmp_path / "data" / "integrations" / "opencode"
    assert not list(integration.glob("prompt-*.txt"))
    assert not list(integration.glob("runtime-config-*.json"))


def test_project_symlink_escape_is_rejected(monkeypatch, tmp_path):
    from app.features import registry
    from app.integrations.opencode import provider as op
    from app.jobs.service import Job

    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: True)
    monkeypatch.setattr(op.files, "resolve", lambda path: (_ for _ in ()).throw(op.files.FileAccessError("outside")))
    job = Job(id="escape", kind="opencode.run", title="test")
    try:
        asyncio.run(op.provider.run(job, operation="analyze", project_path="/escape", instruction="x"))
        assert False, "CodeAgentError expected"
    except op.CodeAgentError as exc:
        assert "outside" in str(exc)
