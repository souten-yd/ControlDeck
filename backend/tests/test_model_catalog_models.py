"""目的: Models の目録をモデルごとにまとめた形（/models/catalog の models）と、カードを開いたときの詳細
（量子化ごとの大きさ・載るか・取得済み）を確かめる。

根拠: 2026-11-28 利用者（スマホの写真つき）「途中から精度が落ちてるし、なんだか目録が変。追加する機能の
ところ全てやり直して」。本番の目録では Strata が二重（Strata が取る行と HF から取る行）、開発者向けのメモ
（「Qwen 用の引数は渡さない」「RADIANCE_NVFP4_MXFP4=1」）が画面に出て、大半の行で大きさが空だった。
"""
from __future__ import annotations

import asyncio

import pytest

from app.models_mgmt import runtime_catalog as rc

RAM, VRAM = 91.8, 31.9   # 本番の PC（本番の /models/catalog の hardware）


def _models(runtime: str) -> list[dict]:
    return rc.models_for(runtime, RAM, VRAM)


def test_strata_has_no_duplicate_model_and_quantization():
    """Strata 自身の目録にある系列×量子化は 1 つだけ（HF から取る同じ行を捨てる）。第三者の無検閲版は残す。"""
    models = _models("strata")
    names = [m["name"] for m in models]
    assert len(names) == len(set(names))
    pairs = [(v["family"], v["quantization"].casefold()) for m in models for v in m["variants"] if not v["repo"].startswith(("SC117", "huihui"))]
    assert len(pairs) == len(set(pairs))
    flash = next(m for m in models if m["name"] == "Qwen3.8-Flash-Next")
    assert [v["label"] for v in flash["variants"]] == ["Q2_0", "IQ2_XS", "IQ3_XXS", "IQ3_S"]
    assert all(v["acquire"] == "runtime" for v in flash["variants"])
    assert flash["recommended"] and models[0] is flash
    uncensored = [m for m in models if m["uncensored"]]
    assert len(uncensored) == 2 and not any(m["recommended"] for m in uncensored)


def test_developer_notes_are_kept_out_of_user_text():
    """引数・環境変数・内部の経路のメモは note_expert へ。利用者向けの文（名前・説明・量子化の名前と説明）に出さない。"""
    for runtime in ("llama", "radiance", "sly-radiance", "strata", "maya"):
        for model in _models(runtime):
            user = " ".join([model["name"], model["description"]]
                            + [f"{v['label']} {v['note']} {v['reason']}" for v in model["variants"]])
            for word in ("引数", "RADIANCE_", "probe", "/api/v1", "alias", "model_path"):
                assert word not in user, (runtime, model["name"], word)
    sly = next(m for m in _models("sly-radiance") if m["name"] == "Qwen3.8-27B")
    assert "RADIANCE_NVFP4_MXFP4" in sly["note_expert"]
    qwen36 = next(m for m in _models("strata") if m["name"] == "Qwen3.6-35B-A3B")
    assert "引数" in qwen36["note_expert"] and "引数" not in qwen36["description"]


def test_same_weights_from_several_repos_are_one_card_with_per_variant_marks():
    """Sly の Qwen3.8-27B の 4 つの量子化（別々の repo）は 1 枚。無検閲は heretic の行だけなのでカードには付けない。"""
    sly = next(m for m in _models("sly-radiance") if m["name"] == "Qwen3.8-27B")
    assert len(sly["variants"]) == 4 and not sly["uncensored"]
    marks = {v["label"]: v["uncensored"] for v in sly["variants"]}
    assert marks["Swift-1.5 heretic MXFP4（標準）"] is True and marks["ParoQuant"] is False
    maya = next(m for m in _models("maya") if m["name"] == "GLM-5.3-Flash")
    assert len(maya["variants"]) == 5


def test_catalog_payload_keeps_entries_and_adds_models():
    """取得・登録の API は今までの行の id を使うので entries は残し、models の variant の id は entries の id と同じ。"""
    payload = rc.api_payload()
    for block in payload["runtimes"]:
        ids = {e["id"] for e in block["entries"]}
        assert {v["id"] for m in block["models"] for v in m["variants"]} == ids


def test_details_reads_sizes_once_and_marks_missing_quantizations(monkeypatch, tmp_path):
    """開いたときだけ HF の一覧から量子化ごとの大きさと載るかを読み、24 時間覚える。HF に無い量子化は選べない印。"""
    from app.models_mgmt import catalog_details, hf, strata_catalog

    monkeypatch.setattr(catalog_details, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(catalog_details, "_roots", lambda: [tmp_path / "models"])
    monkeypatch.setattr(strata_catalog, "machine_gib", lambda: (RAM, VRAM))
    calls: list[str] = []

    async def fake_quant_files(repo, revision="main", quantization=""):
        calls.append(quantization)
        if quantization == "Q8_0":
            raise hf.HfError("量子化 Q8_0 の GGUF が repo にありません")
        return [{"path": f"{quantization}/m-{quantization}-00001-of-00002.gguf", "size": 10 * 1024 ** 3},
                {"path": f"{quantization}/m-{quantization}-00002-of-00002.gguf", "size": 6 * 1024 ** 3}]

    monkeypatch.setattr(hf, "list_quantization_files", fake_quant_files)
    model = next(m for m in _models("llama") if m["name"] == "Qwen3.8-27B")
    out = asyncio.run(catalog_details.model_details("llama", model["id"]))
    rows = {r["id"]: r for r in out["variants"]}
    q4 = rows["unsloth-qwen38-27b-ud-q4_k_xl"]
    assert q4["size_gib"] == 16.0 and q4["fit"] == "fits" and q4["supported"]
    q8 = rows["unsloth-qwen38-27b-q8_0"]
    assert q8["supported"] is False and "見つかりません" in q8["reason"]
    asyncio.run(catalog_details.model_details("llama", model["id"]))
    assert len(calls) == 2                      # 2 回目は覚えから（通信しない）


def test_details_reports_downloaded_when_all_files_are_complete(monkeypatch, tmp_path):
    """取得先に HF と同じ大きさのファイルが全部あれば「取得済み」と登録の入口（先頭の片）を返す。途中なら返さない。"""
    from app.models_mgmt import catalog_details, hf, strata_catalog

    monkeypatch.setattr(catalog_details, "data_dir", lambda: tmp_path)
    root = tmp_path / "models"
    monkeypatch.setattr(catalog_details, "_roots", lambda: [root])
    monkeypatch.setattr(strata_catalog, "machine_gib", lambda: (RAM, VRAM))

    async def fake_quant_files(repo, revision="main", quantization=""):
        return [{"path": f"{quantization}/a-00001-of-00002.gguf", "size": 4},
                {"path": f"{quantization}/a-00002-of-00002.gguf", "size": 4}]

    monkeypatch.setattr(hf, "list_quantization_files", fake_quant_files)
    folder = root / "unsloth--Qwen3.8-27B-GGUF"
    folder.mkdir(parents=True)
    (folder / "a-00001-of-00002.gguf").write_bytes(b"GGUF")
    (folder / "a-00002-of-00002.gguf").write_bytes(b"GG")          # 足りない
    model = next(m for m in _models("llama") if m["name"] == "Qwen3.8-27B")
    rows = asyncio.run(catalog_details.model_details("llama", model["id"]))["variants"]
    assert all(r["state"] == "available" for r in rows)
    (folder / "a-00002-of-00002.gguf").write_bytes(b"GGUF")
    (tmp_path / "cache" / "model-catalog-details.json").unlink()
    rows = asyncio.run(catalog_details.model_details("llama", model["id"]))["variants"]
    assert rows[0]["state"] == "downloaded" and rows[0]["local_path"].endswith("a-00001-of-00002.gguf")


def test_details_does_not_touch_network_for_rows_strata_fetches_itself(monkeypatch, tmp_path):
    """Strata が初回の起動で取る行は、Strata の目録の値を使い HF を読まない。"""
    from app.models_mgmt import catalog_details, hf

    monkeypatch.setattr(catalog_details, "data_dir", lambda: tmp_path)

    async def boom(*args, **kwargs):
        raise AssertionError("通信してはいけない")

    monkeypatch.setattr(hf, "list_quantization_files", boom)
    monkeypatch.setattr(hf, "list_model_files", boom)
    flash = next(m for m in _models("strata") if m["name"] == "Qwen3.8-Flash-Next")
    rows = asyncio.run(catalog_details.model_details("strata", flash["id"]))["variants"]
    assert len(rows) == 4


@pytest.mark.parametrize("runtime,size,expected", [
    ("llama", 20.0, "fits"), ("llama", 60.0, "tight"), ("llama", 200.0, "no"),
    ("sly-radiance", 30.0, "tight"), ("maya", 90.0, "fits"),
])
def test_fit_by_size(runtime, size, expected):
    """大きさから載るかの目安。llama.cpp は VRAM に収まれば載る、RAM と合わせてならぎりぎり（遅い）。"""
    from app.models_mgmt import catalog_details

    assert catalog_details.fit_by_size(runtime, size, RAM, VRAM) == expected
