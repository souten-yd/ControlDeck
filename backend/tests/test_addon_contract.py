from __future__ import annotations

import pytest
from pydantic import ValidationError


def addon_manifest() -> dict:
    return {
        "api_version": "2",
        "id": "fake-addon",
        "name": "Fake Add-on",
        "version": "1.0.0",
        "publisher": "Control Deck",
        "requires": {"addon_contract": ">=2.0 <3.0"},
        "runtime": {"kind": "external-service", "base_url": "http://127.0.0.1:9130", "health_path": "/health"},
        "host_capabilities": ["theme.read", "jobs.write", "resources.acquire"],
        "contributions": {
            "navigation": [{"id": "workspace", "label": {"en": "Lab", "ja": "ラボ"}, "route": "/x/fake-addon/workspace"}],
            "embedded_views": [{"id": "workspace", "label": "Workspace", "route": "/x/fake-addon/workspace", "path": "/"}],
            "commands": [{"id": "generate", "label": "Generate", "endpoint": "/commands/generate"}],
            "quick_actions": [{"id": "quick-generate", "label": "Quick generate", "endpoint": "/commands/generate"}],
            "settings": [{"id": "settings", "label": "Settings", "route": "/x/fake-addon/settings"}],
            "workflow_executors": [{
                "id": "fake.generate", "label": "Generate", "endpoint": "/workflow/execute",
                "input_schema_path": "/schemas/workflow-input", "output_schema_path": "/schemas/workflow-output",
            }],
            "agent_tools": [{"id": "fake.generate", "label": "Generate", "endpoint": "/agent/execute", "schema_path": "/schemas/agent-tool"}],
            "context_actions": [{"id": "fake.inspect", "label": "Inspect", "endpoint": "/context/inspect", "contexts": ["file", "project"]}],
            "setup_checklist": [{"id": "setup", "label": "Setup"}],
        },
    }


def test_dispatch_keeps_v1_contract_compatible():
    from app.addons.schema import PluginManifestV1, parse_manifest
    from app.plugins.schema import PluginManifest

    value = {
        "api_version": "1", "id": "example-gui", "name": "Example GUI", "version": "1.2.3",
        "publisher": "Example", "capabilities": ["navigation"],
        "navigation": {"label": "Example", "url": "http://127.0.0.1:9010/", "permission": "apps.view"},
    }
    parsed = parse_manifest(value)
    assert isinstance(parsed.manifest, PluginManifestV1)
    assert PluginManifest is PluginManifestV1
    assert parsed.warnings == ()


def test_v2_manifest_dispatch_and_contract_range():
    from app.addons.schema import AddonManifestV2, parse_manifest

    parsed = parse_manifest(addon_manifest())
    assert isinstance(parsed.manifest, AddonManifestV2)
    assert parsed.manifest.runtime.health_path == "/health"
    assert parsed.manifest.contributions.embedded_views[0].mobile == "companion"

    incompatible = addon_manifest()
    incompatible["requires"] = {"addon_contract": ">=3.0 <4.0"}
    with pytest.raises(ValueError, match="互換性がありません"):
        parse_manifest(incompatible)


@pytest.mark.parametrize("patch", [
    {"api_version": "3"},
    {"host_capabilities": ["host.everything"]},
    {"contributions": {"arbitrary_python": []}},
    {"runtime": {"kind": "external-service", "base_url": "http://example.com", "health_path": "/health"}},
])
def test_v2_manifest_rejects_unknown_execution_contracts_and_unsafe_urls(patch):
    from app.addons.schema import parse_manifest

    value = addon_manifest()
    value.update(patch)
    with pytest.raises((ValueError, ValidationError)):
        parse_manifest(value)


def test_v2_unknown_presentational_field_is_ignored_with_warning():
    from app.addons.schema import parse_manifest

    value = addon_manifest()
    value["contributions"]["commands"][0]["badge"] = "new-host-field"
    parsed = parse_manifest(value)
    assert parsed.manifest.contributions.commands[0].model_dump() == {
        "id": "generate", "label": "Generate", "permission": "apps.view", "endpoint": "/commands/generate"
    }
    assert parsed.warnings == (
        "contributions.commands[0].badge: このhostでは未対応の表示fieldを無視しました",
    )
