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


def test_update_installs_latest_and_reports_previous_version(monkeypatch, tmp_path):
    from app.features import registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    binary = tmp_path / "data" / "features" / "opencode" / "node_modules" / ".bin" / "opencode"
    versions = ["1.18.3"]
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "/usr/bin/npm":
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)
            if argv[-1].endswith("@latest"):
                versions.append("1.18.16")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout=versions[-1] + "\n", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", run)
    registry.install("opencode")
    calls.clear()
    updated = registry.update("opencode")
    npm_calls = [argv for argv in calls if argv[0] == "/usr/bin/npm"]
    assert npm_calls == [[
        "/usr/bin/npm", "install", "--prefix", str(tmp_path / "data" / "features" / "opencode"),
        "--no-fund", "--no-audit", "opencode-ai@latest",
    ]]
    assert updated["previous_version"] == "1.18.3" and updated["version"] == "1.18.16"


def test_update_rejects_external_only_install(monkeypatch, tmp_path):
    import pytest

    from app.features import registry

    external = tmp_path / "bin" / "opencode"
    external.parent.mkdir()
    external.write_text("#!/bin/sh\necho 1.2.3\n", encoding="utf-8")
    external.chmod(0o755)
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(
        registry.shutil, "which",
        lambda name: str(external) if name == "opencode" else "/usr/bin/npm" if name == "npm" else None,
    )
    with pytest.raises(registry.FeatureError):
        registry.update("opencode")
    assert external.read_text(encoding="utf-8").strip().endswith("1.2.3")


def test_update_job_endpoint_requires_managed_install(admin_client, monkeypatch):
    from app.features import registry, router as features_router

    assert admin_client.post(
        "/api/v1/features/unknown/update-jobs", json={}, headers={"X-Requested-With": "ControlDeck"},
    ).status_code == 404
    assert admin_client.post(
        "/api/v1/features/opencode/update-jobs", json={}, headers={"X-Requested-With": "ControlDeck"},
    ).status_code == 422  # 未導入（managed=False）

    base = registry.status("opencode")
    monkeypatch.setattr(features_router.registry, "status", lambda feature_id: {**base, "managed": True})
    monkeypatch.setattr(features_router.registry, "update", lambda feature_id: {**base, "previous_version": "1.0.0"})
    started = admin_client.post(
        "/api/v1/features/opencode/update-jobs", json={}, headers={"X-Requested-With": "ControlDeck"},
    )
    assert started.status_code == 201 and started.json()["job_id"]


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


def test_runtime_config_lets_opencode_send_images_and_roam_codedev(monkeypatch, tmp_path):
    """VLM を載せていても宣言が無いと画像は送られない。CodeDEV は毎回聞かない。"""
    import json as _json

    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path)
    monkeypatch.setattr(provider, "codedev_root", lambda: tmp_path / "CodeDEV")
    path = provider._runtime_config("caps", "http://127.0.0.1:8090/v1", "auto")
    payload = _json.loads(path.read_text(encoding="utf-8"))

    model = payload["provider"]["controldeck"]["models"]["auto"]
    # attachment だけでは足りない。modalities.input に image が無いと OpenCode は
    # 画像を text へ落として送り、モデルは「画像入力に対応していない」と答える。
    assert model["attachment"] is True
    assert "image" in model["modalities"]["input"]
    assert "text" in model["modalities"]["input"]

    allowed = payload["permission"]["external_directory"]
    root = tmp_path / "CodeDEV"
    assert allowed[f"{root}/*"] == "allow"
    assert allowed[f"{root}/**"] == "allow"
    # ターミナルから送った画像はパスで渡すので、置き場も開いていないと読めない
    from app.terminals import attachments

    assert allowed[f"{attachments.store.root}/*"] == "allow"
    # 全部開けてしまっていないこと
    assert not any(key in ("*", "**", "/*") for key in allowed)


def test_addon_assets_are_readable_without_asking_every_time(monkeypatch, tmp_path):
    """生成物の置き場は開ける。閉じていると生成のたびに確認が入る。

    開けるのは assets の下だけ。feature-data ごと開けると、モデルの重みや
    実行状態まで読めてしまう。
    """
    from app.integrations.opencode import provider

    base = tmp_path / "feature-data"
    (base / "media-forge" / "data" / "assets").mkdir(parents=True)
    (base / "sonic-forge" / "assets").mkdir(parents=True)
    (base / "media-forge" / "runtimes" / "rocm-torch").mkdir(parents=True)
    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("app.config.data_dir", lambda: tmp_path)

    roots = {str(item) for item in provider._addon_asset_roots()}
    assert str(base / "media-forge" / "data" / "assets") in roots
    assert str(base / "sonic-forge" / "assets") in roots
    # 重みや実行状態は開けない
    assert not any("runtimes" in item for item in roots)
