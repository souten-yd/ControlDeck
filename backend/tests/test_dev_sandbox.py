"""開発用の軽量サンドボックス（./deck.sh sandbox）。podman は呼ばず、引数と後始末を確かめる。

ネットワークの境界（pod・中継）の目的と根拠: 箱は host network で本番のゲートウェイへ鍵なしで届き、
モデルの切り替え（POST .../models/{m}/load）まで呼べた。箱は pod に入れ、届くのは中継だけにする。
"""
import json
import subprocess

import pytest

from app.maintenance import sandbox


REAL_ENSURE_POD = sandbox.ensure_pod
REAL_ENSURE_GATEWAY = sandbox.ensure_gateway


@pytest.fixture
def repo(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(sandbox, "_data_dir", lambda: data)
    monkeypatch.setattr(sandbox, "_free_port", lambda: 18800)
    monkeypatch.setattr(sandbox, "_free_gateway_port", lambda: 18900)
    monkeypatch.setattr(sandbox, "ensure_pod", lambda box: sandbox.pod_name(box))
    monkeypatch.setattr(sandbox, "ensure_gateway", lambda box: None)
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


def test_opencode_plugins_visible_readonly_without_exposing_other_features(repo, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    features = sandbox._data_dir() / "features"
    names = ("opencode-superpowers", "opencode-adversarial-review", "opencode-auto-advisor")
    for name in (*names, "unrelated-addon"):
        (features / name / "node_modules").mkdir(parents=True)
    box = sandbox.create("plugins", str(repo))
    argv = sandbox.podman_argv(box, ["true"])
    volumes = [argv[i + 1] for i, item in enumerate(argv) if item == "-v"]
    for name in names:
        path = features / name
        assert f"{path}:{path}:ro" in volumes
    assert not any("unrelated-addon" in volume for volume in volumes)
    assert [v for v in volumes if v.endswith(":rw")] == [f"{box.path}:{box.path}:rw"]


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
    monkeypatch.setattr(sandbox, "gateway_stop", lambda box: None)
    monkeypatch.setattr(sandbox, "pod_remove", lambda box: None)
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


def test_dev_box_runs_in_its_pod_not_on_the_host_network(repo, monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    box = sandbox.create("net1", str(repo))
    argv = sandbox.podman_argv(box, ["true"])
    assert "host" not in argv and "--userns=keep-id" not in argv
    assert argv[argv.index("--pod") + 1] == "cd-sandbox-net1"
    # プロジェクトの箱（pod 無し）は従来のまま
    plain = sandbox.container_argv(["true"], workdir=repo, writable=[repo])
    assert plain[plain.index("--userns=keep-id") + 1:plain.index("--userns=keep-id") + 3] == ["--network", "host"]
    assert "--pod" not in plain


def _fake_podman(monkeypatch, label):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["pod", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if label[0] is not None else 1, label[0] or "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    return calls


def test_ensure_pod_reuses_same_spec_and_recreates_on_change(repo, monkeypatch):
    box = sandbox.create("net2", str(repo))
    label = [None]
    calls = _fake_podman(monkeypatch, label)
    REAL_ENSURE_POD(box)
    create = next(c for c in calls if c[1:3] == ["pod", "create"])
    assert "pasta:-T,18900" in create and "127.0.0.1:18800:18800" in create
    assert not any(c[1:3] == ["pod", "rm"] for c in calls), "無ければ rm しない"
    label[0] = sandbox._pod_spec(box)
    calls.clear()
    REAL_ENSURE_POD(box)
    assert [c[1:3] for c in calls] == [["pod", "inspect"]], "同じ設定なら作り直さない"
    label[0] = "stale"
    calls.clear()
    REAL_ENSURE_POD(box)
    kinds = [c[1:3] for c in calls]
    assert kinds.index(["pod", "rm"]) < kinds.index(["pod", "create"])


def test_old_sandbox_json_without_gateway_port_gets_one_and_saves_it(repo):
    box = sandbox.create("old1", str(repo))
    meta = json.loads((box.path / "sandbox.json").read_text())
    meta.pop("gateway_port")
    meta.pop("allow_load")
    (box.path / "sandbox.json").write_text(json.dumps(meta))
    loaded = sandbox.load("old1")
    assert loaded.gateway_port == 18900 and loaded.allow_load is False
    assert json.loads((box.path / "sandbox.json").read_text())["gateway_port"] == 18900


def test_opencode_config_has_no_production_key_and_points_at_the_relay(repo, monkeypatch):
    from app.models_mgmt import gateway

    monkeypatch.setattr(gateway, "get_api_key", lambda create=False: "sk-production-secret-123")
    box = sandbox.create("cfg1", str(repo))
    text = sandbox.opencode_config(box).read_text()
    assert "sk-production-secret-123" not in text
    assert "http://127.0.0.1:18900/api/v1/llm/v1" in text


ZEN_KEY = "zen-secret-key-abcd"


@pytest.fixture
def zen_box(repo, monkeypatch, tmp_path):
    """Zen のキーが保存済みで、opencode の実行ファイルがある箱。"""
    from app.features import registry
    from app.integrations.opencode import zen

    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(zen, "get", lambda: ZEN_KEY)
    binary = tmp_path / "opencode"
    binary.write_text("")
    monkeypatch.setattr(registry, "_managed_executable", lambda name: binary)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return sandbox.create("zen1", str(repo))


def test_zen_key_goes_to_opencode_only_through_a_private_env_file_outside_the_box(zen_box):
    """目的: Zen のキーは argv に出さず（ps から見える）、0600 の --env-file で箱の外の一時ファイルから渡し、opencode.json に書かない。
    根拠: 2026-11-21 Zen の無料モデルは箱の OpenCode が opencode.ai へ直接繋ぐ。秘密は argv・設定ファイルに置かない決まり。"""
    argv = sandbox.opencode_argv(zen_box, message="hi", interactive=False)
    assert ZEN_KEY not in " ".join(argv) and "OPENCODE_API_KEY" not in " ".join(argv)
    path = sandbox.Path(argv[argv.index("--env-file") + 1])
    assert path.read_text() == f"OPENCODE_API_KEY={ZEN_KEY}\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert not path.resolve().is_relative_to(zen_box.path)
    assert ZEN_KEY not in (zen_box.home / ".config" / "opencode" / "opencode.json").read_text()
    sandbox.remove_env_file(argv)
    assert not path.exists()


def test_zen_key_is_not_given_when_the_box_disallows_it_or_for_shell_and_exec(zen_box):
    """目的: allow_zen が off の箱、および shell・exec（podman_argv を直接使う経路）にはキーを渡さない。"""
    argv = sandbox.podman_argv(zen_box, ["true"])
    assert "--env-file" not in argv and ZEN_KEY not in " ".join(argv)
    zen_box.meta["allow_zen"] = False
    assert not zen_box.allow_zen
    argv = sandbox.opencode_argv(zen_box, message="hi", interactive=False)
    assert "--env-file" not in argv


def test_new_box_allows_zen_by_default_and_no_zen_turns_it_off(repo):
    assert sandbox.create("zen2", str(repo)).allow_zen is True
    assert sandbox.create("zen3", str(repo), allow_zen=False).allow_zen is False


def test_opencode_model_keeps_provider_form_and_defaults_to_controldeck(zen_box):
    """目的: --model は provider/モデルの形ならそのまま、無ければ controldeck/ を付ける。"""
    assert sandbox.opencode_model("auto") == "controldeck/auto"
    assert sandbox.opencode_model("opencode/deepseek-v4.1-flash-free") == "opencode/deepseek-v4.1-flash-free"
    argv = sandbox.opencode_argv(zen_box, message="hi", model="opencode/deepseek-v4.1-flash-free", interactive=False)
    assert argv[argv.index("--model") + 1] == "opencode/deepseek-v4.1-flash-free"
    sandbox.remove_env_file(argv)
