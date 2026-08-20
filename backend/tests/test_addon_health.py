from __future__ import annotations

import asyncio
import httpx
import pytest

from tests.test_addon_contract import addon_manifest


@pytest.fixture()
def health_registry(monkeypatch, tmp_path):
    from app.addons import health, registry
    from app.addons.schema import parse_manifest

    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path / "data")
    registry.reset_runtime_state_for_tests()
    health.reset_for_tests()
    registry.install(parse_manifest(addon_manifest()))
    registry.set_enabled("fake-addon", True)
    return registry, health


def _client(responses: list[httpx.Response]) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_health_failure_threshold_and_unavailable_recovery(health_registry):
    registry, health = health_registry
    async def scenario():
        client = _client([httpx.Response(503), httpx.Response(503), httpx.Response(503), httpx.Response(200, json={
            "status": "healthy", "contract_version": "2.0", "contributions": {}, "setup": [],
        })])
        try:
            assert (await health.recheck("fake-addon", client))["state"] == "degraded"
            assert (await health.recheck("fake-addon", client))["state"] == "degraded"
            assert (await health.recheck("fake-addon", client))["state"] == "unavailable"
            assert (await health.recheck("fake-addon", client))["state"] == "healthy"
        finally:
            await client.aclose()
    asyncio.run(scenario())
    assert registry.health_observation("fake-addon").consecutive_failures == 0


def test_degraded_requires_two_healthy_checks_to_clear(health_registry):
    _registry, health = health_registry
    async def scenario():
        client = _client([
            httpx.Response(200, json={
                "status": "degraded", "contract_version": "2.0", "reason_code": "worker_not_installed",
                "message": "video missing", "action": {"kind": "open_route", "route": "/x/fake-addon/settings"},
            }),
            httpx.Response(200, json={"status": "healthy", "contract_version": "2.0"}),
            httpx.Response(200, json={"status": "healthy", "contract_version": "2.0"}),
        ])
        try:
            assert (await health.recheck("fake-addon", client))["state"] == "degraded"
            assert (await health.recheck("fake-addon", client))["state"] == "degraded"
            assert (await health.recheck("fake-addon", client))["state"] == "healthy"
        finally:
            await client.aclose()
    asyncio.run(scenario())


def test_health_rejects_redirect_oversize_and_unapproved_origin(health_registry, monkeypatch):
    registry, health = health_registry
    async def scenario():
        client = _client([
            httpx.Response(302, headers={"Location": "http://127.0.0.1:9999/"}),
            httpx.Response(200, content=b"x" * (64 * 1024 + 1)),
        ])
        try:
            assert (await health.recheck("fake-addon", client))["state"] == "degraded"
            assert (await health.recheck("fake-addon", client))["state"] == "degraded"
        finally:
            await client.aclose()

        current = registry.status("fake-addon")
        current["runtime"]["base_url"] = "https://unapproved.example"
        monkeypatch.setattr(registry, "status", lambda _addon_id: current)
        with pytest.raises(registry.AddonRegistryError, match="allowed_origins"):
            await health.fetch_health("fake-addon")
    asyncio.run(scenario())


def test_health_poll_skips_disabled_addons(health_registry, monkeypatch):
    registry, health = health_registry
    registry.set_enabled("fake-addon", False)
    called = False

    async def unexpected(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(health, "recheck", unexpected)
    asyncio.run(health.poll_once())
    assert called is False
