"""Idle unload, on-demand start and startup auto-start for Maya-S and Strata.

llama.cpp keeps its own loop (llama.idle_unload_loop); this module applies the
same shared runtime policy (idle_unload_enabled / idle_unload_minutes) and the
same per-model "idle_exclude" to the two large MoE runtimes.

Both runtimes hold most of the GPU and tens of GB of RAM, so starting one on
demand first stops the other (and idle llama.cpp chat models). A model that is
in use is never stopped: ControlDeck requests refresh the last-used time, and
external clients connected straight to the port keep it alive too.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

from app.config import data_dir

logger = logging.getLogger("control_deck.large_model_lifecycle")

MAYA = "maya-s"
STRATA = "strata"
_lock = threading.Lock()


def _usage_path() -> Path:
    return data_dir().resolve() / "runtimes" / "usage.json"


def _read_usage() -> dict[str, float]:
    try:
        value = json.loads(_usage_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): float(v) for k, v in value.items()
            if isinstance(v, (int, float))} if isinstance(value, dict) else {}


def mark_used(runtime: str, alias: str, *, at: float | None = None) -> None:
    """Record that a model was just used (or started). Never raises."""
    try:
        with _lock:
            usage = _read_usage()
            usage[f"{runtime}:{alias}"] = time.time() if at is None else at
            path = _usage_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(usage), encoding="utf-8")
            os.replace(tmp, path)
    except OSError:
        logger.warning("モデル利用時刻を記録できませんでした: %s:%s", runtime, alias)


def last_used(runtime: str, alias: str) -> float | None:
    return _read_usage().get(f"{runtime}:{alias}")


def _loaded_models() -> list[tuple[str, dict]]:
    from app.models_mgmt import maya_s, strata, strata_gateway

    rows: list[tuple[str, dict]] = []
    if maya_s.installed():
        for item in maya_s.list_instances():
            if item.get("loaded"):
                rows.append((MAYA, {**item, "idle_exclude": bool(maya_s.get_settings().get("idle_exclude"))}))
    if strata.installed():
        rows += [(STRATA, item) for item in strata_gateway.list_instances() if item.get("loaded")]
    return rows


async def release_for(runtime: str) -> None:
    """Free memory before an on-demand start of `runtime`.

    Maya-S and Strata cannot both fit; Maya-S also needs the GPU that llama.cpp
    chat models hold. Only models nobody is using are released by llama's own
    guard; Strata/Maya being stopped here is the trade the request asked for.
    """
    from app.models_mgmt import maya_s, strata, strata_gateway

    if runtime != MAYA and maya_s.installed() and await asyncio.to_thread(maya_s.is_running):
        ok, reason = await asyncio.to_thread(maya_s.stop_instance)
        if not ok:
            raise RuntimeError(reason or "Maya-Sを停止できませんでした")
    if runtime != STRATA and strata.installed():
        for item in await asyncio.to_thread(strata_gateway.list_instances):
            if item.get("loaded"):
                ok, reason = await asyncio.to_thread(strata_gateway.stop_instance, str(item["alias"]))
                if not ok:
                    raise RuntimeError(reason or "Strataモデルを停止できませんでした")
    if runtime == MAYA:
        from app.models_mgmt import llama

        await llama.release_loaded_llms()


def _client_connected(port: int) -> bool:
    from app.models_mgmt import llama

    return bool(port) and llama._has_connected_clients(port)


async def idle_unload_once(now: float | None = None) -> list[str]:
    """One pass of the idle unload. Returns the stopped "runtime:alias" keys."""
    from app.models_mgmt import maya_s, strata_gateway
    from app.models_mgmt.runtime_policy import get_policy

    policy = await asyncio.to_thread(get_policy)
    if not policy.idle_unload_enabled:
        return []
    now = time.time() if now is None else now
    deadline = now - policy.idle_unload_minutes * 60
    stopped: list[str] = []
    for runtime, item in await asyncio.to_thread(_loaded_models):
        alias = str(item["alias"])
        if item.get("idle_exclude"):
            continue
        last = last_used(runtime, alias)
        if last is None:
            # Unknown (started before this feature, or outside ControlDeck):
            # start its clock now instead of stopping it immediately.
            mark_used(runtime, alias, at=now)
            continue
        if last >= deadline:
            continue
        if await asyncio.to_thread(_client_connected, int(item.get("port") or 0)):
            mark_used(runtime, alias, at=now)
            continue
        module = maya_s if runtime == MAYA else strata_gateway
        ok, reason = await asyncio.to_thread(module.stop_instance, alias)
        if ok:
            stopped.append(f"{runtime}:{alias}")
            logger.info("idle %s model unloaded: %s", runtime, alias)
        else:
            logger.warning("idle %s model could not be unloaded: %s (%s)", runtime, alias, reason)
    return stopped


async def idle_unload_loop() -> None:
    while True:
        try:
            await asyncio.sleep(60)
            await idle_unload_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("Maya-S/Strata idle unload loop error")


async def autostart() -> str | None:
    """Start the one model marked "ControlDeck起動時に自動起動". Maya-S first.

    Only one of the two can be resident, so at most one is started.
    """
    from app.models_mgmt import maya_s, strata, strata_gateway

    try:
        if maya_s.installed() and maya_s.get_settings().get("auto_start") and maya_s.list_instances():
            if await maya_s.ensure_ready(maya_s.ALIAS, start=True):
                return f"{MAYA}:{maya_s.ALIAS}"
        if strata.installed():
            for item in await asyncio.to_thread(strata_gateway.list_instances):
                if item.get("auto_start") and item.get("activated"):
                    if await strata_gateway.ensure_ready(str(item["alias"])):
                        return f"{STRATA}:{item['alias']}"
    except Exception:  # noqa: BLE001 - startup must continue
        logger.exception("Maya-S/Strata auto-start failed")
    return None
