from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_addon_contract import addon_manifest


@pytest.fixture()
def isolated_registry(monkeypatch, tmp_path):
    from app.addons import registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    registry.reset_runtime_state_for_tests()
    return registry


def _install(registry, value: dict | None = None):
    from app.addons.schema import parse_manifest

    return registry.install(parse_manifest(value or addon_manifest()))


def _health(status: str = "healthy", contributions: dict | None = None):
    from app.addons.schema import AddonHealthReport

    value = {"status": status, "contract_version": "2.0", "contributions": contributions or {}}
    if status != "healthy" and not contributions:
        value.update({"reason_code": "service_unreachable", "message": "not ready", "action": {"kind": "retry"}})
    return AddonHealthReport.model_validate(value)


def test_registry_install_enable_health_and_uninstall_are_confined(isolated_registry, tmp_path):
    registry = isolated_registry
    installed = _install(registry)
    manifest_path = tmp_path / "data" / "addons" / "fake-addon" / registry.MANIFEST_NAME
    assert installed["state"] == "installed_disabled"
    assert manifest_path.stat().st_mode & 0o777 == 0o600

    enabled = registry.set_enabled("fake-addon", True)
    assert enabled["state"] == "enabling"
    assert enabled["granted_capabilities"] == addon_manifest()["host_capabilities"]
    healthy = registry.update_health("fake-addon", _health())
    assert healthy["state"] == "healthy"

    removed = registry.uninstall("fake-addon")
    assert removed["state"] == "not_installed"
    assert not manifest_path.exists()


def test_registry_rejects_unknown_grants_and_symlinked_directory(isolated_registry, tmp_path):
    registry = isolated_registry
    _install(registry)
    with pytest.raises(registry.AddonRegistryError, match="要求していない"):
        registry.set_enabled("fake-addon", True, ["theme.read", "files.delete.everything"])

    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "data" / "addons"
    (root / "escape-addon").symlink_to(outside, target_is_directory=True)
    with pytest.raises(registry.AddonRegistryError, match="symlink"):
        registry.status("escape-addon")


def test_effective_registry_filters_permission_health_and_partial_availability(isolated_registry):
    registry = isolated_registry
    value = deepcopy(addon_manifest())
    value["contributions"]["settings"][0]["permission"] = "settings.manage"
    _install(registry, value)
    registry.set_enabled("fake-addon", True)

    unavailable_video = {
        "navigation:workspace": "available",
        "workflow_executor:fake.generate": "available",
        "workflow_executor:fake.video": {
            "state": "unavailable", "reason_code": "worker_not_installed", "message": "missing",
            "action": {"kind": "open_route", "route": "/x/fake-addon/settings"},
        },
    }
    registry.update_health("fake-addon", _health("degraded", unavailable_video))
    effective = registry.effective_for_permissions({"apps.view", "workflows.run"})
    assert [item["id"] for item in effective["contributions"]["navigation"]] == ["workspace"]
    assert [item["id"] for item in effective["contributions"]["workflow_executors"]] == ["fake.generate"]
    assert "settings" not in effective["contributions"]

    registry.update_health("fake-addon", _health("unavailable"))
    effective = registry.effective_for_permissions({"apps.view", "workflows.run"})
    assert effective["contributions"]["navigation"][0]["availability"] == "available"
    assert "workflow_executors" not in effective["contributions"]

    registry.set_enabled("fake-addon", False)
    assert registry.effective_for_permissions({"apps.view"})["addons"] == []


def test_effective_etag_and_revision_change_deterministically(isolated_registry):
    registry = isolated_registry
    _install(registry)
    before = registry.effective_for_permissions({"apps.view"})
    registry.set_enabled("fake-addon", True)
    after = registry.effective_for_permissions({"apps.view"})
    assert before["etag"] != after["etag"]
    assert after["revision"] > before["revision"]
    assert registry.wait_for_revision(after["revision"], timeout=0.001) == after["revision"]


def test_disable_pending_remains_effective_until_completion_and_can_be_canceled(isolated_registry):
    registry = isolated_registry
    _install(registry)
    registry.set_enabled("fake-addon", True)
    registry.update_health("fake-addon", _health())

    pending = registry.begin_disable("fake-addon")
    assert pending["enabled"] is True
    assert pending["state"] == "disable_pending"
    assert registry.effective_for_permissions({"apps.view"})["addons"][0]["state"] == "disable_pending"
    disabled = registry.complete_disable("fake-addon")
    assert disabled["enabled"] is False
    assert registry.effective_for_permissions({"apps.view"})["addons"] == []

    registry.set_enabled("fake-addon", True)
    registry.begin_disable("fake-addon")
    registry.set_enabled("fake-addon", True)
    assert registry.complete_disable("fake-addon")["enabled"] is True


def test_incompatible_managed_manifest_remains_visible_but_never_effective(isolated_registry, tmp_path):
    registry = isolated_registry
    _install(registry)
    path = tmp_path / "data" / "addons" / "fake-addon" / registry.MANIFEST_NAME
    path.write_text('{"api_version":"3"}', encoding="utf-8")
    item = registry.list_addons()[0]
    assert item["state"] == "incompatible"
    assert item["health"]["reason_code"] == "contract_incompatible"
    with pytest.raises(registry.AddonRegistryError, match="有効化できません"):
        registry.set_enabled("fake-addon", True)
    assert registry.effective_for_permissions({"apps.view"})["addons"] == []
    assert registry.uninstall("fake-addon")["state"] == "not_installed"
