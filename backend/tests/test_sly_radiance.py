"""Sly-Radiance（vLLM 改造版）のランタイム: 登録の検証・起動の引数・目録・フォルダの読み取り・共通部品との結びつき。

目的: `vllm serve` の引数が配列で組まれ（shell 不使用）、127.0.0.1 だけに待ち受け、モデル・先読みのフォルダが
read-only で渡り、先読み 0 や先読みモデル無しでは speculative を付けず、量子化の切り替え・追加の環境変数が
検証を通ったものだけ渡ること。GPU の使用率が共通の VRAM の余裕を超えないこと。他のランタイム（Radiance）と
同じ扱い（載せ替え・並列の枠・ゲートウェイ・一覧）に入っていること。
根拠: 2026-11-27 利用者「Sly-Radiance を導入し、Radiance と同様のメニューや導入方法・他のランタイムと同様に利用」。
起動の引数は公式 README の Quickstart（2026-11-27 確認）。実機の起動は別に確かめる（ここは全部モック）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.models_mgmt import container_runtime, large_model_lifecycle, local_llm, parallel_slots
from app.models_mgmt import sly_radiance as sly
from app.models_mgmt import sly_radiance_catalog as catalog


@pytest.fixture
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(sly, "root", lambda: tmp_path / "sly")
    monkeypatch.setattr(sly, "installed", lambda: True)
    monkeypatch.setattr(sly, "_running", lambda alias: False)
    model = tmp_path / "Swift-model"
    model.mkdir()
    (model / "config.json").write_text(json.dumps({"quantization_config": {"quant_method": "compressed-tensors"}}))
    (model / "model-00001.safetensors").write_bytes(b"x" * 10)
    drafter = tmp_path / "drafter"
    drafter.mkdir()
    (drafter / "config.json").write_text("{}")
    from app.files import service as files
    monkeypatch.setattr(files, "resolve", lambda value: Path(value))
    monkeypatch.setattr(local_llm, "find", lambda alias: None)
    monkeypatch.setattr(local_llm, "port_taken_by_other_runtime", lambda *a: "")
    # 32 GiB のカード・共通の余裕 3 GiB
    from app.models_mgmt import strata_catalog, runtime_policy
    monkeypatch.setattr(strata_catalog, "machine_gib", lambda: (64.0, 32.0))
    monkeypatch.setattr(runtime_policy, "vram_margin_mib", lambda: 3072)
    return model, drafter


def test_registration_validates(managed):
    model, drafter = managed
    row = sly.save_instance("q", model_path=str(model), port=8100, drafter_path=str(drafter))
    assert row["runtime"] == "sly-radiance" and row["base_url"] == "http://127.0.0.1:8100/v1"
    assert row["n_parallel"] == 4 and row["ctx_size"] == 131072 and row["speculative_tokens"] == 7
    bad = [dict(max_num_seqs=9), dict(context=1_000_000), dict(kv_cache="bf16"), dict(prefill_chunk=1000),
           dict(speculative_tokens=16), dict(gpu_memory_utilization=0.99), dict(quantization="gptq"),
           dict(reasoning_effort="xhigh"), dict(port=8100)]
    for index, patch in enumerate(bad):
        with pytest.raises(sly.SlyRadianceError):
            sly.save_instance(f"b{index}", model_path=str(model), **{"port": 8200 + index, **patch})
    with pytest.raises(sly.SlyRadianceError):   # config.json の無いフォルダ
        sly.save_instance("nocfg", model_path=str(model.parent), port=8300)
    assert [i["alias"] for i in sly.list_instances()] == ["q"]


def test_engine_settings_are_validated(managed):
    model, _ = managed
    sly.save_instance("e", model_path=str(model), port=8100,
                      engine_settings={"RADIANCE_KV_GROUP_SIZE": "16", "R4D_X": "a,b"})
    for env in ({"PATH": "x"}, {"RADIANCE_A": "$(x)"}, {"RADIANCE_a": "1"}, {"RADIANCE_A": "x" * 65}):
        with pytest.raises(sly.SlyRadianceError):
            sly.save_instance("f", model_path=str(model), port=8101, engine_settings=env)


def test_folder_with_colon_is_refused(managed, tmp_path):
    odd = tmp_path / "a:b"
    odd.mkdir()
    (odd / "config.json").write_text("{}")
    with pytest.raises(sly.SlyRadianceError):
        sly.save_instance("c", model_path=str(odd), port=8100)


def test_launch_args_loopback_readonly_and_speculative(managed):
    model, drafter = managed
    sly.save_instance("q", model_path=str(model), drafter_path=str(drafter), port=8100, max_num_seqs=8)
    args = sly.launch_args("q", sly._read()["q"])
    assert isinstance(args, list) and args[1:3] == ["run", "--rm"] and "--ipc=host" in args
    joined = " ".join(args)
    assert f"{model}:/models/{model.name}:ro" in joined and f"{drafter}:/drafter/{drafter.name}:ro" in joined
    assert "--host 127.0.0.1 --port 8100" in joined and "--served-model-name q" in joined
    assert "--max-num-seqs 8" in joined and "--speculative-config.num_speculative_tokens 7" in joined
    assert "--network host" in joined and "keep-groups" in joined
    assert "[8,16,24,32,40,48,56,64]" in joined       # README の値と同じ（8 本 x 先読み 7）
    assert "/vllm" in joined and "/triton" in joined and "/aiter" in joined
    assert f"{sly.IMAGE}:{sly.PINNED_TAG}" in args
    # 先読みモデルが無い / 先読み 0 では speculative を付けず、drafter も mount しない
    sly.save_instance("nodraft", model_path=str(model), port=8101)
    sly.save_instance("zero", model_path=str(model), drafter_path=str(drafter), port=8102, speculative_tokens=0)
    for alias in ("nodraft", "zero"):
        text = " ".join(sly.launch_args(alias, sly._read()[alias]))
        assert "speculative" not in text and "/drafter/" not in text


def test_quantization_switches_and_kv4_and_thinking(managed):
    model, _ = managed
    env_of = lambda args: {args[i + 1].split("=")[0]: args[i + 1].split("=", 1)[1] for i, a in enumerate(args) if a == "-e"}
    sly.save_instance("quark", model_path=str(model), port=8100, quantization="quark")
    args = sly.launch_args("quark", sly._read()["quark"])
    assert "--quantization quark" in " ".join(args)
    sly.save_instance("nv", model_path=str(model), port=8101, quantization="nvfp4", kv_cache="kv4",
                      reasoning_effort="medium", engine_settings={"RADIANCE_ADAPTIVE_WIDTH": "off"})
    args = sly.launch_args("nv", sly._read()["nv"])
    env = env_of(args)
    assert "--quantization compressed-tensors" in " ".join(args) and env["RADIANCE_NVFP4_MXFP4"] == "1"
    assert env["KVCACHE_DTYPE"] == "kv4" and env["RADIANCE_ADAPTIVE_WIDTH"] == "off"   # 利用者の指定が既定に勝つ
    assert args[args.index("--default-chat-template-kwargs") + 1] == '{"reasoning_effort": "medium"}'
    sly.save_instance("paro", model_path=str(model), port=8102, quantization="paroquant")
    assert env_of(sly.launch_args("paro", sly._read()["paro"]))["RADIANCE_PAROQUANT"] == "1"


def test_gpu_utilization_leaves_the_common_margin(managed):
    # 32 GiB のカードで余裕 3 GiB: 0.98 のままだと 0.64 GiB しか残らないので 0.90 へ抑える
    assert sly.gpu_utilization(0.98) == 0.90
    assert sly.gpu_utilization(0.70) == 0.70


def test_inspect_folder_suggests_quantization(managed, tmp_path):
    model, _ = managed
    info = sly.inspect_folder(str(model))
    assert info["safetensors_count"] == 1 and info["total_bytes"] == 10 and info["suggested_quantization"] == ""
    quark = tmp_path / "quark"
    quark.mkdir()
    (quark / "config.json").write_text(json.dumps({"quantization_config": {"quant_method": "quark"}}))
    assert sly.inspect_folder(str(quark))["suggested_quantization"] == "quark"
    assert sly.inspect_folder(str(quark))["ready"] is False
    nv = tmp_path / "nv"
    nv.mkdir()
    (nv / "config.json").write_text(json.dumps({"quantization_config": {
        "quant_method": "compressed-tensors", "format": "nvfp4-pack-quantized"}}))
    assert sly.inspect_folder(str(nv))["suggested_quantization"] == "nvfp4"


def test_catalog_matches_the_readme_rows():
    rows = {e.id: e for e in catalog.ENTRIES}
    assert rows["swift-heretic"].uncensored and rows["swift-heretic"].quantization == ""
    assert {rows[k].quantization for k in ("quark-awq", "nvfp4", "paroquant")} == {"quark", "nvfp4", "paroquant"}
    assert rows["dflash2"].kind == "drafter" and not rows["minicpm-v"].supported
    payload = catalog.api_payload()
    assert {e["id"] for e in payload["entries"]} == set(rows)
    assert catalog.fit(19.0, 32.0) == "fits" and catalog.fit(19.0, 18.0) == "no" and catalog.fit(None, 32.0) == "unknown"


def test_release_tag_conversion(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"tag_name": "v1.14.0"}'
    monkeypatch.setattr(sly.urllib.request, "urlopen", lambda *a, **k: Response())
    assert sly._latest_tag() == "1.14.0-rocm10.1"
    assert sly._version_key("1.14.0-rocm10.1") > sly._version_key(sly.PINNED_TAG)


def test_shared_with_radiance_runtime_groups(monkeypatch):
    assert container_runtime.CONTAINER_RUNTIMES == ("radiance", "sly-radiance")
    assert "sly-radiance" in parallel_slots.SCALING_RUNTIMES
    assert parallel_slots.slots_of({"runtime": "sly-radiance", "n_parallel": 6}) == 6
    assert set(large_model_lifecycle.container_modules()) == {"radiance", "sly-radiance"}
    assert local_llm.module_for("sly-radiance") is sly


def test_a_loaded_sly_model_is_freed_for_another_runtime(monkeypatch):
    stopped = []
    from app.models_mgmt import llama, maya_s, radiance, strata
    monkeypatch.setattr(maya_s, "installed", lambda: False)
    monkeypatch.setattr(strata, "installed", lambda: False)
    monkeypatch.setattr(radiance, "installed", lambda: False)
    monkeypatch.setattr(sly, "installed", lambda: True)
    monkeypatch.setattr(sly, "list_instances", lambda: [{"alias": "q", "loaded": True}])
    monkeypatch.setattr(sly, "stop_instance", lambda a: (stopped.append(a), (True, ""))[1])

    async def noop(*a, **k):
        return None
    monkeypatch.setattr(llama, "release_loaded_llms", noop)
    monkeypatch.setattr(large_model_lifecycle, "release_gpu_helpers", noop)
    asyncio.run(large_model_lifecycle.release_for("strata"))
    assert stopped == ["q"]
