"""Hugging Face のモデル検索（AI アシスタントの道具 huggingface_models）。

目的: 「Qwen の最新で Strata に対応しているモデル」を、取りこぼさず・誤判定せず答えられること。
根拠: 2026-11-22 利用者の依頼「検索の幅・十分な件数を担保して」。Strata は ISTA-DASLab の GSQ-RCO・Unsloth の UD-*・Q8_0 だけを
動かし、Q4_K_S などは動かない（strata_gateway.check_runnable、2026-11-21 本番の起動失敗）。秘密の会話は通信の直前で止める
（app.security.offline）。HTTP はすべてモック。"""
import asyncio

import httpx
import pytest

from app.models_mgmt import hf_search as hs
from app.workflows import chat_tools


def f(name, size=1_000_000_000):
    return {"name": name, "size": size}


def test_strata_judgement():
    ud = hs.classify_gguf("unsloth/Qwen3.6-27B-GGUF", [f("Qwen3.6-27B-UD-IQ4_XS.gguf"), f("Qwen3.6-27B-Q4_K_S.gguf")])
    assert ud["strata"] and ud["strata_basis"][0]["quant"] == "UD-IQ4_XS" and ud["strata_family"] == "unsloth"
    assert ud["strata_registerable"] == ["UD-IQ4_XS"]
    gsq = hs.classify_gguf("ISTA-DASLab/Qwen3.6-GSQ-RCO", [f("model-IQ3_S.gguf")])
    assert gsq["strata"] and gsq["strata_family"] == "qwen" and gsq["strata_basis"][0]["kind"] == "gsq-rco"
    q8 = hs.classify_gguf("ggml-org/Qwen3.6-27B-GGUF", [f("Qwen3.6-27B-Q8_0.gguf")])
    assert q8["strata"] and q8["strata_basis"][0]["kind"] == "q8_0"
    no = hs.classify_gguf("bartowski/x-GGUF", [f("x-Q4_K_S.gguf"), f("x-IQ3_S.gguf"), f("x-UD-IQ2_M.gguf")])
    assert not no["strata"] and no["llama_cpp"]            # Q4_K_S・他作者の IQ3_S・他作者の UD は非対応
    assert not hs.classify_gguf("unsloth/x", [f("x-UD-Q8_K_XL.gguf"), f("mmproj-F16.gguf")])["strata"]
    none = hs.classify_gguf("Qwen/Qwen3.6-27B", [])
    assert not none["gguf"] and not none["strata"] and not none["llama_cpp"]


def test_variants_and_params():
    v = hs.query_variants("Qwen3.8")
    assert {"Qwen3.8", "Qwen-3.8", "Qwen3_8", "Qwen38"} <= set(v) and all(" " not in x for x in v)
    assert hs.estimate_params("Qwen3.6-35B-A3B-GGUF") == 35
    assert hs.estimate_params("model") is None


def row(rid, base="", files=(), updated="2026-11-01T00:00:00.000Z", created=None, pipeline="text-generation",
        relation="quantized", gguf=False, downloads=5, **extra):
    tags = ["gguf"] if gguf or any(n.endswith(".gguf") for n in files) else []
    if base:
        tags.append(f"base_model:{base}")
        tags.append(f"base_model:{relation}:{base}")
    return {"id": rid, "lastModified": updated, "createdAt": created or updated, "downloads": downloads, "likes": 1,
            "tags": tags, "pipeline_tag": pipeline, "cardData": {"base_model": base, "license": "apache-2.0"} if base else {},
            "siblings": [{"rfilename": n} for n in files], **extra}


class Hub:
    """絞り込み（author・search・filter）に答える偽の Hugging Face。通信を覚える。"""
    def __init__(self, rows, trees=None):
        self.rows, self.trees, self.calls = rows, trees or {}, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if "/tree/" in path:
            repo = path.split("/api/models/")[1].split("/tree/")[0]
            return httpx.Response(200, json=[{"type": "file", "path": n, "size": s} for n, s in self.trees.get(repo, [])])
        params = request.url.params.multi_items()
        out = list(self.rows)
        for k, v in params:
            if k == "author":
                out = [r for r in out if r["id"].split("/")[0] == v]
            elif k == "search":
                out = [r for r in out if hs._normalize(v) in hs._normalize(r["id"])]
            elif k == "filter" and v == "gguf":
                out = [r for r in out if "gguf" in r["tags"]]
            elif k == "filter" and v.startswith("base_model:"):
                out = [r for r in out if (r["cardData"] or {}).get("base_model") == v[11:]]
        out.sort(key=lambda r: r["lastModified"], reverse=True)
        return httpx.Response(200, json=out)


@pytest.fixture
def hub(monkeypatch):
    hs.clear_cache()
    monkeypatch.setattr(hs, "_registered_index", lambda: [])

    def install(hub_):
        original = httpx.AsyncClient
        monkeypatch.setattr(hs.httpx, "AsyncClient", lambda *a, **k: original(*a, transport=httpx.MockTransport(hub_.handler), **k))
        return hub_
    return install


def run(**kw):
    return asyncio.run(hs.search(**kw))


def world():
    rows = [
        row("Qwen/Qwen3.8-27B", pipeline="image-text-to-text", updated="2026-11-07T00:00:00.000Z", created="2026-08-05T00:00:00.000Z"),
        row("Qwen/Qwen3.6-27B", pipeline="image-text-to-text", updated="2026-11-20T00:00:00.000Z", created="2026-04-21T00:00:00.000Z"),
        row("Qwen/Qwen3-TTS-1.7B", pipeline="text-to-speech", created="2026-11-10T00:00:00.000Z"),
        row("unsloth/Qwen3.8-27B-GGUF", base="Qwen/Qwen3.8-27B", files=["a-Q8_0.gguf", "a-UD-IQ4_XS.gguf", "a-Q4_K_S.gguf"]),
        row("ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF", base="Qwen/Qwen3.8-27B", files=["g-IQ3_S.gguf"]),
        row("zed/Qwen3.8-27B-Q8-GGUF", base="Qwen/Qwen3.8-27B", files=["z-Q8_0.gguf"]),
        row("bartowski/Qwen3.8-27B-GGUF", base="Qwen/Qwen3.8-27B", files=["b-Q4_K_S.gguf", "b-IQ3_S.gguf"]),
        row("ggml-org/Qwen3.6-27B-GGUF", base="Qwen/Qwen3.6-27B", files=["c-Q8_0.gguf"]),
        row("evil/Qwen3.8-27B-Uncensored", base="Qwen/Qwen3.8-27B", relation="finetune", updated="2026-11-22T00:00:00.000Z"),
        row("mrad/Qwen3.8-27B-Uncensored-GGUF", base="evil/Qwen3.8-27B-Uncensored", files=["u-Q8_0.gguf"]),
    ]
    trees = {"unsloth/Qwen3.8-27B-GGUF": [("a-Q8_0.gguf", 29), ("a-UD-IQ4_XS.gguf", 15), ("a-Q4_K_S.gguf", 16)],
             "ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF": [("g-IQ3_S.gguf", 24)], "zed/Qwen3.8-27B-Q8-GGUF": [("z-Q8_0.gguf", 29)],
             "bartowski/Qwen3.8-27B-GGUF": [("b-Q4_K_S.gguf", 16), ("b-IQ3_S.gguf", 12)],
             "ggml-org/Qwen3.6-27B-GGUF": [("c-Q8_0.gguf", 29)], "mrad/Qwen3.8-27B-Uncensored-GGUF": [("u-Q8_0.gguf", 29)]}
    return rows, trees


def test_official_first_with_ordered_placements_and_derivatives_below(hub):
    hub(Hub(*world()))
    r = run(query="Qwen")
    ids = [i["id"] for i in r["items"]]
    assert r["official"] == "Qwen"
    assert ids[:2] == ["Qwen/Qwen3.8-27B", "Qwen/Qwen3.6-27B"] or ids[0] == "Qwen/Qwen3.8-27B"    # 公式が先、作成日の新しい順
    assert ids.index("Qwen/Qwen3.8-27B") < ids.index("evil/Qwen3.8-27B-Uncensored")              # 派生はベースの下
    assert ids.index("evil/Qwen3.8-27B-Uncensored") < ids.index("Qwen/Qwen3.6-27B")
    head = r["items"][0]
    # 置き場: UD・GSQ が先、Q8_0 は後。Q4_K_S だけの bartowski は入らない
    assert [p["id"] for p in head["placements"]] == ["unsloth/Qwen3.8-27B-GGUF", "ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF", "zed/Qwen3.8-27B-Q8-GGUF"]
    assert head["placements"][0]["basis"][0]["quant"] == "UD-IQ4_XS" and head["placements"][0]["basis"][0]["size"] == 15
    assert not head["derivative"]
    deriv = next(i for i in r["items"] if i["id"] == "evil/Qwen3.8-27B-Uncensored")
    assert deriv["derivative"] and [p["id"] for p in deriv["placements"]] == ["mrad/Qwen3.8-27B-Uncensored-GGUF"]
    text = hs.render(r)
    assert "派生" in text and "調べた件数" in text and "見つけた公式モデル" in text and "起動時" in text


def test_derivatives_can_be_excluded_and_tts_hidden_by_default(hub):
    hub(Hub(*world()))
    r = run(query="Qwen", include_derivatives=False)
    ids = [i["id"] for i in r["items"]]
    assert "evil/Qwen3.8-27B-Uncensored" not in ids and r["counts"]["derivatives_found"] >= 1
    assert "Qwen/Qwen3-TTS-1.7B" not in ids                       # TTS は既定で出ない
    hs.clear_cache()
    assert "Qwen/Qwen3-TTS-1.7B" in [i["id"] for i in run(query="Qwen", pipeline="any")["items"]]


def test_runtime_strata_keeps_only_rows_with_strata_placement(hub):
    hub(Hub(*world()))
    r = run(query="Qwen", runtime="strata", include_derivatives=False)
    assert [i["id"] for i in r["items"]] == ["Qwen/Qwen3.8-27B", "Qwen/Qwen3.6-27B"]
    assert r["counts"]["with_strata"] == 2


def test_pagination_follows_cursor(hub):
    def many(req):
        page = int(req.url.params.get("cursor", "1"))
        if req.url.params.get("author") or "base_model" in str(req.url) or req.url.params.get("search") != "Qwen":
            return httpx.Response(200, json=[])
        rows = [row(f"o/Qwen-{page}-{i}") for i in range(100)]
        headers = {"link": f'<https://huggingface.co/api/models?search=Qwen&cursor={page + 1}>; rel="next"'} if page < 3 else {}
        return httpx.Response(200, json=rows, headers=headers)
    h = Hub([])
    h.handler = many
    hub(h)
    r = run(query="Qwen", limit=200)
    assert r["total_found"] == 300          # 100 件ずつのページを 3 つたどる（件数は取りこぼさない）


def test_cache_and_registered_mark(hub, monkeypatch):
    h = hub(Hub(*world()))
    monkeypatch.setattr(hs, "_registered_index", lambda: [("my-qwen", "/models/unsloth--Qwen3.8-27B-GGUF/a-UD-IQ4_XS.gguf")])
    first = run(query="Qwen")
    n = len(h.calls)
    second = run(query="Qwen")
    assert len(h.calls) == n and second["cached"]                 # 10 分は再通信しない
    assert first["items"][0]["placements"][0]["registered_as"] == ["my-qwen"] and "登録済み" in hs.render(second)


def test_confidential_scope_never_connects(hub):
    from app.security import offline
    h = hub(Hub([]))
    with offline.confidential_scope():
        with pytest.raises(offline.ExternalResearchBlocked):
            run(query="Qwen")
        text, record = asyncio.run(chat_tools.run_hf_models({"name": "huggingface_models", "arguments": '{"query":"Qwen"}'}, max_chars=5000))
    assert not h.calls and record["error"] == "confidential" and "秘密" in text


def test_tool_registered_and_answer_shape(hub):
    fn = chat_tools.TOOL_HF_MODELS["function"]
    assert fn["name"] == chat_tools.HF_MODELS == "huggingface_models" and "Hugging Face" in fn["description"]
    assert fn["parameters"]["required"] == ["query"] and "include_derivatives" in fn["parameters"]["properties"]
    hub(Hub(*world()))
    text, record = asyncio.run(chat_tools.run_hf_models(
        {"name": "huggingface_models", "arguments": '{"query":"Qwen","runtime":"strata"}'}, max_chars=20000))
    assert record["count"] >= 2 and "| モデル | 更新日 | 大きさ | Strata で動かせる置き場と根拠のファイル | 特徴 |" in text
    assert "UD-IQ4_XS" in text and "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF" in text
    assert "query が空" in asyncio.run(chat_tools.run_hf_models({"name": "x", "arguments": "{}"}, max_chars=100))[0]
