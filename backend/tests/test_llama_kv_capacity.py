"""共有KV(kv_unified)の空き容量観測と受け入れ制御。

実機で確認した llama.cpp の挙動が前提:
- kv_unified では ctx_size が全sequence共有のプール総量になる（各slotの n_ctx は上限値）。
- プールが尽きると待機せず HTTP 500 "Context size has been exceeded" を返し、
  実行中の他リクエストごと失敗する。
- 実測: ctx の 74% は通り、97% は失敗した。
"""
from __future__ import annotations

import asyncio

import pytest

from app.models_mgmt import llama


def _slots(entries):
    """/slots のレスポンス形。entries は (n_prompt_tokens, n_decoded, busy)。"""
    return [
        {"id": i, "n_ctx": 8192, "is_processing": busy,
         "n_prompt_tokens": prompt,
         "next_token": [{"n_decoded": decoded}]}
        for i, (prompt, decoded, busy) in enumerate(entries)
    ]


class _FakeClient:
    def __init__(self, slots, metrics=""):
        self._slots, self._metrics = slots, metrics

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        class _R:
            def __init__(self, payload, text=""):
                self._payload, self.text = payload, text

            def json(self):
                return self._payload

        if url.endswith("/slots"):
            return _R(self._slots)
        return _R({}, self._metrics)


def _patch(monkeypatch, slots, metrics=""):
    monkeypatch.setattr(llama.httpx, "AsyncClient",
                        lambda **kwargs: _FakeClient(slots, metrics))


def test_capacity_sums_prompt_and_generated_tokens(monkeypatch):
    """使用量は稼働中slotの prompt + 生成済みの合計。空きslotは数えない。"""
    _patch(monkeypatch, _slots([(1000, 50, True), (500, 10, True), (0, 0, False)]),
           "llamacpp:requests_deferred 2\n")
    cap = asyncio.run(llama.endpoint_capacity(8090))
    assert cap["available"] is True
    assert cap["slots"] == 3
    assert cap["busy"] == 2
    assert cap["ctx_total"] == 8192
    assert cap["ctx_used"] == 1560
    assert cap["deferred"] == 2
    # 余白を引いた実効容量で判定する
    assert cap["usable"] == int(8192 * llama.KV_HEADROOM_RATIO)
    assert cap["ctx_free"] == cap["usable"] - 1560
    assert cap["accepting"] is True


def test_not_accepting_when_pool_is_nearly_full(monkeypatch):
    """実測で97%は失敗したので、余白を割り込んだら受け入れない。"""
    _patch(monkeypatch, _slots([(7900, 0, True), (0, 0, False)]))
    cap = asyncio.run(llama.endpoint_capacity(8090))
    assert cap["accepting"] is False
    assert cap["ctx_free"] == 0


def test_not_accepting_when_all_slots_busy(monkeypatch):
    _patch(monkeypatch, _slots([(100, 0, True), (100, 0, True)]))
    cap = asyncio.run(llama.endpoint_capacity(8090))
    assert cap["busy"] == 2 and cap["slots"] == 2
    assert cap["accepting"] is False


def test_capacity_unavailable_for_unmanaged_port(monkeypatch):
    """停止中・管理外は available=False を返し、呼び出し側は素通しできる。"""
    def _boom(**kwargs):
        raise llama.httpx.HTTPError("connection refused")

    monkeypatch.setattr(llama.httpx, "AsyncClient", _boom)
    cap = asyncio.run(llama.endpoint_capacity(9999))
    assert cap["available"] is False
    assert cap["accepting"] is False


def test_await_capacity_returns_immediately_when_idle(monkeypatch):
    """誰も使っていなければ待たない（単発の大きなリクエストを妨げない）。"""
    _patch(monkeypatch, _slots([(0, 0, False), (0, 0, False)]))
    cap = asyncio.run(llama.await_capacity(8090, needed_tokens=7000, timeout_seconds=5))
    assert cap["busy"] == 0


def test_await_capacity_waits_until_room_frees_up(monkeypatch):
    """混雑中は空くまで待つ。llama.cpp は待たずに落とすため、投げる前に待つ。"""
    states = [
        _slots([(7000, 0, True), (0, 0, False)]),   # ほぼ満杯
        _slots([(7000, 0, True), (0, 0, False)]),
        _slots([(500, 0, True), (0, 0, False)]),    # 解放された
    ]
    calls = {"n": 0}

    def _client(**kwargs):
        index = min(calls["n"], len(states) - 1)
        calls["n"] += 1
        return _FakeClient(states[index])

    monkeypatch.setattr(llama.httpx, "AsyncClient", _client)
    cap = asyncio.run(llama.await_capacity(8090, needed_tokens=1000, timeout_seconds=30))
    assert cap["ctx_used"] == 500
    assert cap["accepting"] is True
    assert calls["n"] >= 3, "空くまで再取得している"


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload or {}, text

    def json(self):
        return self._payload


def test_capacity_error_is_detected_only_for_pool_exhaustion():
    """枯渇(500)だけ再試行する。単一リクエストのCTX超過(400)は投げ直しても無駄。"""
    from app.models_mgmt import runtime_provider as rp

    provider = rp.LlamaCppRuntimeProvider()
    exhausted = _Resp(500, {"error": {"message": "Context size has been exceeded."}})
    assert provider._is_capacity_error(exhausted) is True

    too_big = _Resp(400, {"error": {
        "message": "request (10015 tokens) exceeds the available context size (8192 tokens)"}})
    assert provider._is_capacity_error(too_big) is False

    other = _Resp(500, {"error": {"message": "internal error"}})
    assert provider._is_capacity_error(other) is False
    # 本文がJSONでない場合も落ちない
    assert provider._is_capacity_error(_Resp(500, None, "Context size has been exceeded.")) is True


def test_only_llamacpp_retries_on_capacity():
    """共有KVを持たないproviderは再試行しない（挙動を変えない）。"""
    from app.models_mgmt import runtime_provider as rp

    assert rp.LlamaCppRuntimeProvider()._capacity_retries() > 0
    assert rp.OpenAICompatibleRuntimeProvider()._capacity_retries() == 0
    assert rp.OllamaRuntimeProvider()._capacity_retries() == 0


def test_capacity_api_aggregates_running_endpoints(admin_client, monkeypatch):
    """ダッシュボード表示用。稼働中のエンドポイントだけをまとめて返す。"""
    import asyncio

    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "llama", "role": "llm", "n_parallel": 4, "port": 8090},
    ])
    monkeypatch.setattr(llama, "list_endpoints", lambda: [
        {"id": "ep-8090", "label": "メイン", "port": 8090, "running_alias": "llama",
         "aliases": ["llama"], "base_url": "", "active_alias": "llama"},
        {"id": "ep-8091", "label": "停止中", "port": 8091, "running_alias": "",
         "aliases": ["other"], "base_url": "", "active_alias": ""},
    ])

    async def _cap(port):
        return {"port": port, "available": True, "slots": 4, "busy": 2,
                "ctx_total": 8192, "ctx_used": 1000, "ctx_free": 5963,
                "usable": 6963, "deferred": 1, "accepting": True}

    monkeypatch.setattr(llama, "endpoint_capacity", _cap)
    monkeypatch.setattr("app.features.registry.is_enabled", lambda fid: False)

    response = admin_client.get("/api/v1/models/llama/capacity")
    assert response.status_code == 200
    body = response.json()
    # 停止中のエンドポイントは出さない（表示しても情報が無い）
    assert [e["id"] for e in body["endpoints"]] == ["ep-8090"]
    assert body["endpoints"][0]["busy"] == 2
    assert body["endpoints"][0]["deferred"] == 1
    assert body["omo"] is None


def test_capacity_api_includes_omo_when_installed(admin_client, monkeypatch):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "llama", "role": "llm", "n_parallel": 4, "port": 8090},
    ])
    monkeypatch.setattr(llama, "list_endpoints", lambda: [
        {"id": "ep-8090", "label": "メイン", "port": 8090, "running_alias": "llama",
         "aliases": ["llama"], "base_url": "", "active_alias": "llama"},
    ])

    async def _cap(port):
        return {"port": port, "available": True, "slots": 4, "busy": 0,
                "ctx_total": 8192, "ctx_used": 0, "ctx_free": 6963,
                "usable": 6963, "deferred": 0, "accepting": True}

    monkeypatch.setattr(llama, "endpoint_capacity", _cap)
    monkeypatch.setattr("app.features.registry.is_enabled", lambda fid: fid == "omo")
    monkeypatch.setattr("app.integrations.opencode.provider.get_settings",
                        lambda: {"model": "llama", "use_gateway": True})

    body = admin_client.get("/api/v1/models/llama/capacity").json()
    assert body["omo"]["installed"] is True
    assert body["omo"]["gated"] is True
    # ゲートウェイ経由なので OMo 既定の論理並列
    assert body["omo"]["concurrency"] == 5


def test_unavailable_endpoint_reports_zero_slots_not_full(monkeypatch):
    """モデル読込中は slots=0 になる。これを「満杯」と読ませてはいけない。

    UI 側は available=False / slots=0 を「起動中」として扱う。
    0/0 を busy>=slots と判定すると負荷「高」に見えてしまう。
    """
    def _boom(**kwargs):
        raise llama.httpx.HTTPError("503")

    monkeypatch.setattr(llama.httpx, "AsyncClient", _boom)
    cap = asyncio.run(llama.endpoint_capacity(8090))
    assert cap["available"] is False
    assert cap["slots"] == 0 and cap["busy"] == 0
    # accepting も False（空きがあると誤認させない）
    assert cap["accepting"] is False


def test_capacity_api_excludes_embedding_and_reranker(admin_client, monkeypatch):
    """embedding / reranker は RAG の補助で並列駆動の対象ではない。

    並べると本来見たいチャットモデルの行が埋もれる。
    """
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "list_instances", lambda: [
        {"alias": "chat", "role": "llm", "n_parallel": 4, "port": 8090},
        {"alias": "embed", "role": "embedding", "n_parallel": 4, "port": 8094},
        {"alias": "rerank", "role": "reranker", "n_parallel": 1, "port": 8095},
    ])
    monkeypatch.setattr(llama, "list_endpoints", lambda: [
        {"id": "ep-8090", "label": "", "port": 8090, "running_alias": "chat",
         "aliases": ["chat"], "base_url": "", "active_alias": "chat"},
        {"id": "ep-8094", "label": "", "port": 8094, "running_alias": "embed",
         "aliases": ["embed"], "base_url": "", "active_alias": "embed"},
        {"id": "ep-8095", "label": "", "port": 8095, "running_alias": "rerank",
         "aliases": ["rerank"], "base_url": "", "active_alias": "rerank"},
    ])

    async def _cap(port):
        return {"port": port, "available": True, "slots": 4, "busy": 0,
                "ctx_total": 8192, "ctx_used": 0, "ctx_free": 6963,
                "usable": 6963, "deferred": 0, "accepting": True}

    monkeypatch.setattr(llama, "endpoint_capacity", _cap)
    monkeypatch.setattr("app.features.registry.is_enabled", lambda fid: False)

    body = admin_client.get("/api/v1/models/llama/capacity").json()
    assert [e["running_alias"] for e in body["endpoints"]] == ["chat"]


def test_omo_slots_come_from_the_model_opencode_uses(admin_client, monkeypatch):
    """OMoの並列は、OpenCodeが使うモデルのスロット数で決める。

    先頭のエンドポイントを見ると、無関係なモデル（embeddingなど）の値を拾う。
    """
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "list_instances", lambda: [
        # 先頭は OpenCode が使わないモデル。ここを拾ってはいけない。
        {"alias": "other", "role": "llm", "n_parallel": 1, "port": 8091},
        {"alias": "used", "role": "llm", "n_parallel": 8, "port": 8090},
    ])
    monkeypatch.setattr(llama, "list_endpoints", lambda: [
        {"id": "ep-8091", "label": "", "port": 8091, "running_alias": "other",
         "aliases": ["other"], "base_url": "", "active_alias": "other"},
    ])

    async def _cap(port):
        return {"port": port, "available": True, "slots": 1, "busy": 0,
                "ctx_total": 8192, "ctx_used": 0, "ctx_free": 6963,
                "usable": 6963, "deferred": 0, "accepting": True}

    monkeypatch.setattr(llama, "endpoint_capacity", _cap)
    monkeypatch.setattr("app.features.registry.is_enabled", lambda fid: fid == "omo")
    monkeypatch.setattr("app.integrations.opencode.provider.get_settings",
                        lambda: {"model": "used", "use_gateway": False})

    omo = admin_client.get("/api/v1/models/llama/capacity").json()["omo"]
    assert omo["model"] == "used"
    assert omo["slots"] == 8           # 稼働中の other(1) ではなく used(8)
    assert omo["concurrency"] == 7     # 直結なのでメイン用に1本空ける


def test_throughput_survives_polling_slower_than_the_window():
    """見に来る間隔が窓より広くても速度が出ること。

    窓から出た点を全部捨てていたため、手元に1点しか残らず毎回 0 を返していた。
    画面は 3 秒ごとに見に来るが、携帯の回線や tunnel 越しではその倍以上に開く。
    実測（2026-09-06、12 秒間隔）では KV が 85,503 → 86,055 と動いているのに
    tok/s は 0.0 のままだった。利用者からは「生成しているのに速度が出ない」と見える。
    """
    port = 65001
    llama._THROUGHPUT_SAMPLES.pop(port, None)
    clock = [1000.0]
    import time as _time
    real_monotonic = _time.monotonic
    _time.monotonic = lambda: clock[0]
    try:
        # 窓（45秒）より広い 60 秒間隔でも、2 回目以降は速度が出る。
        assert llama._throughput(port, 1000.0) == 0.0     # 1点目は基準にするだけ
        clock[0] += 60.0
        rate = llama._throughput(port, 1600.0)
    finally:
        _time.monotonic = real_monotonic
    assert rate == pytest.approx(10.0), "窓より広い間隔で毎回 0 に戻ってはいけない"


def test_throughput_starts_over_after_a_long_silence():
    """間が空きすぎたら測り直す。

    止まっていた時間まで割り算に入れると、再開直後の速度が実態よりずっと低く出る。
    """
    port = 65002
    llama._THROUGHPUT_SAMPLES.pop(port, None)
    clock = [2000.0]
    import time as _time
    real_monotonic = _time.monotonic
    _time.monotonic = lambda: clock[0]
    try:
        llama._throughput(port, 500.0)
        clock[0] += llama.THROUGHPUT_STALE_SECONDS + 1
        stale = llama._throughput(port, 500.0)
        clock[0] += 10.0
        resumed = llama._throughput(port, 600.0)
    finally:
        _time.monotonic = real_monotonic
    assert stale == 0.0, "古すぎる基準は捨てて測り直す"
    assert resumed == pytest.approx(10.0), "測り直した後は普通に出る"
