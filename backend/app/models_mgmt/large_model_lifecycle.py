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


async def _wait_idle_requests(seconds: float = 60.0) -> bool:
    """True once no local LLM request is in flight (so nothing is cut off)."""
    from app.models_mgmt.resource_provider import provider

    adapter = provider()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while adapter._active_requests > 0:
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.5)
    return True


def follow_selection(runtime: str) -> None:
    """In exclusive mode the runtime that actually started becomes the selected one.

    Models showed "Strata selected" while Maya-S had been started on request:
    the selection drives the default connection and add-on AI targets, so it
    must follow what is running. Only the stored choice changes here; the other
    runtimes were already released by the start path.
    """
    from app.models_mgmt.runtime_policy import get_policy, save_policy

    try:
        policy = get_policy()
        if policy.coexistence != "exclusive" or policy.selected_runtime == runtime:
            return
        update: dict = {"selected_runtime": runtime}
        if runtime == "llama.cpp":
            from app.models_mgmt import llama

            backend = str(llama.get_config().get("backend") or "")
            if backend not in ("rocm", "vulkan"):
                return
            update["selected_backend"] = backend
        save_policy(policy.model_copy(update=update))
        logger.info("選択中ランタイムを稼働中の %s に合わせました", runtime)
    except Exception:  # noqa: BLE001 - never fail a start over the stored choice
        logger.exception("選択中ランタイムを更新できませんでした")


async def prepare_switch(alias: str) -> None:
    """Before a stopped model starts on request, free the large model that blocks it.

    Maya-S and Strata cannot both be resident, and neither fits beside a
    llama.cpp chat model. The resource broker only knows how to release
    llama.cpp models, so without this a request for Strata waits behind a
    resident Maya-S (and vice versa) until admission times out.
    """
    from app.models_mgmt import local_llm

    item = await asyncio.to_thread(local_llm.find, alias)
    runtime = str((item or {}).get("runtime") or "")
    if (item or {}).get("loaded"):
        return
    if runtime == "llama.cpp":
        # Small helpers (embedding / reranker) fit beside a large model.
        if str((item or {}).get("role") or "llm") != "llm":
            return
        if not await asyncio.to_thread(lambda: bool(_loaded_models())):
            return
        if not await _wait_idle_requests():
            logger.info("%s を起動するための解放を見送りました（推論中）", alias)
            return
        try:
            await release_for("llama.cpp")
        except RuntimeError as exc:
            logger.info("%s を起動するための解放を見送りました: %s", alias, exc)
            return
        await asyncio.to_thread(follow_selection, "llama.cpp")
        return
    if runtime not in (MAYA, STRATA):
        return
    if not await _wait_idle_requests():
        logger.info("%s を起動するための解放を見送りました（推論中）", alias)
        return
    try:
        await release_for(runtime)
    except RuntimeError as exc:
        logger.info("%s を起動するための解放を見送りました: %s", alias, exc)


async def release_for(runtime: str) -> None:
    """Free memory before an on-demand start of `runtime`.

    Maya-S and Strata cannot both fit; Maya-S also needs the GPU that llama.cpp
    chat models hold. Only models nobody is using are released by llama's own
    guard; Strata/Maya being stopped here is the trade the request asked for.
    """
    from app.models_mgmt import maya_s, strata, strata_gateway

    if runtime != MAYA and maya_s.installed() and await asyncio.to_thread(maya_s.is_running):
        if maya_s.get_settings().get("idle_exclude"):
            raise RuntimeError("Maya-Sが常駐指定で稼働中です")
        ok, reason = await asyncio.to_thread(maya_s.stop_instance)
        if not ok:
            raise RuntimeError(reason or "Maya-Sを停止できませんでした")
    if runtime != STRATA and strata.installed():
        for item in await asyncio.to_thread(strata_gateway.list_instances):
            if item.get("loaded"):
                if item.get("idle_exclude"):
                    raise RuntimeError(f"Strataモデル {item['alias']} が常駐指定で稼働中です")
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


def reconcile_selection() -> str | None:
    """Point the selected runtime at the large model that is actually loaded.

    Covers models started before this rule existed or by other paths. Only acts
    when exactly one runtime holds an LLM, so nothing is guessed.
    """
    from app.models_mgmt import llama

    active = {runtime for runtime, _item in _loaded_models()}
    try:
        if any(i.get("loaded") and str(i.get("role") or "llm") == "llm" for i in llama.list_instances()):
            active.add("llama.cpp")
    except Exception:  # noqa: BLE001
        pass
    if len(active) != 1:
        return None
    runtime = next(iter(active))
    follow_selection(runtime)
    return runtime


async def idle_unload_loop() -> None:
    await asyncio.to_thread(reconcile_selection)
    while True:
        try:
            await asyncio.sleep(60)
            await asyncio.to_thread(reconcile_selection)
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
