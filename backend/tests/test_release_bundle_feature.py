from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_addon_contract import addon_manifest


def bundle_bytes(version: str, *, unsafe: bool = False, provision: bool = False) -> bytes:
    addon = addon_manifest()
    addon["version"] = version
    package = {
        "schema_version": 1,
        "feature_id": "fake-addon",
        "version": version,
        "platform": "linux",
        "architecture": "x86_64",
        "entrypoint": "run.sh",
        "addon_manifest": "control-deck-addon.json",
        "provision_args": ["provision"] if provision else [],
        "smoke_args": ["doctor"],
        "service_args": ["serve"],
        "health_url": "http://127.0.0.1:9130/health",
    }
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content, mode in (
            ("fake-addon/control-deck-feature.json", json.dumps(package).encode(), 0o644),
            ("fake-addon/control-deck-addon.json", json.dumps(addon).encode(), 0o644),
            ("fake-addon/run.sh", b"#!/bin/sh\nexit 0\n", 0o755),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            archive.addfile(info, io.BytesIO(content))
        if unsafe:
            info = tarfile.TarInfo("../escape")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    return output.getvalue()


def spec() -> dict:
    return {
        "addon_id": "fake-addon",
        "artifact_name": "fake-{version}-{platform}-{arch}.tar.gz",
        "max_download_bytes": 10_000_000,
        "max_expanded_bytes": 10_000_000,
        "smoke_timeout_sec": 10,
        "allowed_host_capabilities": ["theme.read", "jobs.write", "resources.acquire"],
    }


def prepare(monkeypatch, tmp_path: Path, version: str, content: bytes) -> list[str]:
    from app.features import release_bundle

    monkeypatch.setattr(release_bundle, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(release_bundle, "_metadata", lambda _spec: {"tag_name": version, "draft": False, "assets": []})
    asset = {"name": f"fake-{version}-linux-x86_64.tar.gz"}
    checksum = {"name": asset["name"] + ".sha256"}
    monkeypatch.setattr(release_bundle, "_select_release", lambda _spec, _meta: (version, asset, checksum))
    monkeypatch.setattr(release_bundle, "_download", lambda _spec, _asset, path: path.write_bytes(content))
    monkeypatch.setattr(release_bundle, "_expected_sha", lambda _spec, _sum, _name: hashlib.sha256(content).hexdigest())
    def smoke(*_args, **kwargs):
        environment = kwargs["env"]
        assert environment["CONTROL_DECK_FEATURE_DATA_DIR"] == str(tmp_path / "data" / "feature-data" / "fake-addon")
        assert environment["CONTROL_DECK_SHARED_CACHE_DIR"] == str(tmp_path / "data" / "cache")
        assert Path(environment["CONTROL_DECK_FEATURE_ROOT"]).is_dir()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release_bundle.subprocess, "run", smoke)
    monkeypatch.setattr(release_bundle, "_wait_health", lambda *a, **k: None)
    monkeypatch.setattr(release_bundle.systemd, "write_unit", lambda name, body: tmp_path / name)
    monkeypatch.setattr(release_bundle.systemd, "restart", lambda name: (True, ""))
    monkeypatch.setattr(release_bundle.systemd, "stop", lambda name: (True, ""))
    monkeypatch.setattr(release_bundle.systemd, "query_status", lambda name: {"status": "RUNNING"})
    installed: list[str] = []
    monkeypatch.setattr(release_bundle.addon_registry, "install", lambda parsed: installed.append(parsed.manifest.version))
    return installed


def test_release_bundle_installs_side_by_side_and_switches_atomically(monkeypatch, tmp_path):
    from app.features import release_bundle

    installed = prepare(monkeypatch, tmp_path, "1.2.3", bundle_bytes("1.2.3"))
    result = release_bundle.install("fake-addon", spec())
    root = tmp_path / "data" / "features" / "fake-addon"
    assert (root / "current").resolve() == (root / "versions" / "1.2.3").resolve()
    assert (root / "downloads" / "fake-1.2.3-linux-x86_64.tar.gz").is_file()
    assert result == {"version": "1.2.3", "previous_version": ""}
    assert installed == ["1.2.3"]


def test_failed_new_health_rolls_back_current_and_addon(monkeypatch, tmp_path):
    from app.features import release_bundle

    installed = prepare(monkeypatch, tmp_path, "1.0.0", bundle_bytes("1.0.0"))
    release_bundle.install("fake-addon", spec())
    prepare(monkeypatch, tmp_path, "2.0.0", bundle_bytes("2.0.0"))
    calls = 0

    def fail_new_health(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise release_bundle.ReleaseBundleError("unhealthy")

    monkeypatch.setattr(release_bundle, "_wait_health", fail_new_health)
    with pytest.raises(release_bundle.ReleaseBundleError, match="unhealthy"):
        release_bundle.install("fake-addon", spec())
    root = tmp_path / "data" / "features" / "fake-addon"
    assert (root / "current").resolve() == (root / "versions" / "1.0.0").resolve()
    assert installed[-1] == "1.0.0" and calls == 1


def test_unsafe_archive_never_changes_current(monkeypatch, tmp_path):
    from app.features import release_bundle

    prepare(monkeypatch, tmp_path, "1.0.0", bundle_bytes("1.0.0"))
    release_bundle.install("fake-addon", spec())
    prepare(monkeypatch, tmp_path, "2.0.0", bundle_bytes("2.0.0", unsafe=True))
    with pytest.raises(release_bundle.ReleaseBundleError, match="unsafe path"):
        release_bundle.install("fake-addon", spec())
    root = tmp_path / "data" / "features" / "fake-addon"
    assert (root / "current").resolve() == (root / "versions" / "1.0.0").resolve()
    assert not (tmp_path / "data" / "features" / "escape").exists()
    assert list((root / "downloads").glob("*.partial")) == []


def test_same_version_reinstall_repairs_service_and_addon(monkeypatch, tmp_path):
    from app.features import release_bundle

    installed = prepare(monkeypatch, tmp_path, "1.2.3", bundle_bytes("1.2.3"))
    release_bundle.install("fake-addon", spec())
    selected = tmp_path / "data" / "features" / "fake-addon" / "versions" / "1.2.3"
    marker = selected / "keep-me"
    marker.write_text("immutable", encoding="utf-8")
    result = release_bundle.install("fake-addon", spec())
    assert marker.read_text(encoding="utf-8") == "immutable"
    assert installed == ["1.2.3", "1.2.3"]
    assert result == {"version": "1.2.3", "previous_version": "1.2.3"}


def test_provision_runs_before_smoke_with_the_managed_environment(monkeypatch, tmp_path):
    from app.features import release_bundle

    calls: list[tuple[list[str], dict[str, str]]] = []
    prepare(monkeypatch, tmp_path, "1.2.3", bundle_bytes("1.2.3", provision=True))

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release_bundle.subprocess, "run", run)
    release_bundle.install("fake-addon", {**spec(), "provision_timeout_sec": 60})
    assert [call[0][-1] for call in calls] == ["provision", "doctor"]
    assert all(call[1]["CONTROL_DECK_FEATURE_DATA_DIR"].endswith("/feature-data/fake-addon") for call in calls)


def test_lifecycle_environment_does_not_inherit_host_secrets(monkeypatch, tmp_path):
    from app.features import release_bundle

    monkeypatch.setenv("CONTROL_DECK_SERVICE_TOKEN", "must-not-leak")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    monkeypatch.setenv("PATH", "/usr/bin")
    environment = release_bundle._lifecycle_environment("fake-addon", tmp_path)
    assert environment["PATH"] == "/usr/bin"
    assert "CONTROL_DECK_SERVICE_TOKEN" not in environment
    assert "UNRELATED_SECRET" not in environment
    assert environment["CONTROL_DECK_FEATURE_DATA_DIR"].endswith("/feature-data/fake-addon")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (subprocess.TimeoutExpired(["run.sh", "provision"], 1), "provisioning timed out"),
        (OSError("exec failed"), "provisioning could not start"),
    ],
)
def test_lifecycle_errors_are_normalized(monkeypatch, tmp_path, error, message):
    from app.features import release_bundle

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(release_bundle.subprocess, "run", fail)
    with pytest.raises(release_bundle.ReleaseBundleError, match=message):
        release_bundle._run_lifecycle(
            "provisioning", tmp_path / "run.sh", ["provision"], cwd=tmp_path,
            environment={"PATH": "/usr/bin"}, timeout=1,
        )


def test_failed_provision_does_not_change_current(monkeypatch, tmp_path):
    from app.features import release_bundle

    prepare(monkeypatch, tmp_path, "1.0.0", bundle_bytes("1.0.0"))
    release_bundle.install("fake-addon", spec())
    prepare(monkeypatch, tmp_path, "2.0.0", bundle_bytes("2.0.0", provision=True))

    def fail_provision(argv, **_kwargs):
        return SimpleNamespace(returncode=1 if argv[-1] == "provision" else 0, stdout="", stderr="")

    monkeypatch.setattr(release_bundle.subprocess, "run", fail_provision)
    with pytest.raises(release_bundle.ReleaseBundleError, match="provisioning failed"):
        release_bundle.install("fake-addon", spec())
    root = tmp_path / "data" / "features" / "fake-addon"
    assert (root / "current").resolve() == (root / "versions" / "1.0.0").resolve()
    assert list((root / "downloads").glob("*.partial")) == []


def test_release_bundle_status_uses_live_service_health(monkeypatch, tmp_path):
    from app.features import registry, release_bundle

    prepare(monkeypatch, tmp_path, "1.2.3", bundle_bytes("1.2.3"))
    release_bundle.install("fake-addon", spec())
    monkeypatch.setitem(
        registry.FEATURES,
        "fake-addon",
        {"name": "Fake", "kind": "release-bundle", "route_gated": False, "summary": "Fake", **spec()},
    )
    monkeypatch.setattr(registry, "KNOWN_FEATURES", {*registry.KNOWN_FEATURES, "fake-addon"})
    monkeypatch.setattr(release_bundle.addon_registry, "status", lambda _addon_id: {"enabled": True})
    monkeypatch.setattr(release_bundle, "health", lambda *_args: (False, "service is stopped"))
    state = registry.status("fake-addon")
    assert state["installed"] is True
    assert state["health"] == "error"
    assert state["enabled"] is False
    assert state["error"] == "service is stopped"


def test_release_bundle_enable_disable_controls_addon_registry(monkeypatch, tmp_path):
    from app.features import registry, release_bundle

    prepare(monkeypatch, tmp_path, "1.2.3", bundle_bytes("1.2.3"))
    release_bundle.install("fake-addon", spec())
    monkeypatch.setitem(
        registry.FEATURES,
        "fake-addon",
        {"name": "Fake", "kind": "release-bundle", "route_gated": True, "summary": "Fake", **spec()},
    )
    monkeypatch.setattr(registry, "KNOWN_FEATURES", {*registry.KNOWN_FEATURES, "fake-addon"})
    monkeypatch.setattr(release_bundle, "health", lambda *_args: (True, ""))
    enabled = False

    def addon_status(_addon_id):
        return {"enabled": enabled}

    def set_enabled(_addon_id, value, grants=None):
        nonlocal enabled
        enabled = value
        return addon_status(_addon_id)

    monkeypatch.setattr(release_bundle.addon_registry, "status", addon_status)
    monkeypatch.setattr(release_bundle.addon_registry, "set_enabled", set_enabled)
    assert registry.status("fake-addon")["enabled"] is False
    assert registry.enable("fake-addon")["enabled"] is True
    assert registry.disable("fake-addon")["enabled"] is False


def test_catalog_url_rejects_untrusted_host():
    from app.features import release_bundle

    with pytest.raises(release_bundle.ReleaseBundleError, match="not trusted"):
        release_bundle._bounded_get("https://attacker.invalid/release", allowed_hosts={"api.github.com"}, limit=100)


def test_feature_job_api_rejects_caller_supplied_source(admin_client):
    headers = {"X-Requested-With": "ControlDeck"}
    for field, value in (
        ("url", "https://attacker.invalid/bundle.tgz"),
        ("repository", "attacker/repo"),
        ("command", ["sh", "-c", "id"]),
    ):
        response = admin_client.post(
            "/api/v1/features/media-forge/install-jobs",
            json={field: value},
            headers=headers,
        )
        assert response.status_code == 422
