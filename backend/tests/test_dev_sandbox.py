"""開発用の軽量サンドボックス（./deck.sh sandbox）。podman は呼ばず、引数と後始末を確かめる。

ネットワークの境界（pod・中継）の目的と根拠: 箱は host network で本番のゲートウェイへ鍵なしで届き、
モデルの切り替え（POST .../models/{m}/load）まで呼べた。箱は pod に入れ、届くのは中継だけにする。

箱の持ち主と MCP（P2a、2026-11-21）の目的と根拠: 箱の AI に ControlDeck の道具を使わせる。誰の権限で動くかを持ち主
（--owner、省くと管理者が 1 人ならその人）で決め、そのトークンは箱の外の中継だけが持つ（0600 の環境変数ファイル、
argv・箱の opencode.json には入れない）。箱の bridge は中継を向く。

箱の設定（meta）を箱の外へ置く目的と根拠（2026-11-22 監査）: 設定が箱の書ける box.path/sandbox.json にあると、箱の中の AI が
overlays に ~/.ssh などを足して、次にホストが作る podman の引数で見せられる。設定は `<root>/.meta/<名前>.json`（0700・0600）へ置き、
古い箱は検査を通した分だけ移し、load のたびに検査する（git_common_dir・overlays の 2 種類・ポートの範囲・owner の型）。
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
    monkeypatch.setattr(sandbox, "_free_serve_port", lambda: 19000)
    monkeypatch.setattr(sandbox, "ensure_pod", lambda box, **kw: sandbox.pod_name(box))
    monkeypatch.setattr(sandbox, "ensure_gateway", lambda box: None)
    monkeypatch.setattr(sandbox, "_users_with_permission",
                        lambda permission, name="": [name or "admin1"] if permission != "none" else [])
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
    meta = json.loads(sandbox._meta_path(box.name).read_text())
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
    # 書けるのは箱のフォルダと、その中の箱ごとの /tmp だけ
    assert writable == [f"{box.path}:{box.path}:rw", f"{box.path / 'tmp'}:/tmp:rw"]
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
    assert [v for v in volumes if v.endswith(":rw")] == [f"{box.path}:{box.path}:rw", f"{box.path / 'tmp'}:/tmp:rw"]


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
    # 上流の LLM は箱の中の中継（P3）
    assert f"llm_upstream: http://127.0.0.1:{box.gateway_port}/api/v1/llm/v1" in text


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


def test_dev_box_has_its_own_tmp_that_goes_away_with_the_box(repo, monkeypatch):
    """箱ごとの /tmp。空のルートの /tmp は全部の箱で共有され、テストの一時フォルダ（90GB の見かけの GGUF）が
    溜まり続けて見かけ 372GB になった（2026-11-21）。箱のフォルダの中なので destroy で一緒に消える。"""
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    box = sandbox.create("tmp1", str(repo))
    argv = sandbox.podman_argv(box, ["true"])
    assert f"{box.path / 'tmp'}:/tmp:rw" in argv
    assert (box.path / "tmp").is_dir() and ((box.path / "tmp").stat().st_mode & 0o777) == 0o700


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
    assert "127.0.0.1:19000:19000" in create, "会話サーバーのポート（serve_port）も公開する"
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
    meta = json.loads(sandbox._meta_path(box.name).read_text())
    meta.pop("gateway_port")
    meta.pop("allow_load")
    sandbox._meta_path(box.name).write_text(json.dumps(meta))
    loaded = sandbox.load("old1")
    assert loaded.gateway_port == 18900 and loaded.allow_load is False
    assert json.loads(sandbox._meta_path(box.name).read_text())["gateway_port"] == 18900


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
def _users(monkeypatch, admins, others=()):
    def fake(permission, name=""):
        pool = admins if permission == "settings.manage" else [*admins, *others]
        return [u for u in pool if not name or u == name]

    monkeypatch.setattr(sandbox, "_users_with_permission", fake)


def test_owner_is_the_only_admin_or_must_be_named(repo, monkeypatch):
    _users(monkeypatch, ["alice"], ["bob"])
    assert sandbox.create("own1", str(repo)).owner == "alice"
    assert json.loads(sandbox._meta_path("own1").read_text())["owner"] == "alice"
    assert sandbox.create("own2", str(repo), owner="bob").owner == "bob"
    with pytest.raises(sandbox.SandboxError, match="carol"):
        sandbox.create("own3", str(repo), owner="carol")
    _users(monkeypatch, ["alice", "dave"])
    with pytest.raises(sandbox.SandboxError, match="--owner"):
        sandbox.create("own4", str(repo))
    assert not sandbox._dir("own4").exists(), "決められなければ箱を作らない"
    assert sandbox.create("own5", str(repo), owner="dave").owner == "dave"
    _users(monkeypatch, [])
    with pytest.raises(sandbox.SandboxError, match="管理者がいません"):
        sandbox.create("own6", str(repo))


def test_old_box_without_owner_gets_one_by_the_same_rule_and_saves_it(repo, monkeypatch):
    box = sandbox.create("old2", str(repo))
    meta = json.loads(sandbox._meta_path(box.name).read_text())
    meta.pop("owner")
    sandbox._meta_path(box.name).write_text(json.dumps(meta))
    _users(monkeypatch, ["alice", "dave"])
    loaded = sandbox.load("old2")  # 読むだけの操作（list・destroy）は持ち主が決まらなくても通る
    with pytest.raises(sandbox.SandboxError, match="--owner"):
        sandbox.box_owner(loaded)
    _users(monkeypatch, ["alice"])
    assert sandbox.box_owner(sandbox.load("old2")) == "alice"
    assert json.loads(sandbox._meta_path(box.name).read_text())["owner"] == "alice"


def test_relay_env_file_is_0600_outside_the_box_and_token_stays_out_of_argv(repo, monkeypatch):
    import stat

    from app.addons import agent_mcp

    box = sandbox.create("env1", str(repo))
    monkeypatch.setattr(sandbox, "_owner_user_id", lambda username: 7)
    monkeypatch.setattr(agent_mcp, "issue_opencode_token", lambda uid, cid, **kw: f"tok-{uid}-{cid}")
    calls = []
    # 起動前は動いていない、systemd-run を呼んだ後は動いている
    monkeypatch.setattr(sandbox, "_gateway_healthy",
                        lambda port: any(c[0].endswith("systemd-run") for c in calls))
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("")

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(sandbox.subprocess, "run", run)
    monkeypatch.setattr(sandbox.time, "sleep", lambda s: None)
    REAL_ENSURE_GATEWAY(box)
    path = sandbox.relay_env_file(box)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not path.resolve().is_relative_to(box.path), "箱のフォルダの外（箱からは見えない）"
    assert path.read_text() == "CONTROL_DECK_SANDBOX_MCP_TOKEN=tok-7-sandbox-env1\n"
    start = next(c for c in calls if c[0].endswith("systemd-run"))
    assert f"--property=EnvironmentFile={path}" in start
    assert not any("tok-7" in part for part in start), "トークンは argv に出さない"


def test_opencode_config_points_the_bridge_at_the_relay_without_the_mcp_token(repo, monkeypatch):
    box = sandbox.create("cfg2", str(repo))
    (box.path.parent / ".relay").mkdir(exist_ok=True)
    sandbox.relay_env_file(box).write_text("CONTROL_DECK_SANDBOX_MCP_TOKEN=relay-secret-token\n")
    text = sandbox.opencode_config(box).read_text()
    assert "relay-secret-token" not in text
    config = json.loads(text)["mcp"]["servers"]["controldeck_addons"]
    assert config["environment"]["CONTROL_DECK_ADDON_MCP_URL"] == "http://127.0.0.1:18900/api/v1/addons/agent-mcp"
    assert config["environment"]["CONTROL_DECK_ADDON_MCP_TOKEN"] == "sandbox"
    bridge = box.home / ".controldeck" / "addon_mcp_bridge.py"
    assert config["command"] == ["/usr/bin/python3", str(bridge)] and bridge.is_file()
    assert config["environment"]["CONTROL_DECK_ADDON_MCP_RESULT_DIR"].startswith(str(box.path)), "結果のフォルダは箱の中"
    assert str(box.path) in str(bridge)


def test_opencode_config_refuses_to_write_when_the_mcp_token_would_leak(repo, monkeypatch):
    box = sandbox.create("cfg3", str(repo))
    (box.path.parent / ".relay").mkdir(exist_ok=True)
    sandbox.relay_env_file(box).write_text("CONTROL_DECK_SANDBOX_MCP_TOKEN=sandbox\n")  # 偽の漏れ: 設定にある値と同じ
    with pytest.raises(sandbox.SandboxError, match="MCP のトークン"):
        sandbox.opencode_config(box)
    assert not (box.home / ".config" / "opencode" / "opencode.json").exists()


# --- モデルの順番（使い切ったら次へ） ---
# 目的: 箱の OpenCode は既定で GLM(Go)→Zen 無料→ローカルの順に使い、上限に当たったら次へ移る。
# 根拠: 利用者 2026-11-21「GLM を使い切り、無料で高性能なものを順次」。上限以外のエラーで切り替えると不具合を隠す。
CHAIN = ["opencode-go/glm-5.3", "opencode/hy4-flash-free", "opencode/deepseek-v4.1-flash-free", "controldeck/auto"]


def test_default_chain_skips_opencode_models_without_zen_key_or_when_box_disallows(zen_box, monkeypatch):
    """目的: 既定の順番、キー無し・allow_zen off で opencode* を飛ばす、meta の model_chain が優先、local は別名。"""
    from app.integrations.opencode import zen

    assert sandbox.DEFAULT_MODEL_CHAIN == CHAIN
    assert sandbox.model_chain(zen_box) == CHAIN
    zen_box.meta["allow_zen"] = False
    assert sandbox.model_chain(zen_box) == ["controldeck/auto"]
    zen_box.meta["allow_zen"] = True
    monkeypatch.setattr(zen, "get", lambda: "")
    assert sandbox.model_chain(zen_box) == ["controldeck/auto"]
    monkeypatch.setattr(zen, "get", lambda: ZEN_KEY)
    zen_box.meta["model_chain"] = ["opencode/hy4-flash-free", "local"]
    assert sandbox.model_chain(zen_box) == ["opencode/hy4-flash-free", "controldeck/auto"]


def _fake_opencode(monkeypatch, outputs):
    """outputs: 実行ごとの (JSONL の行, 終了コード)。起動した argv を返す。"""
    calls = []
    real = sandbox.subprocess.Popen

    class Proc(real):
        def __new__(cls, argv, **kw):
            return real(argv, **kw) if argv[0] == "git" else object.__new__(cls)

        def __init__(self, argv, **kw):
            self.argv = argv
            lines, self.code = outputs[len(calls)]
            self.stdout = iter(json.dumps(x) + "\n" for x in lines)
            calls.append(argv)

        def wait(self):
            return self.code

    monkeypatch.setattr(sandbox.subprocess, "Popen", Proc)
    return calls


def _model_of(argv):
    return argv[argv.index("--model") + 1]


def test_run_switches_model_on_limit_error_and_continues_the_session(zen_box, monkeypatch, capsys):
    """目的: 上限の印（type=error＋limit 等）で次のモデルへ、取れた sessionID は --session で続け、last_model を保存する。"""
    limit = {"type": "error", "error": {"message": "Monthly usage limit exceeded"}}
    calls = _fake_opencode(monkeypatch, [([{"type": "step", "sessionID": "ses_1"}, limit], 1),
                                         ([{"type": "text", "sessionID": "ses_1"}], 0)])
    code = sandbox.run_with_chain(zen_box, "do it", CHAIN)
    assert code == 0
    assert [_model_of(a) for a in calls] == CHAIN[:2]
    assert "--session" not in calls[0]
    assert calls[1][calls[1].index("--session") + 1] == "ses_1"
    assert "do it" in calls[1]
    out = capsys.readouterr()
    assert '"sessionID": "ses_1"' in out.out
    assert "opencode-go/glm-5.3 -> opencode/hy4-flash-free" in out.err
    assert sandbox.load("zen1").meta["last_model"] == "opencode/hy4-flash-free"
    monkeypatch.setattr(sandbox, "probe", lambda box, **kw: {"branch": "", "dirty": 0, "unpushed": 0})  # 箱の中の git は動かさない
    assert sandbox.status(sandbox.load("zen1"))["last_model"] == "opencode/hy4-flash-free"


def test_run_does_not_switch_on_non_limit_errors_and_ends_with_last_exit_code_when_exhausted(zen_box, monkeypatch):
    """目的: 道具の失敗では切り替えない。全部尽きたら最後の終了コードで終わる。"""
    calls = _fake_opencode(monkeypatch, [([{"type": "error", "error": {"message": "tool crashed"}}], 3)])
    assert sandbox.run_with_chain(zen_box, "x", CHAIN) == 3
    assert len(calls) == 1
    limit = {"type": "error", "error": {"message": "HTTP 429"}}
    calls = _fake_opencode(monkeypatch, [([limit], 1), ([limit], 2)])
    assert sandbox.run_with_chain(zen_box, "x", CHAIN[:2]) == 2
    assert len(calls) == 2


def test_explicit_model_is_used_alone_and_local_is_an_alias(zen_box, monkeypatch):
    """目的: --model 明示は順番を使わず 1 つだけ。local はローカル LLM。"""
    calls = _fake_opencode(monkeypatch, [([{"type": "error", "error": {"message": "quota"}}], 1)])
    assert sandbox.main(["opencode", "zen1", "--run", "x", "--model", "local"]) == 1
    assert [_model_of(a) for a in calls] == ["controldeck/auto"]


def test_limit_detection_uses_word_boundaries():
    """目的: 上限の印は語の区切りで見る。"rate" だけで判定すると generate・accurate を含むふつうのエラーで
    誤って次のモデルへ移った（2026-11-21 監査で指摘）。"""
    err = lambda message: {"type": "error", "error": {"message": message}}
    for hit in ("Rate limit exceeded", "429 Too Many Requests", "monthly usage limit reached",
                "insufficient credits", "402 Payment Required", "quota exceeded"):
        assert sandbox.is_limit_error(err(hit)), hit
    for miss in ("failed to generate the patch", "inaccurate tool arguments", "file not found",
                 "tool edit failed: oldString not unique"):
        assert not sandbox.is_limit_error(err(miss)), miss
    assert not sandbox.is_limit_error({"type": "text", "part": {"text": "rate limit"}})


def test_a_box_has_its_own_git_author_and_the_users_gitconfig_is_not_needed(repo):
    """開発の作業場 1 段目: 箱の中にはコミットの作者が無く不定だった。作業ツリーの local の設定に固定の作者を入れる。"""
    box = sandbox.create("who1", str(repo))
    assert sandbox._git("config", "--local", "user.name", cwd=box.repo) == "ControlDeck Sandbox (who1)"
    assert sandbox._git("config", "--local", "user.email", cwd=box.repo) == "sandbox@controldeck.local"


def test_reviewer_model_differs_from_the_work_model(repo, monkeypatch):
    """レビュー役は作業と別のモデルにする（同じモデルは同じ見落としをする）。ローカルの作業→最初の外部、外部の作業→ローカル。"""
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "get", lambda: "zen-key")
    box = sandbox.create("rev1", str(repo))
    assert sandbox.reviewer_model_for(box, "auto") == "opencode-go/glm-5.3"
    assert sandbox.reviewer_model_for(box, "local") == "opencode-go/glm-5.3"
    assert sandbox.reviewer_model_for(box, "controldeck/auto") == "opencode-go/glm-5.3"
    assert sandbox.reviewer_model_for(box, "opencode-go/glm-5.3") == "controldeck/auto"
    assert sandbox.reviewer_model_for(box, "opencode/hy4-flash-free") == "controldeck/auto"


def test_reviewer_stays_local_when_no_external_is_allowed(repo, monkeypatch):
    """allow_zen が off（またはキーが無い）なら外部は使わない。ローカルの作業のレビュー役は設定を変えない（None）。"""
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "get", lambda: "zen-key")
    box = sandbox.create("rev2", str(repo), allow_zen=False)
    assert sandbox.reviewer_model_for(box, "auto") is None
    monkeypatch.setattr(zen, "get", lambda: "")
    on = sandbox.create("rev3", str(repo))
    assert sandbox.reviewer_model_for(on, "auto") is None


def test_opencode_config_gives_the_reviewer_another_model_and_ships_the_development_skill(repo, monkeypatch):
    from app.integrations.opencode import zen

    monkeypatch.setattr(zen, "get", lambda: "zen-key")
    box = sandbox.create("rev4", str(repo))
    config = json.loads(sandbox.opencode_config(box).read_text())
    assert config["agents"]["reviewer"]["model"] == "opencode-go/glm-5.3"
    skill = box.home / ".controldeck" / "skills" / "controldeck-development" / "SKILL.md"
    assert skill.is_file()
    assert str(skill.parent) in config["skills"]
    external = json.loads(sandbox.opencode_config(box, model="opencode-go/glm-5.3", work_model="opencode-go/glm-5.3").read_text())
    assert external["agents"]["reviewer"]["model"] == "controldeck/auto"


def test_box_keeps_npm_and_pip_offline(repo, monkeypatch):
    """目的: 箱の中から npm・npx・pip が外へ取りに行かない。
    根拠: 2026-11-22 実機、箱の OpenCode が `npx tsc` で npm からパッケージを取ってきた（外部からの取得の禁止に反する）。"""
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: f"/usr/bin/{name}")
    box = sandbox.create("off1", str(repo))
    argv = sandbox.podman_argv(box, ["true"])
    assert "npm_config_offline=true" in argv and "PIP_NO_INDEX=1" in argv


# ---- meta は箱の外（.meta）。箱の中から書き換えて見せる場所を増やせない ----

def _argv_text(box):
    return " ".join(sandbox.podman_argv(box, ["true"]))


def test_new_box_writes_meta_outside_the_box_with_private_modes(repo):
    box = sandbox.create("meta1", str(repo))
    meta_file = sandbox._meta_path("meta1")
    assert meta_file.is_file() and not box.path.joinpath("sandbox.json").exists(), "新しい箱は最初から .meta へ"
    assert not meta_file.resolve().is_relative_to(box.path.resolve()), "箱の pod が書ける box.path の外"
    assert oct(meta_file.stat().st_mode & 0o777) == "0o600" and oct(meta_file.parent.stat().st_mode & 0o777) == "0o700"
    assert str(sandbox.meta_dir()) not in _argv_text(box), "箱へはマウントしない"
    box.meta["last_model"] = "x"
    sandbox._save_meta(box.path, box.meta)
    assert oct(meta_file.stat().st_mode & 0o777) == "0o600" and sandbox.load("meta1").meta["last_model"] == "x"


def test_legacy_sandbox_json_is_migrated_once_after_inspection_and_extra_overlays_are_dropped(repo, tmp_path):
    box = sandbox.create("leg1", str(repo))
    forged = dict(box.meta)
    forged["overlays"] = [*box.meta["overlays"], [str(tmp_path / "home" / ".ssh"), str(tmp_path / "home" / ".ssh")],
                          str(tmp_path / "secret"), [str(tmp_path / "forbidden"), str(tmp_path / "forbidden")]]
    forged["last_check"] = {"passed": True, "at": 1, "tree": "x"}          # 関門の合格の偽装は引き継がない
    sandbox._meta_path("leg1").unlink()
    (box.path / "sandbox.json").write_text(json.dumps(forged))
    moved = sandbox.load("leg1")
    assert moved.meta["overlays"] == box.meta["overlays"] and "last_check" not in moved.meta
    text = _argv_text(moved)
    assert ".ssh" not in text and "secret" not in text and "forbidden" not in text, "足した見せる場所は podman の引数に入らない"
    assert sandbox._meta_path("leg1").is_file() and not (box.path / "sandbox.json").exists(), "移したら古い場所は消す"
    # 箱の中からもう一度置かれても使わない（消す）
    (box.path / "sandbox.json").write_text(json.dumps({**forged, "port": 22}))
    assert sandbox.load("leg1").meta["port"] == box.meta["port"] and not (box.path / "sandbox.json").exists()


@pytest.mark.parametrize("field,value", [
    ("git_common_dir", "/etc"), ("name", "other"), ("port", 22), ("gateway_port", 80), ("serve_port", True),
    ("owner", ["root"]), ("source", "box"),
])
def test_tampered_legacy_meta_is_refused(repo, field, value):
    box = sandbox.create("tam1", str(repo))
    forged = dict(box.meta)
    forged[field] = str(box.path / "work" / "repo") if value == "box" else value
    sandbox._meta_path("tam1").unlink()
    (box.path / "sandbox.json").write_text(json.dumps(forged))
    with pytest.raises(sandbox.SandboxError, match="改ざんの恐れ"):
        sandbox.load("tam1")
    assert not sandbox._meta_path("tam1").exists(), "通らないものは移さない"


def test_git_common_dir_of_a_stored_meta_is_checked_on_every_load(repo, tmp_path):
    sandbox.create("gcd1", str(repo))
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True, capture_output=True)
    meta = json.loads(sandbox._meta_path("gcd1").read_text())
    meta["git_common_dir"] = str(other / ".git")
    sandbox._meta_path("gcd1").write_text(json.dumps(meta))
    with pytest.raises(sandbox.SandboxError, match="git_common_dir"):
        sandbox.load("gcd1")
    sandbox.destroy("gcd1")      # 使えない箱でも消せる
    assert not sandbox._dir("gcd1").exists() and not sandbox._meta_path("gcd1").exists()
