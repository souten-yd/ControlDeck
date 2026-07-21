import json
from pathlib import Path

import pytest
from pydantic import ValidationError


def manifest(plugin_id: str = "example-gui") -> dict:
    return {
        "api_version": "1",
        "id": plugin_id,
        "name": "Example GUI",
        "version": "1.2.3",
        "description": "A separately hosted GUI",
        "publisher": "Example",
        "capabilities": ["navigation"],
        "navigation": {"label": "Example", "url": "http://127.0.0.1:9010/", "permission": "apps.view"},
    }


def test_manifest_rejects_unknown_fields_permissions_and_unsafe_urls():
    from app.plugins.schema import PluginManifest

    for patch in (
        {"unexpected": True},
        {"navigation": {"label": "X", "url": "javascript:alert(1)", "permission": "apps.view"}},
        {"navigation": {"label": "X", "url": "http://example.com", "permission": "apps.view"}},
        {"navigation": {"label": "X", "url": "/ok", "permission": "not.real"}},
    ):
        value = manifest()
        value.update(patch)
        with pytest.raises(ValidationError):
            PluginManifest.model_validate(value)


def test_registry_install_enable_and_uninstall_are_confined(monkeypatch, tmp_path):
    from app.plugins import registry
    from app.plugins.schema import PluginManifest

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    installed = registry.install(PluginManifest.model_validate(manifest()))
    path = tmp_path / "data" / "plugins" / "example-gui" / registry.MANIFEST_NAME
    assert installed["enabled"] is False and path.stat().st_mode & 0o777 == 0o600
    assert registry.set_enabled("example-gui", True)["enabled"] is True
    assert registry.enabled_navigation() == [
        {"id": "example-gui", "label": "Example", "url": "http://127.0.0.1:9010/", "permission": "apps.view"}
    ]
    removed = registry.uninstall("example-gui")
    assert removed["installed"] is False and not path.exists()


def test_registry_ignores_symlinked_plugin_directory(monkeypatch, tmp_path):
    from app.plugins import registry

    root = tmp_path / "data" / "plugins"
    outside = tmp_path / "outside"
    root.mkdir(parents=True)
    root.chmod(0o700)
    outside.mkdir()
    (outside / registry.MANIFEST_NAME).write_text(json.dumps(manifest("escape")), encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    assert registry.list_plugins() == []


def test_registry_rejects_symlink_to_another_managed_directory(monkeypatch, tmp_path):
    from app.plugins import registry
    from app.plugins.schema import PluginManifest

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    registry.install(PluginManifest.model_validate(manifest("real-plugin")))
    root = tmp_path / "data" / "plugins"
    (root / "alias-plugin").symlink_to(root / "real-plugin", target_is_directory=True)
    with pytest.raises(registry.PluginError, match="symlink"):
        registry.status("alias-plugin")


def test_manifest_file_requires_safe_regular_user_owned_file(monkeypatch, tmp_path):
    from app.plugins import registry

    source = tmp_path / "plugin.json"
    source.write_text(json.dumps(manifest()), encoding="utf-8")
    source.chmod(0o666)
    with pytest.raises(registry.PluginError, match="otherから書込み可能"):
        registry.validate_file(source)


def test_plugin_api_requires_admin_and_audits(admin_client, monkeypatch, tmp_path):
    from app.plugins import registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "plugin-data")
    created = admin_client.post("/api/v1/plugins", json=manifest(), headers={"X-Requested-With": "ControlDeck"})
    assert created.status_code == 201
    enabled = admin_client.post("/api/v1/plugins/example-gui/enable", headers={"X-Requested-With": "ControlDeck"})
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True
    assert admin_client.get("/api/v1/meta").json()["plugin_navigation"][0]["id"] == "example-gui"
    audits = admin_client.get("/api/v1/audit").json()
    assert any(item["action"] == "plugin.install" and item["resource_id"] == "example-gui" for item in audits)


def test_plugin_api_rejects_csrf(admin_client):
    response = admin_client.post("/api/v1/plugins", json=manifest("csrf-test"))
    assert response.status_code == 403
