"""Strata の目録が 1 か所に集約され、画面・API・登録のすべてが同じ値を見ることを守る。

確かめること: 全系列に量子化がある・id が重ならない・未知の系列を Pydantic が弾く・
NVIDIA 限定の行を NVIDIA の無い PC で出さない・「載るか」の計算・API の形（同期 endpoint）。
根拠: 2026-11-26 の指示書 M1。以前は目録が gateway・router・model_names・frontend に
二重・三重にあり、0.2.22 の全系列を登録画面に出せなかった。AGENTS.md「テストは目的と根拠を
書いてから足す」の決まりで、足す前に `./deck.sh test-catalog` で同じ観点を探した。
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.models_mgmt import strata_catalog


def test_catalog_is_complete_and_ids_do_not_collide():
    """目録の全行に量子化があり、系列 id・量子化 id が重ならない。"""
    rows = strata_catalog.families()
    assert rows
    family_ids = [row.id for row in rows]
    assert len(family_ids) == len(set(family_ids))
    for row in rows:
        assert row.quantizations, f"{row.id} に量子化が無い"
        quant_ids = [q.id for q in row.quantizations]
        assert len(quant_ids) == len(set(quant_ids)), f"{row.id} の量子化 id が重複"
        assert all(q.id == q.id.lower() and q.label for q in row.quantizations)
    # 既存の登録を壊さない id と、0.2.22 で増えた系列が両方ある。
    for family, model in (("qwen", "iq2_xs"), ("unsloth", "ud-iq4_xs"),
                          ("deepseek41", "ista-das-coder"), ("inkling-2-mini", "sq-20"),
                          ("gyro", "gyro-s"), ("qwen36", "ud-q4_k_xl"),
                          ("hy4-flash", "sq-64"), ("coder", "iq1_m"), ("swift", "iq3_s")):
        assert strata_catalog.is_valid(family, model)
    assert not strata_catalog.is_valid("orca", "iq3_xxs")   # 手作業の行は案内だけ
    assert not strata_catalog.is_valid("qwen", "ud-iq4_xs")  # 別の系列の量子化は混ぜない
    assert not strata_catalog.is_valid("evil;rm", "q2_0")


def test_catalog_engine_and_thinking_flags():
    """Qwen 用引数を渡さない系列と、0.2.22 で思考を持つ系列を間違えない。"""
    assert strata_catalog.is_other_engine("deepseek41")
    assert strata_catalog.is_other_engine("inkling-2-mini")
    assert strata_catalog.is_other_engine("qwen36")
    assert not strata_catalog.is_other_engine("qwen")
    assert strata_catalog.has_thinking("inkling-2-mini")   # 0.2.19 の前提から更新
    assert strata_catalog.short_name("unsloth") == "Qwen3.8-Flash-Next"


def test_family_validator_uses_catalog_not_a_fixed_literal(monkeypatch):
    """API の系列は目録の id だけを受け付け、固定 Literal に無い新しい系列も通る。"""
    from app.models_mgmt.router import StrataModelBody

    body = StrataModelBody(alias="new", family="gyro", model="gyro-s", port=8120)
    assert body.family == "gyro"
    with pytest.raises(ValidationError):
        StrataModelBody(alias="bad", family="evil", model="q2_0")
    # 目録に足した系列だけが通るようになった（router の Literal を書き換えなくてよい）。
    assert "gyro" not in getattr(StrataModelBody.model_fields["family"].annotation, "__args__", ())


def test_nvidia_only_family_is_hidden_without_nvidia(monkeypatch):
    """NVIDIA の無い PC では nvfp4 を画面に出さない。目録自体からは消さない。"""
    monkeypatch.setattr(strata_catalog, "has_nvidia_gpu", lambda: False)
    assert not strata_catalog.visible("nvfp4")
    assert strata_catalog.visible("qwen")
    monkeypatch.setattr(strata_catalog, "has_nvidia_gpu", lambda: True)
    assert strata_catalog.visible("nvfp4")


def test_fit_compares_memory_estimate_with_ram_plus_vram():
    """「載る／ぎりぎり／載らない／不明」が RAM+VRAM と目安の比べで決まる。"""
    # qwen/q2_0 のメモリ目安は 37.6GB。
    assert strata_catalog.fit("qwen", "q2_0", 91.0, 32.0) == strata_catalog.FITS
    assert strata_catalog.fit("qwen", "q2_0", 37.6, 0.0) == strata_catalog.TIGHT
    assert strata_catalog.fit("qwen", "q2_0", 16.0, 0.0) == strata_catalog.NO_FIT
    # 目安が未確認の量子化は「載らない」と決めつけない。
    assert strata_catalog.fit("unsloth", "ud-iq1_m", 1.0, 0.0) == strata_catalog.UNKNOWN
    # この PC の RAM を読めないとき（0・None）も「載らない」と決めつけない。
    assert strata_catalog.fit("qwen", "q2_0", 0.0, 32.0) == strata_catalog.UNKNOWN


def test_api_payload_shape_and_sync_endpoint(monkeypatch):
    """目録 API が画面に必要な形を返し、同期 endpoint で event loop を塞がない。"""
    from app.models_mgmt import router

    monkeypatch.setattr(strata_catalog, "machine_gib", lambda: (91.0, 32.0))
    monkeypatch.setattr(strata_catalog, "has_nvidia_gpu", lambda: False)
    payload = strata_catalog.api_payload()
    assert payload["hardware"] == {"ram_total_gib": 91.0, "vram_total_gib": 32.0}
    rows = {row["id"]: row for row in payload["families"]}
    assert len(rows) == len(strata_catalog.families())
    qwen = rows["qwen"]
    assert qwen["engine"] == "qwen" and qwen["thinking"] is True and qwen["visible"] is True
    quant = next(item for item in qwen["quantizations"] if item["id"] == "q2_0")
    assert quant["download_gib"] == 66.0 and quant["memory_gib"] == 37.6
    assert quant["fit"] == strata_catalog.FITS
    assert rows["nvfp4"]["visible"] is False
    orca = rows["orca"]
    assert orca["manual"] is True and orca["uncensored"] is True
    assert all(item["manual"] for item in orca["quantizations"])
    assert not inspect.iscoroutinefunction(router.strata_catalog_endpoint)
    assert router.strata_catalog_endpoint(user=object())["families"]
