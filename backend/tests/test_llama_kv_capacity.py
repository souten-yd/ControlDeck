"""共有KV(kv_unified)の空き容量観測と受け入れ制御。

実機で確認した llama.cpp の挙動が前提:
- kv_unified では ctx_size が全sequence共有のプール総量になる（各slotの n_ctx は上限値）。
- プールが尽きると待機せず HTTP 500 "Context size has been exceeded" を返し、
  実行中の他リクエストごと失敗する。
- 実測: ctx の 74% は通り、97% は失敗した。
"""
from __future__ import annotations

import asyncio

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
