"""開発用の軽量サンドボックス（./deck.sh sandbox）。podman は呼ばず、引数と後始末を確かめる。"""
import json
import subprocess

import pytest

from app.maintenance import sandbox


@pytest.fixture
def repo(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(sandbox, "_data_dir", lambda: data)
    monkeypatch.setattr(sandbox, "_free_port", lambda: 18800)
    source = tmp_path / "src"
    source.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=source, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("-c", "user.email=a@b.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    run("remote", "add", "origin", "https://github.com/example/repo.git")
    (source / ".venv").mkdir()
    return source


def test_create_clones_without_touching_the_source(repo):
    box = sandbox.create("work1", str(repo))
    assert (box.repo / ".git").is_dir()
    assert sandbox._git("rev-parse", "--abbrev-ref", "HEAD", cwd=box.repo) == "sandbox/work1"
    assert sandbox._git("remote", "get-url", "origin", cwd=box.repo) == "https://github.com/example/repo.git"
    assert "sandbox/work1" not in sandbox._git("branch", cwd=repo), "元のリポジトリにブランチを作らない"
    meta = json.loads((box.path / "sandbox.json").read_text())
    assert meta["overlays"] == [[str(repo / ".venv"), str(repo / ".venv")]]
    with pytest.raises(sandbox.SandboxError):
        sandbox.create("work1", str(repo))


def test_names_are_validated(repo):
    for bad in ("../x", "Work", "a" * 40, "1abc", ""):
        with pytest.raises(sandbox.SandboxError):
            sandbox.create(bad, str(repo))


def test_podman_mounts_only_the_box_writable(repo, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    box = sandbox.create("work2", str(repo))
    argv = sandbox.podman_argv(box, ["true"])
    volumes = [argv[i + 1] for i, item in enumerate(argv) if item == "-v"]
    writable = [v for v in volumes if v.endswith(":rw")]
    assert writable == [f"{box.path}:{box.path}:rw"]
    assert "/usr:/usr:ro" in volumes and "/etc:/etc:ro" in volumes
    assert f"{repo / '.venv'}:{repo / '.venv'}:O" in volumes, "依存は overlay（箱の中で書いてもホストは変わらない）"
    assert not any("control-deck.db" in v or v.startswith(f"{sandbox._data_dir()}:") for v in volumes)
    assert not any("/run/gh" in v for v in volumes), "GitHub を許していなければ gh の設定を見せない"
    assert argv[argv.index("--rootfs") + 1] == str(sandbox._rootfs())


def test_github_config_only_when_allowed(repo, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "gh").mkdir(parents=True)
    (home / ".config" / "gh" / "hosts.yml").write_text("github.com: {}\n")
    monkeypatch.setattr(sandbox.Path, "home", lambda: home)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    box = sandbox.create("work3", str(repo), github=True)
    argv = sandbox.podman_argv(box, ["true"])
    assert f"{home / '.config' / 'gh'}:/run/gh:ro" in argv
    assert "GH_CONFIG_DIR=/run/gh" in argv


def test_destroy_stays_inside_the_sandbox_root(repo, monkeypatch):
    monkeypatch.setattr(sandbox, "mirror_stop", lambda box: None)
    box = sandbox.create("work4", str(repo))
    sandbox.destroy("work4")
    assert not box.path.exists() and repo.exists()
    with pytest.raises(sandbox.SandboxError):
        sandbox.destroy("work4")


def test_mirror_config_points_inside_the_box(repo):
    box = sandbox.create("work5", str(repo))
    text = sandbox.mirror_config(box).read_text()
    assert f"data_dir: {box.path / 'mirror' / 'data'}" in text and "port: 18800" in text


def test_opencode_cannot_stop_production_through_deck_sh():
    import fnmatch

    from app.integrations.opencode import provider

    rules = provider._forbidden_commands()

    def verdict(command: str) -> str:
        result = "allow"
        for pattern, effect in rules.items():
            if fnmatch.fnmatchcase(command, pattern):
                result = effect
        return result

    for blocked in ("./deck.sh", "./deck.sh stop", "./deck.sh service", "cd .. && ./deck.sh",
                    "./deck.sh test; ./deck.sh start", "bash deck.sh update-prepare"):
        assert verdict(blocked) == "deny", blocked
    for allowed in ("./deck.sh test -q tests/x.py", "./deck.sh e2e a11y.spec.ts",
                    "./deck.sh sandbox list", "./deck.sh ext lint manifest.json"):
        assert verdict(allowed) == "allow", allowed
