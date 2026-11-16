"""More Maya models beside the first: own alias, port, unit and config; registered from files on disk only.

No engine, GPU, systemd or network: the setup and systemctl are replaced.
"""
from __future__ import annotations

import json
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
    with pytest.raises(maya_s.MayaError):
        maya_s.unregister("maya-s")
    engine.running.add("control-deck-maya-deepseek-xs.service")
    with pytest.raises(maya_s.MayaError, match="停止してから"):
        maya_s.unregister("maya-deepseek-xs")
    engine.running.clear()
    assert maya_s.unregister("maya-deepseek-xs")["removed"]
    assert not (engine.source / "maya-ds4-hip.json").exists() and model.exists()
    assert [row["alias"] for row in maya_s.list_instances()] == ["maya-s"]


def test_registry_ignores_rows_that_could_escape_the_engine_folder(engine):
    maya_s._save_registry([{"alias": "maya-glm-x", "config": "../../etc/passwd", "port": 8100},
                           {"alias": "rm -rf", "config": "maya-x.json", "port": 8100}])
    assert maya_s._registry() == []
