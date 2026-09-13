import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace


def test_feature_default_is_disabled_and_external_uninstall_is_preserved(monkeypatch, tmp_path):
    from app.features import registry

    external = tmp_path / "bin" / "opencode"
    external.parent.mkdir()
    external.write_text("#!/bin/sh\necho 1.2.3\n", encoding="utf-8")
    external.chmod(0o755)
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")

    def which(name):
        if name == "opencode":
            return str(external)
        if name == "npm":
            return "/usr/bin/npm"
        return None

    monkeypatch.setattr(registry.shutil, "which", which)
    current = registry.status("opencode")
    assert current["installed"] is True and current["managed"] is False and current["enabled"] is False
    registry.enable("opencode")
    assert registry.status("opencode")["enabled"] is True
    # user serviceのPATHが対話shellと異なっても、enable時の実体を再利用する。
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    assert registry.status("opencode")["enabled"] is True
    after = registry.uninstall("opencode")
    assert external.exists() and after["installed"] is True and after["enabled"] is False


def test_managed_install_uses_private_prefix_and_does_not_enable(monkeypatch, tmp_path):
    from app.features import registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        binary = tmp_path / "data" / "features" / "opencode" / "node_modules" / ".bin" / "opencode"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\necho 9.9.9\n", encoding="utf-8")
        binary.chmod(0o755)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", run)
    installed = registry.install("opencode")
    assert calls[0][:4] == ["/usr/bin/npm", "install", "--prefix", str(tmp_path / "data" / "features" / "opencode")]
    assert installed["managed"] is True and installed["enabled"] is False


def test_update_installs_latest_and_reports_previous_version(monkeypatch, tmp_path):
    from app.features import registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    binary = tmp_path / "data" / "features" / "opencode" / "node_modules" / ".bin" / "opencode"
    versions = ["1.18.3"]
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "/usr/bin/npm":
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o755)
            if argv[-1].endswith("@latest"):
                versions.append("1.18.16")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout=versions[-1] + "\n", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", run)
    registry.install("opencode")
    calls.clear()
    updated = registry.update("opencode")
    npm_calls = [argv for argv in calls if argv[0] == "/usr/bin/npm"]
    assert npm_calls == [[
        "/usr/bin/npm", "install", "--prefix", str(tmp_path / "data" / "features" / "opencode"),
        "--no-fund", "--no-audit", "opencode-ai@latest",
    ]]
    assert updated["previous_version"] == "1.18.3" and updated["version"] == "1.18.16"


def test_update_rejects_external_only_install(monkeypatch, tmp_path):
    import pytest

    from app.features import registry

    external = tmp_path / "bin" / "opencode"
    external.parent.mkdir()
    external.write_text("#!/bin/sh\necho 1.2.3\n", encoding="utf-8")
    external.chmod(0o755)
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(
        registry.shutil, "which",
        lambda name: str(external) if name == "opencode" else "/usr/bin/npm" if name == "npm" else None,
    )
    with pytest.raises(registry.FeatureError):
        registry.update("opencode")
    assert external.read_text(encoding="utf-8").strip().endswith("1.2.3")


def test_update_job_endpoint_requires_managed_install(admin_client, monkeypatch):
    from app.features import registry, router as features_router

    assert admin_client.post(
        "/api/v1/features/unknown/update-jobs", json={}, headers={"X-Requested-With": "ControlDeck"},
    ).status_code == 404
    assert admin_client.post(
        "/api/v1/features/opencode/update-jobs", json={}, headers={"X-Requested-With": "ControlDeck"},
    ).status_code == 422  # 未導入（managed=False）

    base = registry.status("opencode")
    monkeypatch.setattr(features_router.registry, "status", lambda feature_id: {**base, "managed": True})
    monkeypatch.setattr(features_router.registry, "update", lambda feature_id: {**base, "previous_version": "1.0.0"})
    started = admin_client.post(
        "/api/v1/features/opencode/update-jobs", json={}, headers={"X-Requested-With": "ControlDeck"},
    )
    assert started.status_code == 201 and started.json()["job_id"]


def test_disabled_feature_has_no_router_or_workflow_node(admin_client):
    from app.workflows.catalog import valid_types
    from app.workflows.nodes import NODE_EXECUTORS

    assert admin_client.get("/api/v1/opencode/status").status_code == 404
    assert "code.agent" not in valid_types()
    assert "code.agent" not in NODE_EXECUTORS
    meta = admin_client.get("/api/v1/meta").json()
    assert "opencode" not in meta["enabled_features"]


def test_opencode_provider_builds_array_argv_and_parses_json(monkeypatch, tmp_path):
    from app.features import registry
    from app.integrations.opencode import provider as op
    from app.jobs.service import Job

    project = tmp_path / "project"
    project.mkdir()
    binary = tmp_path / "opencode"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: True)
    monkeypatch.setattr(registry, "executable", lambda feature_id: binary)
    monkeypatch.setattr(op.files, "resolve", lambda path: project.resolve())
    monkeypatch.setattr(op.shutil, "which", lambda name: f"/usr/bin/{name}")
    captured = []

    class Process:
        returncode = 0

        async def communicate(self):
            return (b'{"type":"text","text":"analysis complete"}\n', b"")

        async def wait(self):
            return 0

    async def spawn(*argv, **kwargs):
        captured.append((argv, kwargs))
        return Process()

    monkeypatch.setattr(op.asyncio, "create_subprocess_exec", spawn)
    job = Job(id="safe-job-1", kind="opencode.run", title="test")
    result = asyncio.run(op.provider.run(
        job, operation="analyze", project_path=str(project), instruction="check this",
        base_url="http://127.0.0.1:8090/v1", model="llama",
    ))
    argv = captured[0][0]
    assert "--working-directory=" + str(project.resolve()) in argv
    assert "--file" in argv and "check this" not in argv
    assert result["output"] == "analysis complete"
    assert not list((tmp_path / "data" / "integrations" / "opencode").glob("prompt-*.txt"))
    assert not list((tmp_path / "data" / "integrations" / "opencode").glob("runtime-config-*.json"))


def test_opencode_provider_stops_transient_unit_when_cancelled(monkeypatch, tmp_path):
    from app.features import registry
    from app.integrations.opencode import provider as op
    from app.jobs.service import Job

    project = tmp_path / "project"
    project.mkdir()
    binary = tmp_path / "opencode"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: True)
    monkeypatch.setattr(registry, "executable", lambda feature_id: binary)
    monkeypatch.setattr(op.files, "resolve", lambda path: project.resolve())
    monkeypatch.setattr(op.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    class RunningProcess:
        returncode = None

        async def communicate(self):
            await asyncio.Future()

    class StopProcess:
        async def wait(self):
            return 0

    async def spawn(*argv, **kwargs):
        calls.append(argv)
        return RunningProcess() if len(calls) == 1 else StopProcess()

    monkeypatch.setattr(op.asyncio, "create_subprocess_exec", spawn)

    async def scenario():
        job = Job(id="cancel-job-1", kind="opencode.run", title="test")
        task = asyncio.create_task(op.provider.run(
            job, operation="analyze", project_path=str(project), instruction="wait",
        ))
        while not calls:
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert calls[1] == ("/usr/bin/systemctl", "--user", "stop", "cdfeature-opencode-cancel-job-1.service")
    integration = tmp_path / "data" / "integrations" / "opencode"
    assert not list(integration.glob("prompt-*.txt"))
    assert not list(integration.glob("runtime-config-*.json"))


def test_project_symlink_escape_is_rejected(monkeypatch, tmp_path):
    from app.features import registry
    from app.integrations.opencode import provider as op
    from app.jobs.service import Job

    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: True)
    monkeypatch.setattr(op.files, "resolve", lambda path: (_ for _ in ()).throw(op.files.FileAccessError("outside")))
    job = Job(id="escape", kind="opencode.run", title="test")
    try:
        asyncio.run(op.provider.run(job, operation="analyze", project_path="/escape", instruction="x"))
        assert False, "CodeAgentError expected"
    except op.CodeAgentError as exc:
        assert "outside" in str(exc)


def test_runtime_config_lets_opencode_send_images_and_roam_codedev(monkeypatch, tmp_path):
    """VLM を載せていても宣言が無いと画像は送られない。CodeDEV は毎回聞かない。"""
    import json as _json

    from app.integrations.opencode import provider

    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path)
    monkeypatch.setattr(provider, "codedev_root", lambda: tmp_path / "CodeDEV")
    path = provider._runtime_config("caps", "http://127.0.0.1:8090/v1", "auto")
    payload = _json.loads(path.read_text(encoding="utf-8"))

    model = payload["provider"]["controldeck"]["models"]["auto"]
    # attachment だけでは足りない。modalities.input に image が無いと OpenCode は
    # 画像を text へ落として送り、モデルは「画像入力に対応していない」と答える。
    assert model["attachment"] is True
    assert "image" in model["modalities"]["input"]
    assert "text" in model["modalities"]["input"]

    allowed = payload["permission"]["external_directory"]
    root = tmp_path / "CodeDEV"
    assert allowed[f"{root}/*"] == "allow"
    assert allowed[f"{root}/**"] == "allow"
    # ターミナルから送った画像はパスで渡すので、置き場も開いていないと読めない
    from app.terminals import attachments

    assert allowed[f"{attachments.store.root}/*"] == "allow"

    # 他人の process を撃つ道具は渡さない。実際にあった命令でそのまま縛る——
    # 2026-09-12、OpenCode が http.server の残骸と取り違えて Control Deck 本体を
    # 撃ち、40 分止まった。同じ利用者で走っている以上 signal 自体は塞げないので、
    # 撃つ道具の側を閉じる。
    bash = payload["permission"]["bash"]
    incident = "kill 3080550 2>/dev/null; sleep 1; cd /tmp && python3 -m http.server 8799"
    assert _bash_verdict(bash, incident) == "deny", "事故と同じ命令が通ってしまう"
    for command in ("kill 1234", "pkill -f http.server", "killall python3",
                    "systemctl --user stop control-deck-web", "sudo reboot",
                    "cd /tmp && kill 999", "ls; killall node"):
        assert _bash_verdict(bash, command) == "deny", command
    # 制作の仕事は塞がない。
    for command in ("git status", "npm run build", "timeout 60 python3 -m http.server 8799",
                    "python3 manage.py test", "ls -la"):
        assert _bash_verdict(bash, command) != "deny", command


def _bash_verdict(rules: dict[str, str], command: str) -> str | None:
    """OpenCode と同じ照合をする。最後に一致した規則が勝つ。"""
    import fnmatch

    verdict = None
    for pattern, value in rules.items():
        if fnmatch.fnmatchcase(command, pattern):
            verdict = value
    return verdict
    # 全部開けてしまっていないこと
    assert not any(key in ("*", "**", "/*") for key in allowed)


def test_addon_assets_are_readable_without_asking_every_time(monkeypatch, tmp_path):
    """生成物の置き場は開ける。閉じていると生成のたびに確認が入る。

    開けるのは assets の下だけ。feature-data ごと開けると、モデルの重みや
    実行状態まで読めてしまう。
    """
    from app.integrations.opencode import provider

    base = tmp_path / "feature-data"
    (base / "media-forge" / "data" / "assets").mkdir(parents=True)
    (base / "sonic-forge" / "assets").mkdir(parents=True)
    (base / "media-forge" / "runtimes" / "rocm-torch").mkdir(parents=True)
    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("app.config.data_dir", lambda: tmp_path)

    roots = {str(item) for item in provider._addon_asset_roots()}
    assert str(base / "media-forge" / "data" / "assets") in roots
    assert str(base / "sonic-forge" / "assets") in roots
    # 重みや実行状態は開けない
    assert not any("runtimes" in item for item in roots)


def test_runtime_config_declares_the_window_even_for_the_auto_model(monkeypatch, tmp_path):
    """窓を宣言しないと、先回りの自動圧縮が一度も動かない。

    OpenCode の発火判定は `limit.context === 0` を「窓を知らない」と読んで諦める。
    残るのは溢れてモデルが断ってから畳む後追いだけで、実測（2026-09-12、919
    session）では圧縮 38 件のうち 33 件がその後追い、先回りは 0 件だった。

    以前は alias 照合だけで窓を引いていたため、gateway の仮想モデル `auto`
    ——既定の設定値——では必ず None になっていた。転送先は request ごとに
    決まるので、候補のうち最も狭い窓に合わせる。
    """
    import json as _json

    from app.integrations.opencode import provider
    from app.models_mgmt import local_llm

    instances = [
        {"alias": "embed-bge-m3", "role": "embedding", "ctx_size": 8192},
        {"alias": "Wide", "role": "llm", "ctx_size": 131072},
        {"alias": "Narrow", "role": "llm", "ctx_size": 32768},
    ]
    monkeypatch.setattr(local_llm, "list_instances", lambda **kw: list(instances))
    monkeypatch.setattr(
        local_llm, "llm_instances",
        lambda **kw: [i for i in instances if i["role"] == "llm"])

    # 候補のうち狭いほう。広いほうに合わせると、狭いモデルへ回った回だけ溢れが戻る。
    assert provider._model_limits("auto") == {
        "context": 32768, "output": 8192, "input": 24576}
    # alias が実在するときはそのモデルの窓。embedding は候補に入れない。
    assert provider._model_limits("Wide")["context"] == 131072
    # 共有KVを切っていると ctx_size は slot へ固定配分される。1 request の
    # 取り分はその割り算のあと。総量を宣言すると届く前に転送先が溢れる。
    instances.append(
        {"alias": "Split", "role": "llm", "ctx_size": 131072,
         "kv_unified": False, "n_parallel": 4})
    assert provider._model_limits("Split")["context"] == 32768
    instances.pop()

    monkeypatch.setattr(provider, "_integration_dir", lambda: tmp_path)
    monkeypatch.setattr(provider, "codedev_root", lambda: tmp_path / "CodeDEV")
    path = provider._runtime_config("window", "http://127.0.0.1:8090/v1", "auto")
    payload = _json.loads(path.read_text(encoding="utf-8"))

    limit = payload["provider"]["controldeck"]["models"]["auto"]["limit"]
    assert limit["context"] == 32768
    # input がある場合だけ compaction.reserved が読まれる。両方が揃って初めて
    # 発火点が input - reserved になる。
    assert limit["input"] == 24576
    reserved = payload["compaction"]["reserved"]
    assert limit["input"] - reserved < limit["context"], "発火点が窓を越えている"


# ---- OpenCode v2（@opencode/cli）----
#
# v2 は v1 と別パッケージ・別prefixで同居する。設定の形もCLIの引数も違うので、
# 「v1 のまま動く」ことと「v2 では v2 の形になる」ことを両方押さえる。


def test_v2_installs_into_its_own_prefix_and_leaves_v1_alone(monkeypatch, tmp_path):
    from app.features import registry

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(registry.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    v1 = tmp_path / "data" / "features" / "opencode" / "node_modules" / ".bin" / "opencode"
    v1.parent.mkdir(parents=True)
    v1.write_text("#!/bin/sh\necho 1.18.30\n", encoding="utf-8")
    v1.chmod(0o755)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        binary = tmp_path / "data" / "features" / "opencode-v2" / "node_modules" / ".bin" / "opencode2"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\necho 'opencode v2.0.2'\n", encoding="utf-8")
        binary.chmod(0o755)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(registry.subprocess, "run", run)
    monkeypatch.setattr(registry, "_autoconfigure", lambda feature_id: None)
    installed = registry.install("opencode-v2")
    assert calls[0][:5] == ["/usr/bin/npm", "install", "--prefix",
                            str(tmp_path / "data" / "features" / "opencode-v2"), "--no-fund"]
    assert calls[0][-1] == "@opencode/cli"
    assert installed["managed"] is True and installed["enabled"] is False
    # v2 を消しても v1 の導入は残る。
    registry.uninstall("opencode-v2")
    assert v1.exists() and registry.status("opencode")["installed"] is True


def test_v2_runtime_config_uses_v2_schema_and_xdg_config_home(monkeypatch, tmp_path):
    from app.integrations.opencode import provider as op

    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(op, "_skill_paths", lambda: [str(tmp_path / "skill")])
    monkeypatch.setattr(op, "_model_limits", lambda model: {"context": 100, "output": 20, "input": 80})
    monkeypatch.setattr(op, "_api_key_for", lambda base_url: "cdk-test")
    monkeypatch.setattr(op, "gateway_client_header", lambda: "x-control-deck-client")
    monkeypatch.setattr(op, "_allowed_directories", lambda: {"/tmp/allowed/*": "allow"})
    monkeypatch.setattr(op, "_forbidden_commands", lambda: {"kill": "deny", "*;kill *": "deny"})

    config = op._runtime_config("job-1", "http://127.0.0.1:8765/api/v1/llm/v1", "auto", runtime="v2")
    payload = json.loads((config / "opencode" / "opencode.json").read_text(encoding="utf-8"))

    # v2 の鍵は複数形。v1 の鍵は残さない（残すと黙って無視される）。
    assert set(payload) >= {"providers", "permissions", "agents", "compaction", "skills"}
    assert not {"provider", "permission", "agent"} & set(payload)
    provider = payload["providers"]["controldeck"]
    assert provider["package"] == "@opencode/ai/providers/openai-compatible"
    assert provider["settings"]["baseURL"].endswith("/api/v1/llm/v1")
    # headers は settings の中では送られない。provider 直下に置く。
    assert provider["headers"] == {"x-control-deck-client": "opencode"}
    assert "headers" not in provider["settings"]
    model = provider["models"]["auto"]
    assert model["capabilities"] == {"tools": True, "input": ["text", "image"], "output": ["text"]}
    assert "attachment" not in model and "modalities" not in model
    assert {"action": "shell", "resource": "kill", "effect": "deny"} in payload["permissions"]
    assert {"action": "external_directory", "resource": "/tmp/allowed/*",
            "effect": "allow"} in payload["permissions"]
    assert payload["compaction"] == {"auto": True, "buffer": op.OPENCODE_COMPACTION_RESERVED}
    assert payload["skills"] == [str(tmp_path / "skill")]
    # 読ませ方は XDG_CONFIG_HOME。v2 は OPENCODE_CONFIG を見ない。
    assert op._runtime_env("v2", config) == {"XDG_CONFIG_HOME": str(config)}
    assert op._runtime_env("v1", config) == {"OPENCODE_CONFIG": str(config)}
    # v2 の runtime config は directory なので、まとめて消えること。
    op._discard_runtime_config("v2", config)
    assert not config.exists()


def test_v2_run_argv_drops_dir_and_adds_standalone(tmp_path):
    from app.integrations.opencode import provider as op

    binary = tmp_path / "opencode2"
    project = tmp_path / "project"
    v1 = op._run_argv("v1", binary, "hello", "auto", project)
    v2 = op._run_argv("v2", binary, "hello", "auto", project)
    assert v1[:3] == [str(binary), "run", "hello"] and "--dir" in v1
    assert "--standalone" not in v1
    # v2 は --dir を持たない（作業ディレクトリで決まる）。常駐 service へ繋がない。
    assert v2[:3] == [str(binary), "run", "hello"] and "--dir" not in v2
    assert "--standalone" in v2 and "--format" in v2 and "json" in v2


def test_active_runtime_falls_back_to_the_installed_one(monkeypatch, tmp_path):
    from app.features import registry
    from app.integrations.opencode import provider as op

    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(op.files, "resolve", lambda path: tmp_path)
    op.save_settings({"base_url": "http://127.0.0.1:8765/v1", "model": "auto", "runtime": "v1"})
    # 設定は v1 だが、導入されているのが v2 だけなら v2 で動かす。
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: feature_id == "opencode-v2")
    assert op.active_runtime() == "v2"
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: feature_id == "opencode")
    assert op.active_runtime() == "v1"
    # どちらも無効なら実行時に断る。
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: False)
    try:
        op.active_binary()
    except op.CodeAgentError as exc:
        assert "有効ではありません" in str(exc)
    else:
        raise AssertionError("無効なのに実行を許した")


def test_v2_error_event_is_reported(tmp_path):
    from app.integrations.opencode import provider as op

    assert op._event_error({"error": {"name": "AuthError"}}) == "AuthError"
    assert op._event_error({"error": {"type": "provider.invalid-output",
                                      "message": "stream ended"}}) == "provider.invalid-output"
    assert op._event_error({"error": None}) == "provider error"


def test_v2_caps_tool_output_against_the_window_and_formats_after_edits(monkeypatch, tmp_path):
    from app.integrations.opencode import provider as op

    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(op, "_skill_paths", lambda: [])
    monkeypatch.setattr(op, "_api_key_for", lambda base_url: "cdk-test")
    monkeypatch.setattr(op, "gateway_client_header", lambda: "x-control-deck-client")
    monkeypatch.setattr(op, "_allowed_directories", lambda: {})
    monkeypatch.setattr(op, "_forbidden_commands", lambda: {})

    # 広い窓（131,072）なら v2 の既定（51,200）を超えない。
    monkeypatch.setattr(op, "_model_limits",
                        lambda model: {"context": 131072, "output": 8192, "input": 122880})
    wide = json.loads((op._runtime_config("w", "http://127.0.0.1:1/v1", "auto", runtime="v2")
                       / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert wide["tool_output"]["max_bytes"] <= op.TOOL_OUTPUT_DEFAULT_BYTES
    assert wide["formatter"] is True

    # 狭い窓（8,192）だと 1 件が窓を食い尽くしうるので、既定より厳しく絞る。
    monkeypatch.setattr(op, "_model_limits",
                        lambda model: {"context": 8192, "output": 2048, "input": 6144})
    narrow = json.loads((op._runtime_config("n", "http://127.0.0.1:1/v1", "auto", runtime="v2")
                         / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert narrow["tool_output"]["max_bytes"] < wide["tool_output"]["max_bytes"]
    assert narrow["tool_output"]["max_bytes"] >= op.TOOL_OUTPUT_MIN_BYTES

    # 窓が分からないときは v2 の既定に任せる（勝手に絞らない）。
    monkeypatch.setattr(op, "_model_limits", lambda model: None)
    unknown = json.loads((op._runtime_config("u", "http://127.0.0.1:1/v1", "auto", runtime="v2")
                          / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert unknown["tool_output"] == {"max_lines": op.TOOL_OUTPUT_DEFAULT_LINES,
                                      "max_bytes": op.TOOL_OUTPUT_DEFAULT_BYTES}
    # v1 側にこれらの鍵は無い。従来の形を変えない。
    monkeypatch.setattr(op, "_model_limits",
                        lambda model: {"context": 131072, "output": 8192, "input": 122880})
    v1 = json.loads(op._runtime_config("v1", "http://127.0.0.1:1/v1", "auto").read_text(encoding="utf-8"))
    assert "tool_output" not in v1 and "formatter" not in v1


def test_each_page_pins_its_own_runtime(monkeypatch, tmp_path):
    """v1画面とv2画面のボタンが、それぞれ自分の系列で起動する。"""
    from app.features import registry
    from app.integrations.opencode import provider as op

    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(op.files, "resolve", lambda path: tmp_path)
    op.save_settings({"base_url": "http://127.0.0.1:8765/v1", "model": "auto", "runtime": "v1"})
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: True)

    # 設定は v1 のままでも、画面が v2 を名指しすれば v2 で起動する。
    assert op.active_runtime("v2") == "v2"
    assert op.active_runtime("v1") == "v1"
    assert op.active_runtime("") == "v1"
    # 知らない値は設定値へ落とす（任意の系列名を受け付けない）。
    assert op.active_runtime("v9") == "v1"

    # 名指しした系列が無効なら、有効なほうへ落とす。
    monkeypatch.setattr(registry, "is_enabled", lambda feature_id: feature_id == "opencode")
    assert op.active_runtime("v2") == "v1"


def test_v2_tui_settings_survive_across_sessions(monkeypatch, tmp_path):
    """keybind を変えても次の起動で消えない（config directory は毎回作り替えられる）。"""
    from app.integrations.opencode import provider as op

    monkeypatch.setattr(op, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(op, "_skill_paths", lambda: [])
    monkeypatch.setattr(op, "_api_key_for", lambda base_url: "cdk-test")
    monkeypatch.setattr(op, "gateway_client_header", lambda: "x-control-deck-client")
    monkeypatch.setattr(op, "_allowed_directories", lambda: {})
    monkeypatch.setattr(op, "_forbidden_commands", lambda: {})
    monkeypatch.setattr(op, "_model_limits", lambda model: None)

    first = op._runtime_config("tui-1", "http://127.0.0.1:1/v1", "auto", runtime="v2")
    shared = op.cli_config_path()
    link = first / "opencode" / "cli.json"
    assert link.is_symlink() and link.readlink() == shared
    # 既定は v1 の操作に揃える（plan 切替は tab / shift+tab）。
    keybinds = json.loads(shared.read_text(encoding="utf-8"))["keybinds"]
    assert keybinds["agent.cycle"] == "tab"
    assert keybinds["agent.cycle.reverse"] == "shift+tab"
    # v2 が新機能へ割り当て直したキーは戻さない（戻すと terminal 系が潰れる）。
    assert "theme.switch" not in keybinds and "session.child.first" not in keybinds

    # 利用者が共有の1枚を書き換える。
    shared.write_text(json.dumps({"keybinds": {"agent.cycle": "ctrl+g"}}), encoding="utf-8")
    # 別の session（別 job）でも、作り直した config directory から同じ設定が見える。
    second = op._runtime_config("chat-xyz", "http://127.0.0.1:1/v1", "auto", runtime="v2")
    seen = json.loads((second / "opencode" / "cli.json").read_text(encoding="utf-8"))
    assert seen["keybinds"]["agent.cycle"] == "ctrl+g"
    # 同じ TUI をもう一度起動しても、共有の1枚は作り直されない。
    op._runtime_config("tui-1", "http://127.0.0.1:1/v1", "auto", runtime="v2")
    assert json.loads(shared.read_text(encoding="utf-8"))["keybinds"]["agent.cycle"] == "ctrl+g"

    # TUI が symlink を実体ファイルへ置き換えたら、そちらを尊重して触らない。
    link.unlink()
    link.write_text(json.dumps({"keybinds": {"agent.cycle": "ctrl+j"}}), encoding="utf-8")
    op._runtime_config("tui-1", "http://127.0.0.1:1/v1", "auto", runtime="v2")
    assert not link.is_symlink()
    assert json.loads(link.read_text(encoding="utf-8"))["keybinds"]["agent.cycle"] == "ctrl+j"
