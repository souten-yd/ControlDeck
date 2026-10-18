from pathlib import Path

import pytest

from app.features import registry, release_bundle
from tests.test_release_bundle_feature import bundle_bytes, prepare, spec


@pytest.fixture
def installed(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path, "1.2.3", bundle_bytes("1.2.3"))
    release_bundle.install("fake-addon", spec())
    definition = {
        "name": "Fake", "kind": "release-bundle", "route_gated": True,
        "summary": "Fake", **spec(),
    }
    monkeypatch.setattr(registry, "FEATURES", {"fake-addon": definition})
    monkeypatch.setattr(registry, "KNOWN_FEATURES", {"fake-addon"})
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    addon = {"enabled": True, "granted_capabilities": ["theme.read"]}
    monkeypatch.setattr(release_bundle.addon_registry, "status", lambda _id: dict(addon))
    state = {"status": "STOPPED"}
    monkeypatch.setattr(release_bundle.systemd, "query_status", lambda _name: dict(state))
    monkeypatch.setattr(release_bundle, "health", lambda *_args: (
        state["status"] == "RUNNING", "" if state["status"] == "RUNNING" else "service is stopped",
    ))
    calls = []

    def start(name):
        calls.append(name)
        state["status"] = "RUNNING"
        return True, ""

    monkeypatch.setattr(release_bundle.systemd, "start", start)

    def forbidden(*_args, **_kwargs):
        pytest.fail("restoration must not download, provision, restart, or re-grant")

    monkeypatch.setattr(release_bundle, "install", forbidden)
    monkeypatch.setattr(release_bundle, "_run_lifecycle", forbidden)
    monkeypatch.setattr(release_bundle.systemd, "restart", forbidden)
    monkeypatch.setattr(release_bundle.addon_registry, "set_enabled", forbidden)
    return state, addon, calls


@pytest.mark.parametrize("initial_status", ["STOPPED", "UNKNOWN", "FAILED"])
def test_boot_restores_enabled_installation_once_without_changing_data(installed, tmp_path, initial_status):
    state, addon, calls = installed
    state["status"] = initial_status
    root = tmp_path / "data" / "features" / "fake-addon"
    selected = (root / "current").resolve()
    marker = tmp_path / "data" / "feature-data" / "fake-addon" / "existing-data"
    marker.write_text("keep")
    registry.restore_enabled_services()
    assert calls == ["cdapp-feature-fake-addon.service"]
    assert state["status"] == "RUNNING"
    assert registry.status("fake-addon")["enabled"] is True
    assert (root / "current").resolve() == selected
    assert marker.read_text() == "keep"
    assert addon["granted_capabilities"] == ["theme.read"]
    registry.restore_enabled_services()
    assert len(calls) == 1


def test_boot_respects_addon_opt_out_even_with_stale_feature_flag(installed):
    state, addon, calls = installed
    registry._write_state({"fake-addon": {"enabled": True}})
    addon["enabled"] = False
    registry.restore_enabled_services()
    assert calls == []
    assert state["status"] == "STOPPED"


def test_missing_package_is_not_reinstalled(installed, tmp_path):
    (tmp_path / "data" / "features" / "fake-addon" / "current").unlink()
    registry.restore_enabled_services()
    assert installed[2] == []


def test_running_service_is_not_rewritten_or_restarted(installed, monkeypatch):
    installed[0]["status"] = "RUNNING"
    monkeypatch.setattr(release_bundle, "_write_service", lambda *_args: pytest.fail("running unit rewritten"))
    registry.restore_enabled_services()
    assert installed[2] == []


def test_enable_repairs_stopped_service_and_preserves_existing_grants(installed):
    assert registry.status("fake-addon")["health"] == "error"
    assert registry.enable("fake-addon")["enabled"] is True
    assert installed[2] == ["cdapp-feature-fake-addon.service"]


def test_start_failure_is_reported_without_changing_desired_state(installed, monkeypatch):
    monkeypatch.setattr(release_bundle.systemd, "start", lambda _name: (False, "start refused"))
    with pytest.raises(registry.FeatureError, match="start refused"):
        registry.enable("fake-addon")
    assert registry._read_state() == {}
    assert installed[1]["enabled"] is True


def test_health_failure_is_not_misreported_as_repaired(installed, monkeypatch):
    def fail(*_args):
        raise release_bundle.ReleaseBundleError("unhealthy")

    monkeypatch.setattr(release_bundle, "_wait_health", fail)
    with pytest.raises(registry.FeatureError, match="unhealthy"):
        registry.enable("fake-addon")


def test_invalid_installed_entrypoint_is_never_started(installed, tmp_path, caplog):
    root = tmp_path / "data" / "features" / "fake-addon" / "current"
    (root / "run.sh").unlink()
    (root / "run.sh").symlink_to("/usr/bin/true")
    registry.restore_enabled_services()
    assert installed[2] == []
    assert "package entrypoint" in caplog.text


def test_one_failed_feature_does_not_block_others(monkeypatch, caplog):
    monkeypatch.setattr(registry, "FEATURES", {
        name: {"kind": "release-bundle", "addon_id": name}
        for name in ["broken", "working"]
    })
    monkeypatch.setattr(release_bundle, "current", lambda _id: (object(), Path("/unused")))
    monkeypatch.setattr(release_bundle.addon_registry, "status", lambda _id: {"enabled": True})
    calls = []

    def restore(name, _spec):
        calls.append(name)
        if name == "broken":
            raise release_bundle.ReleaseBundleError("broken package")

    monkeypatch.setattr(release_bundle, "ensure_running", restore)
    registry.restore_enabled_services()
    assert calls == ["broken", "working"]
    assert "broken package" in caplog.text
