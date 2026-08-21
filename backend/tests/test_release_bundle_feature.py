from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_addon_contract import addon_manifest


def bundle_bytes(version: str, *, unsafe: bool = False) -> bytes:
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
    monkeypatch.setattr(release_bundle.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
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
    monkeypatch.setattr(release_bundle, "health", lambda *_args: (False, "service is stopped"))
    state = registry.status("fake-addon")
    assert state["installed"] is True
    assert state["health"] == "error"
    assert state["enabled"] is False
    assert state["error"] == "service is stopped"


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
