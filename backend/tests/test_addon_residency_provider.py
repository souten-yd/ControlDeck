"""Add-on を「場所を空けられる側」として broker に参加させる。

これまで場所を空けられるのは常駐 LLM だけで、Add-on は時計で自分の model を
降ろすしかなかった。短くすれば続けて作るたびに読み直し、長くすれば他の枠を削る。
どちらに振っても片方が痛む。

実測:
  2026-09-11  batch のあとに画像 worker が 19.4GB を抱えたまま残り、
              音楽生成が 300 秒待って期限切れになった
  2026-09-16  音楽の常駐を 180 秒で片付ける掃除が、走っている音楽 worker を
              殺していた（失敗 12 件が 215.1 秒と 245.1 秒に割れ、差の 30 秒が
              掃除の周期だった）
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.resources import addon_provider
from app.resources.addon_provider import AddonResidencyProvider


def _provider(monkeypatch, *, addons, handler) -> AddonResidencyProvider:
    provider = AddonResidencyProvider()
    monkeypatch.setattr(provider, "_enabled_addons", staticmethod(lambda: addons))
    monkeypatch.setattr(provider, "_url", lambda addon_id, path: f"http://addon/{addon_id}{path}")
    monkeypatch.setattr(provider, "_headers", staticmethod(lambda addon_id: {}))

    # 差し替える前に本物を掴む。掴まずに使うと、差し替えた自分自身を呼んで
    # 無限に入れ子になる。
    real_client = httpx.AsyncClient

    class _Client:
        def __init__(self, *args, **kwargs):
            self._inner = real_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self):
            return self._inner

        async def __aexit__(self, *args):
            await self._inner.aclose()

    monkeypatch.setattr(addon_provider.httpx, "AsyncClient", _Client)
    return provider


def test_what_add_ons_hold_is_declared(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"device_id": "gpu0", "reserved_bytes": 8 * 1024**3})

    provider = _provider(monkeypatch, addons=[{"id": "sonic-forge", "enabled": True}], handler=handler)
    asyncio.run(provider.refresh())
    reservations = provider.reservations()
    assert len(reservations) == 1
    assert reservations[0].owner == "addon:sonic-forge"
    assert reservations[0].reserved_bytes == 8 * 1024**3


def test_an_add_on_without_the_endpoint_is_counted_as_holding_nothing(monkeypatch):
    """古い Add-on はこの入口を持たない。持たないことを故障として扱わない。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider = _provider(monkeypatch, addons=[{"id": "old-addon", "enabled": True}], handler=handler)
    assert asyncio.run(provider.refresh()) == {}
    assert provider.reservations() == []


def test_an_unreachable_add_on_does_not_stop_admission(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    provider = _provider(monkeypatch, addons=[{"id": "sonic-forge", "enabled": True}], handler=handler)
    assert asyncio.run(provider.refresh()) == {}


def test_asking_releases_and_reports_what_was_freed(monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/residency"):
            size = 8 * 1024**3 if "sonic" in request.url.path else 16 * 1024**3
            return httpx.Response(200, json={"device_id": "gpu0", "reserved_bytes": size})
        calls.append(request.url.path)
        return httpx.Response(200, json={"released": True, "reason": "released", "freed_bytes": 4 * 1024**3})

    provider = _provider(
        monkeypatch,
        addons=[{"id": "sonic-forge", "enabled": True}, {"id": "media-forge", "enabled": True}],
        handler=handler,
    )
    asyncio.run(provider.refresh())
    released, reason, freed = asyncio.run(provider.step_aside("gpu0"))
    assert released is True
    assert freed == 8 * 1024**3
    # 多く抱えている方から頼む。少ない方から回ると、要らないものまで降ろす。
    assert calls[0].startswith("/media-forge")
    assert "media-forge:released" in reason
    # 降りたなら申告も下げる。古い数を次の判断へ持ち越さない。
    assert provider.reservations() == []


def test_a_busy_add_on_is_left_alone(monkeypatch):
    """使用中のものを取り上げても、取り上げられた側が落ちるだけで取り合いは解決しない。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/residency"):
            return httpx.Response(200, json={"device_id": "gpu0", "reserved_bytes": 8 * 1024**3})
        return httpx.Response(200, json={"released": False, "reason": "in_use_or_empty", "freed_bytes": 0})

    provider = _provider(monkeypatch, addons=[{"id": "sonic-forge", "enabled": True}], handler=handler)
    asyncio.run(provider.refresh())
    released, reason, freed = asyncio.run(provider.step_aside("gpu0"))
    assert released is False and freed == 0
    assert "in_use_or_empty" in reason
    # 抱えたままなので、申告も残る。
    assert provider.reservations()[0].reserved_bytes == 8 * 1024**3


def test_a_nonsense_declaration_is_bounded(monkeypatch):
    """壊れた Add-on が莫大な数を返しても、それに引きずられない。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"device_id": "gpu0", "reserved_bytes": 10 ** 20})

    provider = _provider(monkeypatch, addons=[{"id": "sonic-forge", "enabled": True}], handler=handler)
    held = asyncio.run(provider.refresh())
    assert held["sonic-forge"] == addon_provider.MAX_DECLARED_BYTES


def test_another_device_is_not_this_provider_s_business(monkeypatch):
    provider = AddonResidencyProvider()
    released, reason, freed = asyncio.run(provider.step_aside("gpu1"))
    assert (released, reason, freed) == (False, "not_this_device", 0)
