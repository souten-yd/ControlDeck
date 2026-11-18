"""More Maya models beside the first: own alias, port, unit and config; registered from files on disk only.

No engine, GPU, systemd or network: the setup and systemctl are replaced.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from app.models_mgmt import maya_s


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(maya_s, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(maya_s.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(maya_s, "_ensure_alias_port_available", lambda: None)
    monkeypatch.setattr(maya_s, "_port_busy", lambda *_a: False)
    from app.models_mgmt import large_model_lifecycle, local_llm, runtime_policy
    monkeypatch.setattr(runtime_policy, "_path", lambda: tmp_path / "policy.json")
    monkeypatch.setattr(large_model_lifecycle, "_usage_path", lambda: tmp_path / "usage.json")
    monkeypatch.setattr(local_llm, "port_taken_by_other_runtime", lambda *_a: "")
    monkeypatch.setattr(local_llm, "alias_taken_by_other_runtime", lambda *_a: "")
    source = tmp_path / "runtimes" / "maya-s" / "engine"
    source.mkdir(parents=True)
    (source / "maya.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (source.parent / "managed.json").write_text(json.dumps({"tag": "v1.1.64"}), encoding="utf-8")
    # The first model, prepared as before.
    first = source / "maya-maya-s-v2-iq2_xxs-hip.json"
    first.write_text(json.dumps({"args": ["--max-context", "1048576"], "env": {"MAYA_GLM_PCIE_SHARE": "0.3"}}))
    (source.parent / "model-prepared.json").write_text(json.dumps({"model": "glm-maya-s"}))
    running: set[str] = set()
    calls: list[list[str]] = []

    def fake_exec(argv, **_kw):
        calls.append(list(argv))
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return SimpleNamespace(stdout="active\n" if argv[3] in running else "inactive\n", returncode=0)
        if argv[:3] == ["systemctl", "--user", "stop"]:
            running.discard(argv[3])
        if argv[:2] == ["systemd-run", "--user"]:
            running.add(argv[argv.index("--unit") + 1] + ".service")
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(maya_s, "_exec", fake_exec)
    return SimpleNamespace(root=tmp_path, source=source, first=first, running=running, calls=calls)


def _register_row(engine, alias="maya-deepseek-xs", config="maya-ds4-hip.json", port=8100):
    (engine.source / config).write_text(json.dumps({"args": ["--max-context", "65536"]}))
    rows = maya_s._registry() + [{"alias": alias, "setup_id": "deepseek-maya-xs", "family": "deepseek",
                                  "tier": "Maya-XS", "config": config, "port": port, "size_gb": 282.4}]
    maya_s._save_registry(rows)


def test_first_model_keeps_its_identity_beside_registered_ones(engine):
    # MiniMax's Maya-S writes maya-maya-s-hip.json: it must not make the first model's config ambiguous.
    _register_row(engine, "maya-minimax-s", "maya-maya-s-hip.json", 8101)
    _register_row(engine)
    assert maya_s._find_config() == engine.first
    rows = {row["alias"]: row for row in maya_s.list_instances()}
    assert set(rows) == {"maya-s", "maya-minimax-s", "maya-deepseek-xs"}
    assert rows["maya-s"]["port"] == 8099 and rows["maya-s"]["display_name"] == "GLM-5.3-Flash · Maya-S"
    assert rows["maya-deepseek-xs"]["port"] == 8100
    assert rows["maya-deepseek-xs"]["display_name"] == "DeepSeek-V4.1-Flash · Maya-XS"
    assert rows["maya-deepseek-xs"]["ctx_size"] == 65536
    assert maya_s.residency_key(rows["maya-s"]) == "maya-s:glm-5.3-flash"
    assert maya_s.residency_key(rows["maya-deepseek-xs"]) == "maya-s:deepseek:maya-xs"
    assert maya_s._unit("maya-s") == "control-deck-maya-s.service"
    assert maya_s._unit("maya-deepseek-xs") == "control-deck-maya-deepseek-xs.service"


def test_starting_one_model_stops_the_other_first(engine):
    _register_row(engine)
    engine.running.add("control-deck-maya-s.service")
    assert maya_s.start_instance("maya-deepseek-xs") == (True, "")
    assert engine.running == {"control-deck-maya-deepseek-xs.service"}
    run = next(c for c in engine.calls if c[:2] == ["systemd-run", "--user"])
    launch_config = engine.source / "maya-ds4-hip.json"
    assert str(launch_config) in run and "8100" in run
    assert "--reserve-mib" in json.loads(launch_config.read_text())["args"]
    assert json.loads(launch_config.read_text())["control_deck_vram_margin"]["original"] is None
    assert maya_s.running_alias() == "maya-deepseek-xs"


def test_stop_without_alias_stops_whichever_runs(engine):
    _register_row(engine)
    engine.running.add("control-deck-maya-deepseek-xs.service")
    assert maya_s.is_running()
    assert maya_s.stop_instance() == (True, "")
    assert engine.running == set() and not maya_s.is_running()
    assert maya_s.stop_instance("maya-unknown") == (False, "Mayaのモデル名が不正です")


def _catalog(engine, monkeypatch, **item):
    row = {"family": "glm", "setup_id": "glm-maya-s24", "tier": "Maya-S24", "model": "GLM-5.3-Flash",
           "size_gb": 94.7, "memory_gb": 95, "published": True, "blockers": [],
           "model_dir": str(engine.root / "LLM" / "Maya-S24"), "hf": "hf download x",
           "files": [{"name": "a.gguf", "size": 1, "present": True, "required": True, "what": "model"},
                     {"name": "mmproj.gguf", "size": None, "present": False, "required": False, "what": "vision"}]}
    row.update(item)
    monkeypatch.setattr(maya_s, "_probe", lambda _folder: {"quants": [row], "build": {}, "version": "1.1.64"})
    monkeypatch.setattr(maya_s, "_folder", lambda folder: engine.root / "LLM")
    return row


def test_catalog_is_ready_only_with_every_required_file(engine, monkeypatch):
    _catalog(engine, monkeypatch)
    item = maya_s.catalog("/LLM")["quants"][0]
    assert item["ready"] and item["alias"] == "maya-glm-s24" and item["registered"] == ""
    _catalog(engine, monkeypatch, files=[{"name": "a.gguf", "size": 2_000_000_000, "present": False,
                                          "required": True}])
    item = maya_s.catalog("/LLM")["quants"][0]
    assert not item["ready"] and item["missing_gb"] == 2.0
    _catalog(engine, monkeypatch, blockers=["tokenizers がありません"])
    assert not maya_s.catalog("/LLM")["quants"][0]["ready"]
    # The first model is shown as registered under its own alias.
    _catalog(engine, monkeypatch, setup_id="glm-maya-s", tier="Maya-S")
    assert maya_s.catalog("/LLM")["quants"][0]["registered"] == "maya-s"


def test_register_refuses_missing_files_without_running_the_setup(engine, monkeypatch):
    _catalog(engine, monkeypatch, files=[{"name": "a.gguf", "size": 5, "present": False, "required": True}])
    ran = []
    monkeypatch.setattr(maya_s.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(maya_s.MayaError, match="先に取得してください: hf download x"):
        maya_s.register("glm-maya-s24", "/LLM")
    assert ran == []


def test_register_refuses_while_a_maya_model_runs(engine, monkeypatch):
    _catalog(engine, monkeypatch)
    engine.running.add("control-deck-maya-s.service")
    with pytest.raises(maya_s.MayaError, match="停止してから"):
        maya_s.register("glm-maya-s24", "/LLM")


def test_register_runs_the_setup_offline_and_keeps_other_configs(engine, monkeypatch):
    _catalog(engine, monkeypatch)
    _register_row(engine)
    before_first = engine.first.read_bytes()
    seen = {}

    def setup(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        # The setup writes its own config and (wrongly) touches the first model's.
        (engine.source / "maya-maya-s24-hip.json").write_text(json.dumps({"args": [], "port": 8080}))
        engine.first.write_text("{}")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(maya_s.subprocess, "run", setup)
    with pytest.raises(maya_s.MayaError, match="同じ設定ファイル"):
        maya_s.register("glm-maya-s24", "/LLM")
    assert engine.first.read_bytes() == before_first
    assert not (engine.source / "maya-maya-s24-hip.json").exists()

    def clean_setup(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        (engine.source / "maya-maya-s24-hip.json").write_text(json.dumps({"args": [], "port": 8080}))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(maya_s.subprocess, "run", clean_setup)
    row = maya_s.register("glm-maya-s24", "/LLM", context=131072)
    assert row["alias"] == "maya-glm-s24" and row["port"] == 8101  # 8100 is the DeepSeek one
    argv = seen["argv"]
    assert "--download-model" not in argv and "--no-vision" in argv  # vision files are not there
    assert argv[argv.index("--gguf-dir") + 1].endswith("Maya-S24") and argv[argv.index("--context") + 1] == "131072"
    assert seen["env"]["HTTPS_PROXY"] == "http://127.0.0.1:9" and seen["env"]["HF_HUB_OFFLINE"] == "1"
    cfg = json.loads((engine.source / "maya-maya-s24-hip.json").read_text())
    assert cfg["port"] == 8101 and cfg["fit_max_tokens"] is True
    assert engine.first.read_bytes() == before_first
    assert maya_s._find_config() == engine.first
    with pytest.raises(maya_s.MayaError, match="登録済み"):
        maya_s.register("glm-maya-s24", "/LLM")


def test_unregister_removes_only_the_config(engine, tmp_path):
    model = tmp_path / "LLM" / "x.gguf"
    model.parent.mkdir()
    model.write_text("weights")
    _register_row(engine)
    engine.running.add("control-deck-maya-deepseek-xs.service")
    with pytest.raises(maya_s.MayaError, match="停止してから"):
        maya_s.unregister("maya-deepseek-xs")
    engine.running.clear()
    assert maya_s.unregister("maya-deepseek-xs")["removed"]
    assert not (engine.source / "maya-ds4-hip.json").exists() and model.exists()
    assert [row["alias"] for row in maya_s.list_instances()] == ["maya-s"]


def test_the_first_model_can_be_removed_but_keeps_its_engine_config(engine):
    engine.running.add("control-deck-maya-s.service")
    with pytest.raises(maya_s.MayaError, match="停止してから"):
        maya_s.unregister("maya-s")
    engine.running.clear()
    assert maya_s.unregister("maya-s")["removed"]
    assert maya_s.list_instances() == []
    # The tuned engine config (env such as MAYA_GLM_PCIE_SHARE) stays for a later re-prepare.
    assert (engine.source / "maya-maya-s-v2-iq2_xxs-hip.json").exists()


def test_registry_ignores_rows_that_could_escape_the_engine_folder(engine):
    maya_s._save_registry([{"alias": "maya-glm-x", "config": "../../etc/passwd", "port": 8100},
                           {"alias": "rm -rf", "config": "maya-x.json", "port": 8100}])
    assert maya_s._registry() == []


# ---- ダウンロードして登録・ファイルの削除・更新後の準備し直し ----


def _ds_catalog(engine, monkeypatch, root, *, present=False):
    files = []
    for name, what, required in (("Maya-XS/m-1.gguf", "model", True), ("Maya-XS/t-4.gguf", "tables", True),
                                 ("dspark/d.bin", "drafter", False)):
        path = root / name
        files.append({"name": Path(name).name, "path": str(path), "size": 10, "required": required, "what": what,
                      "present": present or path.is_file(), "sha256": ""})
    row = {"family": "deepseek", "setup_id": "deepseek-maya-xs", "tier": "Maya-XS", "model": "DeepSeek-V4.1-Flash",
           "size_gb": 0.1, "memory_gb": 1, "published": True, "blockers": [], "repo": "peasantsmith/ds",
           "revision": "c33d87b", "folder_name": "DeepSeek-V4.1-Flash-1017-Maya-GGUF",
           "repo_root": str(root), "model_dir": str(root / "Maya-XS"), "models_dir": str(root.parent), "files": files}
    if root.name != row["folder_name"]:
        row["folder_error"] = "名前が違います"
    return row


def test_download_plan_uses_the_repo_named_folder_and_checks_space(engine, monkeypatch):
    base = engine.root / "LLM"
    base.mkdir(exist_ok=True)
    item = _ds_catalog(engine, monkeypatch, base)
    plan = maya_s._download_plan(item, base)
    assert plan["download_root"] == str(base / "DeepSeek-V4.1-Flash-1017-Maya-GGUF")
    assert plan["download_files"] == ["m-1.gguf", "t-4.gguf", "d.bin"] and plan["can_download"]
    monkeypatch.setattr(maya_s.shutil, "disk_usage", lambda _p: SimpleNamespace(free=1))
    assert not maya_s._download_plan(item, base)["can_download"]


def test_download_fetches_only_the_manifest_files_at_the_pinned_revision(engine, monkeypatch):
    base = engine.root / "LLM"
    root = base / "DeepSeek-V4.1-Flash-1017-Maya-GGUF"
    base.mkdir(exist_ok=True)
    monkeypatch.setattr(maya_s, "_folder", lambda folder: Path(folder))
    monkeypatch.setattr(maya_s, "_probe", lambda folder: {"quants": [_ds_catalog(engine, monkeypatch, Path(folder))],
                                                         "build": {}, "version": "x"})
    monkeypatch.setattr(maya_s, "_hf_bin", lambda: "/usr/bin/hf")
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        for i, a in enumerate(argv):
            if a == "--include":
                p = root / argv[i + 1]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x" * 10)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(maya_s.subprocess, "run", fake_run)
    item, got = maya_s._download("deepseek-maya-xs", str(base))
    argv = seen[0]
    assert got == root and argv[:3] == ["/usr/bin/hf", "download", "peasantsmith/ds"]
    assert argv[argv.index("--revision") + 1] == "c33d87b" and argv[argv.index("--local-dir") + 1] == str(root)
    includes = [argv[i + 1] for i, a in enumerate(argv) if a == "--include"]
    assert includes == ["Maya-XS/m-1.gguf", "Maya-XS/t-4.gguf", "dspark/d.bin"]
    # Everything there now: a second run downloads nothing.
    seen.clear()
    maya_s._download("deepseek-maya-xs", str(base))
    assert seen == []


def test_download_refuses_without_space(engine, monkeypatch):
    base = engine.root / "LLM"
    base.mkdir(exist_ok=True)
    monkeypatch.setattr(maya_s, "_folder", lambda folder: Path(folder))
    monkeypatch.setattr(maya_s, "_probe", lambda folder: {"quants": [_ds_catalog(engine, monkeypatch, Path(folder))],
                                                         "build": {}, "version": "x"})
    monkeypatch.setattr(maya_s.shutil, "disk_usage", lambda _p: SimpleNamespace(free=1))
    ran = []
    monkeypatch.setattr(maya_s.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(maya_s.MayaError, match="空き容量"):
        maya_s._download("deepseek-maya-xs", str(base))
    assert ran == []


def test_deleting_files_keeps_shared_tables_and_anything_outside(engine, monkeypatch):
    root = engine.root / "LLM" / "DeepSeek-V4.1-Flash-1017-Maya-GGUF"
    for name in ("Maya-XS/m-1.gguf", "Maya-XS/t-4.gguf", "dspark/d.bin", "keep.txt"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(b"x" * 10)
    _register_row(engine)
    _register_row(engine, "maya-deepseek-s", "maya-ds4s-hip.json", 8101)
    rows = maya_s._registry()
    for row in rows:
        row["folder"] = str(root)
    rows[1]["setup_id"] = "deepseek-maya-s"
    maya_s._save_registry(rows)
    other = {"setup_id": "deepseek-maya-s", "files": [{"path": str(root / "Maya-XS/t-4.gguf")}]}

    def probe(folder):
        return {"quants": [_ds_catalog(engine, monkeypatch, Path(folder)), other], "build": {}, "version": "x"}

    monkeypatch.setattr(maya_s, "_probe", probe)
    result = maya_s.unregister("maya-deepseek-xs", delete_files=True)
    assert result["deleted_files"] == 2
    assert not (root / "Maya-XS/m-1.gguf").exists() and not (root / "dspark").exists()
    assert (root / "Maya-XS/t-4.gguf").exists(), "共有の n-gram 表はほかのモデルが使う"
    assert (root / "keep.txt").exists()
    assert [r["alias"] for r in maya_s._registry()] == ["maya-deepseek-s"]


def test_refresh_after_an_engine_update_keeps_port_and_tuning(engine, monkeypatch):
    root = engine.root / "LLM" / "DeepSeek-V4.1-Flash-1017-Maya-GGUF"
    root.mkdir(parents=True)
    _register_row(engine)
    rows = maya_s._registry()
    rows[0]["folder"] = str(root)
    maya_s._save_registry(rows)
    config = engine.source / "maya-ds4-hip.json"
    config.write_text(json.dumps({"args": ["--max-context", "131072"], "env": {"MAYA_DS4_TUNE": "1"},
                                  "reasoning_effort": "max"}))
    monkeypatch.setattr(maya_s, "_folder", lambda folder: Path(folder))
    monkeypatch.setattr(maya_s, "_probe", lambda folder: {"quants": [_ds_catalog(engine, monkeypatch, Path(folder),
                                                                                  present=True)],
                                                         "build": {}, "version": "x"})

    def setup(argv, **kwargs):
        config.write_text(json.dumps({"args": ["--max-context", "131072"], "env": {"NEW": "1"}}))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(maya_s.subprocess, "run", setup)
    assert maya_s.refresh_registered() == [{"alias": "maya-deepseek-xs", "status": "refreshed"}]
    cfg = json.loads(config.read_text())
    assert cfg["env"] == {"NEW": "1", "MAYA_DS4_TUNE": "1"} and cfg["reasoning_effort"] == "max"
    row = maya_s._registry()[0]
    assert row["port"] == 8100 and row["update_available"] is False

    # A new version lists files that are not there: the old registration stays and is flagged.
    monkeypatch.setattr(maya_s, "_probe", lambda folder: {"quants": [_ds_catalog(engine, monkeypatch, Path(folder))],
                                                         "build": {}, "version": "y"})
    assert maya_s.refresh_registered()[0]["status"] == "new_version"
    assert maya_s.get_instance("maya-deepseek-xs")["update_available"] is True


def test_glm_maya_s_from_the_sheet_comes_back_as_the_first_model(engine, monkeypatch):
    maya_s.unregister("maya-s")  # files were removed; the first model is gone from the list
    assert maya_s.list_instances() == []
    _catalog(engine, monkeypatch, setup_id="glm-maya-s", tier="Maya-S")

    def setup(argv, **kwargs):
        assert argv[argv.index("--port") + 1] == "8099"
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(maya_s.subprocess, "run", setup)
    row = maya_s.register("glm-maya-s", "/LLM")
    assert row["alias"] == "maya-s" and row["port"] == 8099
    assert json.loads(engine.first.read_text())["env"]["MAYA_GLM_PCIE_SHARE"] == "0.3"
    assert maya_s._registry() == []


def test_registered_model_settings_write_the_engine_config(engine, admin_client=None):
    """登録したモデル（DS-XS）の個別設定（2026-11-17 の依頼: 設定が一切無い）。エンジンの args・config へ書き、
    既定（auto）に戻した項目はオプションごと外す。起動中は起動と停止の設定だけ変えられる。"""
    _register_row(engine)
    current = maya_s.instance_settings("maya-deepseek-xs")
    assert current["context"] == 65536 and current["dspark"] == "on" and current["mmap_experts"] == "auto"
    maya_s.save_instance_settings("maya-deepseek-xs", {
        "context": 131072, "dspark": "off", "cpu_threads": 12, "vram_experts": 0.5, "ssd_cache": "auto",
        "reasoning_effort": "medium", "thinking_budget": 8192, "temperature": 0.6, "top_p": 0.95})
    cfg = json.loads((engine.source / "maya-ds4-hip.json").read_text())
    args = cfg["args"]
    assert args[args.index("--max-context") + 1] == "131072"
    assert args[args.index("--dspark") + 1] == "off"
    assert args[args.index("--cpu-threads") + 1] == "12"
    assert args[args.index("--vram-experts") + 1] == "0.5"
    assert args[args.index("--ssd-cache") + 1] == "auto"
    assert "--mmap-experts" not in args
    assert cfg["reasoning_effort"] == "medium" and cfg["thinking_budget"] == 8192
    assert cfg["sampling"] == {"temperature": 0.6, "top_p": 0.95}
    # 既定へ戻すとオプションごと外す
    maya_s.save_instance_settings("maya-deepseek-xs", {"cpu_threads": 0, "vram_experts": 0, "ssd_cache": "off"})
    args = json.loads((engine.source / "maya-ds4-hip.json").read_text())["args"]
    assert "--cpu-threads" not in args and "--vram-experts" not in args and "--ssd-cache" not in args
    # 起動中: 起動と停止の設定は変えられ、エンジンの設定は断る
    engine.running.add(maya_s._unit("maya-deepseek-xs"))
    assert maya_s.save_instance_settings("maya-deepseek-xs", {"idle_exclude": True})["idle_exclude"] is True
    with pytest.raises(maya_s.MayaError):
        maya_s.save_instance_settings("maya-deepseek-xs", {"context": 65536})
    with pytest.raises(maya_s.MayaError):
        maya_s.instance_settings("maya-s")                      # 最初のモデルは従来の設定画面


def test_deepseek_vision_is_detected_from_the_pack_and_can_be_switched(engine, tmp_path):
    """DeepSeek（maya-ds4）は pack に vision tower が入っていて --vision off でなければ画像を読む（Maya #416）。
    以前は GLM の見方（config の "vision"）だけで、画像を読めても「読めない」扱いだった（2026-11-18、
    ゲートウェイの画像の解析が断り、モデル画面に印が出なかった）。個別設定で入／切できる。tower が無ければ入にできない。"""
    pack = tmp_path / "maya-ds4"
    pack.mkdir()
    (pack / "ds4.txt").write_text("vision.num_hidden_layers 32\n")
    (engine.source / "maya-ds4-hip.json").write_text(json.dumps({
        "exe": "/x/build-hip/maya-ds4", "args": ["--pack", str(pack), "--vision", "off", "--max-context", "65536"]}))
    rows = maya_s._registry() + [{"alias": "maya-deepseek-xs", "setup_id": "deepseek-maya-xs", "family": "deepseek",
                                  "tier": "Maya-XS", "config": "maya-ds4-hip.json", "port": 8100, "size_gb": 282.4}]
    maya_s._save_registry(rows)
    config = engine.source / "maya-ds4-hip.json"
    assert maya_s.get_instance("maya-deepseek-xs")["vision"] is False          # --vision off
    s = maya_s.instance_settings("maya-deepseek-xs")
    assert s["vision"] == "off" and s["vision_available"] is True
    maya_s.save_instance_settings("maya-deepseek-xs", {"vision": "on"})
    assert "--vision" not in json.loads(config.read_text())["args"]           # 既定（読む）に戻す
    assert maya_s.get_instance("maya-deepseek-xs")["vision"] is True
    maya_s.save_instance_settings("maya-deepseek-xs", {"vision": "off"})
    args = json.loads(config.read_text())["args"]
    assert args[args.index("--vision") + 1] == "off"
    # tower が入っていない pack では入にできない
    (pack / "ds4.txt").write_text("layers 61\n")
    assert maya_s.instance_settings("maya-deepseek-xs")["vision_available"] is False
    with pytest.raises(maya_s.MayaError, match="vision tower"):
        maya_s.save_instance_settings("maya-deepseek-xs", {"vision": "on"})
    assert maya_s.get_instance("maya-deepseek-xs")["vision"] is False


def test_vision_flag_is_written_only_when_chosen_and_a_refusal_saves_nothing(engine, tmp_path):
    """レビュー指摘（2026-11-18）: tower の無いモデルでほかの項目を保存すると --vision off まで書いていた
    （あとで tower を入れても切のまま残る）。断るときは起動の設定も保存しない。相対の --pack はエンジンの作業フォルダから。"""
    work = tmp_path / "engine-cwd"
    (work / "packs" / "ds4").mkdir(parents=True)
    (work / "packs" / "ds4" / "ds4.txt").write_text("layers 61\n")          # tower 無し
    config = engine.source / "maya-ds4-hip.json"
    config.write_text(json.dumps({"exe": "/x/maya-ds4", "cwd": str(work),
                                  "args": ["--pack", "packs/ds4", "--max-context", "65536"]}))
    rows = maya_s._registry() + [{"alias": "maya-deepseek-xs", "setup_id": "deepseek-maya-xs", "family": "deepseek",
                                  "tier": "Maya-XS", "config": "maya-ds4-hip.json", "port": 8100, "size_gb": 282.4}]
    maya_s._save_registry(rows)
    maya_s.save_instance_settings("maya-deepseek-xs", {"cpu_threads": 8})
    assert "--vision" not in json.loads(config.read_text())["args"]
    before = (engine.root / "runtimes" / "maya-s" / "settings.json")
    saved_before = before.read_text() if before.exists() else ""
    with pytest.raises(maya_s.MayaError):
        maya_s.save_instance_settings("maya-deepseek-xs", {"auto_start": True, "vision": "on"})
    assert (before.read_text() if before.exists() else "") == saved_before   # 起動の設定も書いていない
    # tower を入れた（設定し直し）→ 相対の pack でも読めると分かり、切のまま残っていない
    (work / "packs" / "ds4" / "ds4.txt").write_text("vision.num_hidden_layers 32\n")
    assert maya_s.get_instance("maya-deepseek-xs")["vision"] is True
    assert maya_s.instance_settings("maya-deepseek-xs")["vision"] == "on"


def test_selftest_warns_when_the_running_engine_and_the_vision_judgement_disagree(monkeypatch):
    """同じ食い違い（エンジンは読めるのに判定は読めない）が起きたら、自己診断で気付けるように。"""
    import httpx

    from app.diagnostics import selftest

    monkeypatch.setattr(maya_s, "list_instances", lambda: [{"alias": "maya-deepseek-xs", "port": 8101,
                                                            "loaded": True, "vision": False}])
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(json=lambda: {"images": True}))
    status, text = selftest._maya_vision()
    assert status == "warn" and "maya-deepseek-xs" in text
    monkeypatch.setattr(maya_s, "list_instances", lambda: [{"alias": "maya-deepseek-xs", "port": 8101,
                                                            "loaded": True, "vision": True}])
    assert selftest._maya_vision()[0] is True
