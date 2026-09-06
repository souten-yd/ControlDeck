from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest

from app.skills import catalog, execution, registry


@pytest.fixture()
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(execution, "check", lambda entry: {"state": "ready", "message": "test"})
    def clone(entry: catalog.SkillEntry, target: Path) -> None:
        director = target / "skills/blender-director"
        director.mkdir(parents=True)
        (director / "SKILL.md").write_text("upstream execute_blender_code")
        (target / "UPSTREAM-LICENSE").write_text("MIT fixture")
    monkeypatch.setattr(registry, "_clone_git", clone)
    return tmp_path


def test_adapter_exposes_only_director_and_preserves_upstream(managed: Path) -> None:
    installed = registry.install("blender-skills")
    assert installed["effective"]
    root = managed / "skills/versions/blender-skills" / installed["installed_version"]
    paths = registry.enabled_paths()
    assert paths == [str(root / "runtime")]
    assert (root / "upstream/skills/blender-director/SKILL.md").read_text() == "upstream execute_blender_code"
    assert (root / "UPSTREAM-LICENSE").is_file()
    documents = list((root / "runtime").rglob("SKILL.md"))
    assert len(documents) == 1
    assert "name: blender-director" in documents[0].read_text()
    registry.set_enabled("blender-skills", False)
    assert registry.enabled_paths() == []
    registry.remove("blender-skills")
    assert not root.parent.exists()
    assert not (managed / ".claude").exists()
    assert not (managed / ".config/opencode").exists()


def test_failed_repair_keeps_files_state_and_disabled_choice(managed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = registry.install("blender-skills")
    registry.set_enabled("blender-skills", False)
    root = managed / "skills/versions/blender-skills" / value["installed_version"]
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    state = registry._state_path().read_bytes()
    def fail(value: dict) -> None:
        raise OSError("injected state publication failure")
    monkeypatch.setattr(registry, "_write_state", fail)
    with pytest.raises(OSError):
        registry.install("blender-skills")
    assert registry._state_path().read_bytes() == state
    assert {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()} == before
    assert registry.enabled_paths() == []


def test_old_unadapted_install_requires_update(managed: Path) -> None:
    root = managed / "skills/versions/blender-skills/2026.07.10"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("old")
    registry._write_state({"installed": {"blender-skills": {"version": "2026.07.10", "enabled": True}}})
    status = registry.status("blender-skills")
    assert status["installed"] and status["update_available"]
    assert status["execution"]["state"] == "update_required"
    assert registry.enabled_paths() == []


def test_missing_execution_is_not_exposed(managed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry.install("blender-skills")
    monkeypatch.setattr(execution, "check", lambda entry: {"state": "unavailable", "message": "missing runtime"})
    assert registry.status("blender-skills")["enabled"]
    assert not registry.status("blender-skills")["effective"]
    assert registry.enabled_paths() == []


def test_management_rejects_symlink_escape(managed: Path, tmp_path: Path) -> None:
    outside = tmp_path / "external"
    outside.mkdir()
    (managed / "skills").symlink_to(outside, target_is_directory=True)
    with pytest.raises(registry.SkillError):
        registry.install("blender-skills")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("state,version,local,expected", [
    ("available", "media-forge.scene-recipe@1", True, "ready"),
    ("unavailable", "media-forge.scene-recipe@1", True, "unavailable"),
    ("available", "unknown", True, "unavailable"),
    ("available", "media-forge.scene-recipe@1", False, "unavailable"),
])
def test_execution_checks_public_contract(monkeypatch: pytest.MonkeyPatch, state: str, version: str, local: bool, expected: str) -> None:
    entry = catalog.BY_ID["blender-skills"]
    assert entry.execution
    monkeypatch.setattr(execution.addons, "status", lambda _: {
        "enabled": True, "runtime": {"base_url": "http://127.0.0.1:9130"},
        "contributions": {"agent_tools": [{"id": name} for name in entry.execution.tool_ids]},
    })
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"capabilities": {
        "3d.scene_recipe": {"state": state, "schema_version": version, "local_only": local},
    }}))
    monkeypatch.setattr(execution.httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    assert execution.check(entry)["state"] == expected


def test_skill_config_is_not_called_synchronously_in_async_provider() -> None:
    path = Path(__file__).parents[1] / "app/integrations/opencode/provider.py"
    tree = ast.parse(path.read_text())
    for function in ast.walk(tree):
        if isinstance(function, ast.AsyncFunctionDef):
            for node in ast.walk(function):
                assert not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == "_runtime_config"), function.name
