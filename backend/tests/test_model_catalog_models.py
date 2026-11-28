"""目的: Models の目録（系列 → 検閲あり／無検閲 → 量子化。容量・必要メモリ・載るか）を、ランタイム本体の目録の
実データで確かめる。

根拠: 2026-11-28 利用者「目録はまず大きな種類（Qwen・Hy4・Inkling など）を選ばせ、検閲・無検閲、量子化を選べる
ように。載るかどうかと容量を調査して、Strata・Maya・Radiance それぞれの目録を作って。Maya は DeepSeek・GLM・
（Tencent の）Hy4 など。llama.cpp は目録をやめて従来のファイル選択に戻す」。調査は docs/research-model-catalog-20261128.md。
試験データ: tests/fixtures/strata-catalog-0.2.23.json（本番の Strata 0.2.23 の `Catalog().rows()`）・
tests/fixtures/maya-catalog-1.1.84.json（本番の Maya 1.1.84 の probe）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.models_mgmt import runtime_catalog as rc

RAM, VRAM = 91.8, 31.9   # 本番の PC
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def live(monkeypatch):
    """Strata・Maya の本体の目録を、本番から読んだ実データにする。"""
    from app.models_mgmt import maya_s, strata_catalog, strata_live

    monkeypatch.setattr(strata_live, "rows", lambda **kw: json.loads((FIX / "strata-catalog-0.2.23.json").read_text()))
    monkeypatch.setattr(strata_catalog, "has_nvidia_gpu", lambda: False)
    monkeypatch.setattr(maya_s, "installed", lambda: True)
    monkeypatch.setattr(maya_s, "catalog", lambda folder="": json.loads((FIX / "maya-catalog-1.1.84.json").read_text()))


def _models(runtime: str) -> list[dict]:
    return rc.models_for(runtime, RAM, VRAM)


def _by_name(runtime: str) -> dict[str, dict]:
    return {m["name"]: m for m in _models(runtime)}


def test_llama_has_no_catalog():
    """llama.cpp は目録をやめた（手元のファイルを選んで登録する）。"""
    assert _models("llama") == []


def test_strata_families_come_from_strata_itself(live):
    """Strata の系列は本体の目録から: Hy4-Flash・Inkling・Ornith・Gyro を含む。NVIDIA だけの NVFP4 と提供終了は出さない。"""
    models = _by_name("strata")
    for name in ("Qwen3.8-Flash-Next", "Swift 1.5", "Tencent Hy4-Flash", "Inkling-2-Mini", "Ornith-1.5-35B-A3B",
                 "Qwen3.8-Flash-Next（Gyro 版）", "Qwen3.6-35B-A3B"):
        assert name in models, name
    assert not any("NVFP4" in m["name"] for m in models.values())
    inkling = [v["label"] for v in models["Inkling-2-Mini"]["variants"] if not v["uncensored"]]
    assert "UD-Q8_K_XL" not in inkling and "SQ-20" in inkling           # 提供終了は出さない
    flash = models["Qwen3.8-Flash-Next"]
    assert flash["recommended"] or any(m["recommended"] for m in models.values())
    q2 = next(v for v in flash["variants"] if v["label"] == "Q2_0" and not v["uncensored"])
    assert (q2["size_gib"], q2["memory_gib"], q2["fit"], q2["acquire"]) == (66.4, 35.0, "fits", "runtime")
    q8 = next(v for v in models["Qwen3.8-Flash-Next（Unsloth 版）"]["variants"] if v["label"] == "Q8_0")
    assert q8["fit"] == "slow"                                        # 本体の判定 slower → 遅い


def test_strata_uncensored_variants_sit_in_the_same_family(live):
    """無検閲版は同じ系列のカードの中（検閲あり／無検閲を切り替える）。必要メモリと判定は公式の同じ量子化から。"""
    models = _by_name("strata")
    flash = models["Qwen3.8-Flash-Next"]
    assert flash["has_uncensored"] and flash["has_censored"] and not flash["uncensored"]
    unc = [v for v in flash["variants"] if v["uncensored"]]
    assert [v["label"] for v in unc] == ["Q2_0", "IQ2_XS", "IQ3_XXS", "IQ3_S"]
    assert all(v["acquire"] == "hf" and v["maker"] == "SC117" for v in unc)
    official = {v["label"]: v for v in flash["variants"] if not v["uncensored"]}
    assert all(v["memory_gib"] == official[v["label"]]["memory_gib"] for v in unc)
    inkling = models["Inkling-2-Mini"]
    heretic = next(v for v in inkling["variants"] if v["uncensored"])
    assert heretic["files"] == ["Inkling-2-Mini-Heretic-UD-Q4_K_XL.gguf", "dspark-Inkling-2-Mini-Q8_0.gguf"]
    assert not models["Tencent Hy4-Flash"]["has_uncensored"]          # Hy4 の無検閲版は Strata で動かない（調査）


def test_maya_families_come_from_maya_itself(live):
    """Maya は本体の probe から: GLM・DeepSeek・MiniMax・Hy4。Derisked は GLM の無検閲。未公開は選べない。登録済みを出す。"""
    models = _by_name("maya")
    assert set(models) == {"GLM-5.3-Flash", "DeepSeek-V4.1-Flash", "MiniMax-M3.1-Flash", "Hy4-Flash"}
    glm = models["GLM-5.3-Flash"]
    assert glm["has_uncensored"] and [v["label"] for v in glm["variants"] if v["uncensored"]] == ["Maya-M-Derisked"]
    ds = {v["label"]: v for v in models["DeepSeek-V4.1-Flash"]["variants"]}
    assert ds["Maya-M"]["supported"] is False                         # 未公開
    assert ds["Maya-XS"]["state"] == "registered" and ds["Maya-XS"]["registered"] == "maya-deepseek-xs"
    assert ds["Maya-S"]["fit"] == "slow"                              # 必要 186 GB > RAM+VRAM: SSD から読むので遅い
    assert all(v["acquire"] in ("maya", "none") for m in models.values() for v in m["variants"])


def test_radiance_lists_text_models_with_sizes():
    """Radiance は作者の文章の LLM（画像・動画・音楽は載せない）。大きさは HF の一覧の値・載るかは VRAM と比べる。"""
    models = _by_name("radiance")
    assert {"Qwen3.8-27B", "Qwen3.8-Flash-Next", "Inkling-2-Mini", "Gemma4-26B-A4B", "MiniCPM5-2B"} <= set(models)
    assert not any(word in name.lower() for name in models for word in ("image", "ltx", "flux", "krea", "music", "tts"))
    q27 = models["Qwen3.8-27B"]
    assert q27["has_uncensored"] and len([v for v in q27["variants"] if not v["uncensored"]]) == 18
    assert all(v["size_gib"] for m in models.values() for v in m["variants"])
    small = next(v for v in q27["variants"] if v["label"].startswith("IQ4R（"))
    assert small["fit"] == "fits"
    big = models["Qwen3.8-Flash-Next"]["variants"][0]
    assert big["size_gib"] > 120 and big["fit"] == "no"


def test_user_text_has_no_developer_notes(live):
    """利用者向けの文（系列名・説明・量子化の名前）に、引数・環境変数・内部の経路を出さない。"""
    for runtime in ("radiance", "sly-radiance", "strata", "maya"):
        for model in _models(runtime):
            user = " ".join([model["name"], model["description"]] + [f"{v['label']} {v['reason']}" for v in model["variants"]])
            for word in ("引数", "RADIANCE_", "probe", "/api/v1", "alias", "model_path"):
                assert word not in user, (runtime, model["name"], word)


def test_catalog_payload_keeps_entries_and_adds_models(live):
    """取得・登録の API は今までの行の id を使うので entries は残し、models の variant の id は entries と同じ。"""
    payload = rc.api_payload()
    for block in payload["runtimes"]:
        assert {v["id"] for m in block["models"] for v in m["variants"]} == {e["id"] for e in block["entries"]}
        assert "download_root" in block


def test_strata_falls_back_to_the_hand_table_when_strata_cannot_be_read(monkeypatch):
    """Strata 本体の目録を読めないとき（未導入・時間切れ）は手の表（strata_catalog）で出す。"""
    from app.models_mgmt import strata_live

    monkeypatch.setattr(strata_live, "rows", lambda **kw: None)
    names = {m["name"] for m in _models("strata")}
    assert {"Qwen3.8-Flash-Next", "Ornith-1.5-35B-A3B", "Inkling-2-Mini"} <= names


@pytest.mark.parametrize("size,expected", [(20.0, "fits"), (60.0, "slow"), (200.0, "no")])
def test_fit_by_size(size, expected):
    """GPU に置くランタイムの目安: VRAM に収まれば載る、RAM と合わせて収まれば遅い、それ以外は載らない。"""
    assert rc.fit_by_size(size, RAM, VRAM) == expected


def test_details_reads_sizes_once_and_marks_missing(monkeypatch, tmp_path):
    """Radiance の行は開いたときに HF の一覧で取得済みかを見る（24 時間覚える）。HF に無いファイルは選べない印。"""
    from app.models_mgmt import catalog_details, hf, strata_catalog

    monkeypatch.setattr(catalog_details, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(catalog_details, "_roots", lambda: [tmp_path / "models"])
    monkeypatch.setattr(strata_catalog, "machine_gib", lambda: (RAM, VRAM))
    calls: list[str] = []

    async def fake_list(repo, revision="main"):
        calls.append(repo)
        if repo.endswith("minicpm5-2b-bf16"):
            return []
        return [{"path": repo.split("/")[1] + ".rad", "size": 3_300_000_000}]

    monkeypatch.setattr(hf, "list_model_files", fake_list)
    model = next(m for m in _models("radiance") if m["name"] == "MiniCPM5-2B")
    rows = {r["id"]: r for r in asyncio.run(catalog_details.model_details("radiance", model["id"]))["variants"]}
    assert rows["radiance-minicpm5-2b-fp8"]["size_gib"] == 3.3 and rows["radiance-minicpm5-2b-fp8"]["supported"]
    assert rows["radiance-minicpm5-2b-bf16"]["supported"] is False
    first = len(calls)
    asyncio.run(catalog_details.model_details("radiance", model["id"]))
    assert len(calls) == first                    # 2 回目は覚えから（通信しない）


def test_details_reports_downloaded(monkeypatch, tmp_path):
    """取得先に HF と同じ大きさのファイルがそろっていれば取得済みと登録の入口を返す。足りなければ返さない。"""
    from app.models_mgmt import catalog_details, hf, strata_catalog

    monkeypatch.setattr(catalog_details, "data_dir", lambda: tmp_path)
    root = tmp_path / "models"
    monkeypatch.setattr(catalog_details, "_roots", lambda: [root])
    monkeypatch.setattr(strata_catalog, "machine_gib", lambda: (RAM, VRAM))

    async def fake_list(repo, revision="main"):
        return [{"path": "minicpm5-2b-fp8.rad", "size": 4}]

    monkeypatch.setattr(hf, "list_model_files", fake_list)
    folder = root / "StillDeadcode--minicpm5-2b-fp8"
    folder.mkdir(parents=True)
    (folder / "minicpm5-2b-fp8.rad").write_bytes(b"RA")
    model = next(m for m in _models("radiance") if m["name"] == "MiniCPM5-2B")
    rows = {r["id"]: r for r in asyncio.run(catalog_details.model_details("radiance", model["id"]))["variants"]}
    assert rows["radiance-minicpm5-2b-fp8"]["state"] == "available"
    (folder / "minicpm5-2b-fp8.rad").write_bytes(b"RADX")
    (tmp_path / "cache" / "model-catalog-details.json").unlink()
    rows = {r["id"]: r for r in asyncio.run(catalog_details.model_details("radiance", model["id"]))["variants"]}
    assert rows["radiance-minicpm5-2b-fp8"]["state"] == "downloaded"
    assert rows["radiance-minicpm5-2b-fp8"]["local_path"].endswith("minicpm5-2b-fp8.rad")


def test_details_do_not_touch_network_for_rows_runtimes_fetch_themselves(monkeypatch, tmp_path, live):
    """Strata が初回の起動で取る行・Maya の行は、本体の値を使い HF を読まない。"""
    from app.models_mgmt import catalog_details, hf

    monkeypatch.setattr(catalog_details, "data_dir", lambda: tmp_path)

    async def boom(*args, **kwargs):
        raise AssertionError("通信してはいけない")

    monkeypatch.setattr(hf, "list_quantization_files", boom)
    monkeypatch.setattr(hf, "list_model_files", boom)
    for runtime, name in (("strata", "Tencent Hy4-Flash"), ("maya", "GLM-5.3-Flash")):
        model = next(m for m in _models(runtime) if m["name"] == name)
        assert asyncio.run(catalog_details.model_details(runtime, model["id"]))["variants"]


def test_acquire_uses_listed_files_for_the_inkling_heretic(live, admin_client, monkeypatch):
    """目録にファイル名を書いた行（Inkling の無検閲版は本体と DSpark の 2 つ）は、そのファイルを取る（DSpark を漏らさない）。"""
    from app.models_mgmt import router as models_router
    from tests.conftest import CSRF_HEADERS

    captured = []

    class _Job:
        id = "job-x"

    monkeypatch.setattr(models_router.jobs, "create", lambda *a, **kw: captured.append(kw) or _Job())
    monkeypatch.setattr(models_router.jobs, "resume_records", lambda kind: [])
    response = admin_client.post("/api/v1/models/acquire-jobs", headers=CSRF_HEADERS, json={
        "runtime": "strata", "entry_id": "joaozaokk-inkling2-mini-heretic-ud-q4_k_xl"})
    assert response.status_code == 201, response.text
    resume = captured[0]["resume"]
    assert resume["files"] == ["Inkling-2-Mini-Heretic-UD-Q4_K_XL.gguf", "dspark-Inkling-2-Mini-Q8_0.gguf"]
    assert resume["quantization"] == ""
