"""ローカル LLM の並列のスロット数から調査の同時実行数を決める部分のテスト（LLM・GPU・通信はすべてモック）。

目的: ランタイムの種類・設定の上書き・読めないときの安全側の値を固定し、調査で LLM を呼ぶ処理がスロット由来の本数を超えず、LLM を使わないページ読み取りはスロットに縛られないことを守る。
根拠: 2026-11-23 利用者「並列数が 5 並列なら、1 並列でメインの会話を継続して、あとの 4 並列で調査の要約など。並列数は事前に設定した
ものを読み取って。並列 1 の場合は従来通り」。2026-11-15 の実測: Radiance・llama.cpp は並列で伸び、Strata は伸びず、Maya-S は並列で起動しない。
"""
from __future__ import annotations

import asyncio

import pytest

from app.models_mgmt import local_llm, parallel_slots as ps
from app.workflows import research_intent as ri
from app.workflows import research_keys as rk


def item(runtime, n):
    return {"alias": "m", "runtime": runtime, "n_parallel": n, "port": 9001}


@pytest.mark.parametrize("runtime,n,expected", [
    ("radiance", 5, 4), ("llama.cpp", 3, 2), ("radiance", 2, 1),   # 会話に 1 つ残す
    ("radiance", 1, 1), ("llama.cpp", 1, 1),                        # 並列 1 は従来どおり 1 本ずつ
    ("strata", 6, 1), ("maya-s", 4, 1),                             # 並列で伸びない・起動しない
    ("radiance", 32, ps.MAX_HELPERS),                               # 上限
    ("radiance", "x", 1), ("radiance", None, 1),                    # 読めない値
])
def test_helper_count_by_runtime(monkeypatch, runtime, n, expected):
    monkeypatch.setattr(local_llm, "instance_for_port", lambda port: item(runtime, n))
    assert ps.background_parallel("http://127.0.0.1:9001/v1", "m") == expected


def test_override_is_capped_by_slots_and_zero_means_auto(monkeypatch):
    monkeypatch.setattr(local_llm, "instance_for_port", lambda port: item("radiance", 5))
    url = "http://127.0.0.1:9001/v1"
    assert ps.background_parallel(url, "m", 0) == 4
    assert ps.background_parallel(url, "m", 2) == 2
    assert ps.background_parallel(url, "m", 8) == 5          # スロット数を超えない
    monkeypatch.setattr(local_llm, "instance_for_port", lambda port: item("strata", 5))
    assert ps.background_parallel(url, "m", 4) == 1          # 伸びないランタイムは上書きでも 1


def test_unreadable_config_is_one_for_local_and_default_for_external(monkeypatch):
    def boom(port):
        raise OSError("設定が壊れている")

    monkeypatch.setattr(local_llm, "instance_for_port", boom)
    monkeypatch.setattr(local_llm, "find", lambda alias: None)
    assert ps.background_parallel("http://127.0.0.1:9001/v1", "m") == 1
    assert ps.background_parallel("https://api.example.test/v1", "m") == ps.EXTERNAL_DEFAULT
    assert ps.background_parallel("", "") == 1


def test_model_alias_is_used_when_port_is_not_a_model(monkeypatch):
    monkeypatch.setattr(local_llm, "instance_for_port", lambda port: None)
    monkeypatch.setattr(local_llm, "find", lambda alias: item("radiance", 4) if alias == "m" else None)
    assert ps.background_parallel("http://127.0.0.1:8000/v1", "m") == 3


def test_limit_setting_accepts_zero_to_eight(tmp_path, monkeypatch):
    monkeypatch.setattr(rk, "_path", lambda: tmp_path / "keys.json")
    assert rk.limits()["llm_parallel"] == 0
    rk.set_limits({"llm_parallel": "3"})
    assert rk.limits()["llm_parallel"] == 3
    with pytest.raises(rk.ResearchKeyError):
        rk.set_limits({"llm_parallel": "9"})


def _run_investigation(slots, aspects=4):
    """スロット数 slots のモデルで investigate を回し、読み取りと LLM 処理（digest）の最大同時数を返す（通信・LLM はモック）。"""
    import datetime as dt

    from app.models_mgmt import local_llm

    counts = {"read": 0, "read_peak": 0, "llm": 0, "llm_peak": 0}

    async def searx(query, limit, time_range, pageno):
        return [{"title": f"{query}{n}", "url": f"https://x.test/{query}/{n}", "snippet": "s"} for n in range(8)] if pageno == 1 else []

    async def read(url, focus="", max_chars=0, **kw):
        counts["read"] += 1
        counts["read_peak"] = max(counts["read_peak"], counts["read"])
        await asyncio.sleep(0.01)
        counts["read"] -= 1
        return {"ok": True, "url": url, "title": "t", "date": "", "text": "本文 " * 100, "links": [], "total_chars": 400, "truncated": False}

    async def ranker(q, texts, keep):
        return [0.9] * len(texts)

    async def digest(i, judged):
        counts["llm"] += 1
        counts["llm_peak"] = max(counts["llm_peak"], counts["llm"])
        await asyncio.sleep(0.02)
        counts["llm"] -= 1
        return judged

    intent = ri.Intent(goal="g", subjects=["x"], aspects=[ri.Aspect(f"観点{n}", [f"q{n}"]) for n in range(aspects)])
    import unittest.mock as mock

    with mock.patch.object(local_llm, "instance_for_port", lambda port: item("radiance", slots)):
        asyncio.run(ri.investigate(intent, searx=searx, base_url="http://127.0.0.1:9001/v1", model="m", rounds=1,
                                   now=dt.datetime(2026, 11, 23, 12, tzinfo=dt.timezone(dt.timedelta(hours=9))), read=read, ranker=ranker,
                                   digest=digest, read_base=5))
    return counts


def test_page_reads_ignore_slots_and_llm_work_is_capped_by_slots():
    one = _run_investigation(1)
    assert one["read_peak"] == ri.READ_PARALLEL                  # ページの読み取りはスロット 1 でも 4 本同時（LLM を使わない）
    assert one["llm_peak"] == 1                                  # 並列 1 は LLM 処理を順に
    three = _run_investigation(3)
    assert three["read_peak"] == ri.READ_PARALLEL              # 観点が並んでも読む総数は 4 本のまま
    assert three["llm_peak"] == 2                                # 会話に 1 つ残して n-1 = 2 本
    five = _run_investigation(5)
    assert 2 <= five["llm_peak"] <= 4                           # 読み取りの終わる順で重なり方は揺れるが、n-1 = 4 は超えない


def test_reads_per_aspect_never_exceed_read_parallel():
    live = peak = 0

    async def read(url, focus="", max_chars=0, **kw):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return {"ok": True, "url": url, "title": "t", "date": "", "text": "本文 " * 100}

    async def ranker(q, texts, keep):
        return [0.9] * len(texts)

    results = [{"title": f"r{n}", "url": f"https://x.test/{n}", "snippet": "s"} for n in range(12)]
    asyncio.run(ri.read_and_judge(results, window=None, today=__import__("datetime").date(2026, 11, 23), focus="f", limit=12,
                                  read=read, ranker=ranker))
    assert peak == ri.READ_PARALLEL
