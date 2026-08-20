from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import time
from urllib.parse import urlsplit

import httpx

from app.addons import registry
from app.addons.contract import AddonHealthState, AddonReasonCode
from app.addons.schema import AddonHealthReport
from app.config import get_config

logger = logging.getLogger("control_deck.addons.health")
HEALTH_RESPONSE_LIMIT = 64 * 1024
HEALTH_TIMEOUT_SECONDS = 3.0
POLL_INTERVAL_SECONDS = 15.0
MAX_BACKOFF_SECONDS = 120.0
FAILURES_BEFORE_UNAVAILABLE = 3
_failure_counts: dict[str, int] = {}
_healthy_streaks: dict[str, int] = {}
_next_checks: dict[str, float] = {}


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _loopback_host(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def approved_health_url(base_url: str, health_path: str) -> str:
    parsed = urlsplit(base_url)
    allowed = set(get_config().addons.allowed_origins)
    if not _loopback_host(parsed.hostname) and _origin(base_url) not in allowed:
        raise registry.AddonRegistryError("runtime originがaddons.allowed_originsにありません")
    return f"{base_url.rstrip('/')}{health_path}"


def _failure_report(unavailable: bool) -> AddonHealthReport:
    return AddonHealthReport.model_validate({
        "status": "unavailable" if unavailable else "degraded",
        "contract_version": "2.0",
        "reason_code": AddonReasonCode.SERVICE_UNREACHABLE,
        "message": "拡張機能serviceへ接続できません",
        "action": {"kind": "retry"},
    })


async def fetch_health(addon_id: str, client: httpx.AsyncClient | None = None) -> AddonHealthReport:
    current = registry.status(addon_id)
    url = approved_health_url(current["runtime"]["base_url"], current["runtime"]["health_path"])
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SECONDS, follow_redirects=False)
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        if len(response.content) > HEALTH_RESPONSE_LIMIT:
            raise ValueError("health responseは64KiB以下にしてください")
        raw = json.loads(response.content)
        return AddonHealthReport.model_validate(raw)
    finally:
        if owns_client:
            await client.aclose()


async def recheck(addon_id: str, client: httpx.AsyncClient | None = None) -> dict:
    current = registry.status(addon_id)
    if not current["enabled"]:
        raise registry.AddonRegistryError("無効な拡張機能は再確認できません")
    failed = False
    try:
        fetched = await fetch_health(addon_id, client)
    except (httpx.HTTPError, ValueError, json.JSONDecodeError, registry.AddonRegistryError) as exc:
        failed = True
        count = _failure_counts.get(addon_id, 0) + 1
        _failure_counts[addon_id] = count
        _healthy_streaks[addon_id] = 0
        fetched = _failure_report(count >= FAILURES_BEFORE_UNAVAILABLE)
        logger.info("addon health失敗 id=%s count=%d type=%s", addon_id, count, type(exc).__name__)
    else:
        _failure_counts[addon_id] = 0
        previous = registry.health_observation(addon_id)
        if fetched.status == AddonHealthState.HEALTHY and previous and previous.report.status == AddonHealthState.DEGRADED:
            streak = _healthy_streaks.get(addon_id, 0) + 1
            _healthy_streaks[addon_id] = streak
            if streak < 2:
                fetched = previous.report
        else:
            _healthy_streaks[addon_id] = 0
    result = registry.update_health(addon_id, fetched, failed=failed)
    failures = _failure_counts.get(addon_id, 0)
    interval = min(MAX_BACKOFF_SECONDS, POLL_INTERVAL_SECONDS * (2 ** max(0, failures - 1))) if failed else POLL_INTERVAL_SECONDS
    _next_checks[addon_id] = time.monotonic() + interval
    return result


async def poll_once(client: httpx.AsyncClient | None = None) -> None:
    now = time.monotonic()
    enabled_ids = {item["id"] for item in registry.list_addons() if item["enabled"]}
    for addon_id in list(_next_checks):
        if addon_id not in enabled_ids:
            _next_checks.pop(addon_id, None)
            _failure_counts.pop(addon_id, None)
            _healthy_streaks.pop(addon_id, None)
    for addon_id in sorted(enabled_ids):
        if now < _next_checks.get(addon_id, 0):
            continue
        with contextlib.suppress(registry.AddonRegistryError):
            await recheck(addon_id, client)


async def health_loop() -> None:
    while True:
        try:
            await poll_once()
        except Exception:  # noqa: BLE001 - one bad add-on must not stop the host loop
            logger.exception("addon health pollingに失敗しました")
        await asyncio.sleep(1)


def reset_for_tests() -> None:
    _failure_counts.clear()
    _healthy_streaks.clear()
    _next_checks.clear()
