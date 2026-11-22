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
RADIANCE = "radiance"
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
    from app.models_mgmt import maya_s, radiance, strata, strata_gateway

    rows: list[tuple[str, dict]] = []
    if maya_s.installed():
        for item in maya_s.list_instances():
            if item.get("loaded"):
                rows.append((MAYA, {**item, "idle_exclude": bool(maya_s.get_settings().get("idle_exclude"))}))
    if strata.installed():
        rows += [(STRATA, item) for item in strata_gateway.list_instances() if item.get("loaded")]
    if radiance.installed():
        rows += [(RADIANCE, item) for item in radiance.list_instances() if item.get("loaded")]
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


import contextlib as _contextlib
import contextvars as _contextvars

# 要求による起動（ゲートウェイ・載せ替えの調停役・役割ごとのモデル）の間は、利用者の既定の選択を変えない。
# 2026-11-22: 役割で Maya-S を呼ぶたびに選択が Maya-S へ移り、既定（auto）の要求が Maya-S と Strata の間を
# 行き来した（90 分で 5 回書き換わった）。選択を変えるのは、利用者が画面でロードを押したときだけ。
_ON_DEMAND: _contextvars.ContextVar[bool] = _contextvars.ContextVar("on_demand_start", default=False)


@_contextlib.contextmanager
def on_demand_start():
    """要求による起動の区間。この中の起動は follow_selection で選択を変えない。"""
    token = _ON_DEMAND.set(True)
    try:
        yield
    finally:
        _ON_DEMAND.reset(token)


def follow_selection(runtime: str) -> None:
    """In exclusive mode the runtime that actually started becomes the selected one.

    Models showed "Strata selected" while Maya-S had been started on request:
    the selection drives the default connection and add-on AI targets, so it
    must follow what is running. Only the stored choice changes here; the other
    runtimes were already released by the start path.
    """
    from app.models_mgmt.runtime_policy import get_policy, save_policy

    if _ON_DEMAND.get():
        return
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


async def release_gpu_helpers() -> list[str]:
    """LLM を GPU に載せる前に、GPU に載っている補助（埋め込み・reranker）を止める。

    2026-11-20 利用者と合意（「順番に頼らない方法」）: 補助は Knowledge の検索・添付の保存・Workflow・拡張アプリなどで
    LLM より先に載ることがあり、載ったまま大きな LLM（Maya 31.5 GB・Radiance 31 GB / 34 GB のカード）を起動すると
    OOM になり得る。補助は使うときに数秒で載り直し、そのとき空きに合わせて載る（bge-m3 は --fit、GPU の ruri は
    空きが足りなければ CPU の控え）。常駐の指定があっても止める。CPU だけで動く補助（n_gpu_layers 0）は止めない。"""
    from app.models_mgmt import llama
    from app.workflows import cpu_reranker

    released = await cpu_reranker.stop_device("gpu")
    try:
        helpers = await asyncio.to_thread(llama.list_instances)
    except Exception:  # noqa: BLE001 - 補助の一覧が読めなくても LLM の起動は止めない
        logger.warning("GPU の補助の一覧を読めませんでした", exc_info=True)
        helpers = []
    for item in helpers:
        if str(item.get("role") or "llm") not in ("embedding", "reranker") or not item.get("loaded"):
            continue
        if int(item.get("n_gpu_layers", 999)) == 0:
            continue
        ok, _reason = await asyncio.to_thread(llama.stop_instance, str(item["alias"]))
        if ok:
            released.append(str(item["alias"]))
    if released:
        logger.info("LLM を載せる前に GPU の補助を止めました: %s", ", ".join(released))
    return released


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
        await release_gpu_helpers()
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
    if runtime not in (MAYA, STRATA, RADIANCE):
        return
    if not await _wait_idle_requests():
        logger.info("%s を起動するための解放を見送りました（推論中）", alias)
        return
    try:
        await release_for(runtime, keep=alias)
    except RuntimeError as exc:
        logger.info("%s を起動するための解放を見送りました: %s", alias, exc)


async def release_for(runtime: str, *, keep: str = "") -> None:
    """Free memory before an on-demand start of `runtime`.

    Maya-S and Strata cannot both fit; Maya-S also needs the GPU that llama.cpp
    chat models hold. Only models nobody is using are released by llama's own
    guard; Strata/Maya being stopped here is the trade the request asked for.
    """
    from app.models_mgmt import maya_s, radiance, strata, strata_gateway

    await release_gpu_helpers()
    if runtime != MAYA and maya_s.installed() and await asyncio.to_thread(maya_s.is_running):
        if maya_s.get_settings().get("idle_exclude"):
            raise RuntimeError("Maya-Sが常駐指定で稼働中です")
        ok, reason = await asyncio.to_thread(maya_s.stop_instance)
        if not ok:
            raise RuntimeError(reason or "Maya-Sを停止できませんでした")
    if strata.installed():
        # 別のランタイムを起こすときは全部、Strata を起こすときは頼まれたもの（keep）以外を降ろす。
        # Strata 同士の載せ替えを誰も降ろさず、起動が失敗して 300 秒後に 503 になっていた
        # （2026-11-22、役割を全部違うモデルにした実機で qwen-flash-next → qwen-flash-next-uc）。
        for item in await asyncio.to_thread(strata_gateway.list_instances):
            if runtime == STRATA and str(item.get("alias")) == keep:
                continue
            if item.get("loaded"):
                if item.get("idle_exclude"):
                    raise RuntimeError(f"Strataモデル {item['alias']} が常駐指定で稼働中です")
                ok, reason = await asyncio.to_thread(strata_gateway.stop_instance, str(item["alias"]))
                if not ok:
                    raise RuntimeError(reason or "Strataモデルを停止できませんでした")
    if radiance.installed():
        # One Radiance model holds the card: starting another (or another runtime) frees it.
        for item in await asyncio.to_thread(radiance.list_instances):
            if item.get("loaded"):
                if item.get("idle_exclude"):
                    raise RuntimeError(f"Radianceモデル {item['alias']} が常駐指定で稼働中です")
                ok, reason = await asyncio.to_thread(radiance.stop_instance, str(item["alias"]))
                if not ok:
                    raise RuntimeError(reason or "Radianceモデルを停止できませんでした")
    # Every large runtime needs the card a llama.cpp chat model holds. Strata used to be
    # left out: with Qwen3.8-27B resident its start failed ("no VRAM is left for the
    # expert cache", 2026-11-15). Embedding / reranker helpers stay (llama's own guard).
    if runtime in (MAYA, STRATA, RADIANCE):
        from app.models_mgmt import llama

        await llama.release_loaded_llms()


async def release_for_addon() -> tuple[list[tuple[str, str]], str]:
    """Stop the large runtimes (Maya-S / Strata / Radiance) so an add-on can use the card.

    The resource provider used to stop only llama.cpp here, while it reported every
    runtime's VRAM to the broker. With Strata resident it answered "already released,
    0 bytes", the broker read that as a refusal, and MediaForge / SonicForge waited until
    they fell back to RAM (image 235 s instead of ~3 s, 2026-11-15).

    The caller has already drained in-flight gateway requests. A live connection or the
    operator's "never release" still refuses. Returns the (runtime, alias) pairs stopped,
    so they can be brought back, and "" or the refusal reason.
    """
    from app.models_mgmt import maya_s, radiance, strata_gateway

    loaded = await asyncio.to_thread(_loaded_models)
    for runtime, item in loaded:
        if item.get("idle_exclude"):
            return [], "idle_excluded"
        if await asyncio.to_thread(_client_connected, int(item.get("port") or 0)):
            return [], "clients_connected"
    stopped: list[tuple[str, str]] = []
    for runtime, item in loaded:
        alias = str(item["alias"])
        module = {MAYA: maya_s, STRATA: strata_gateway, RADIANCE: radiance}[runtime]
        ok, reason = await asyncio.to_thread(module.stop_instance, alias)
        if not ok:
            logger.warning("%s %s を add-on のために降ろせませんでした: %s", runtime, alias, reason)
            return stopped, "stop_failed"
        stopped.append((runtime, alias))
    return stopped, ""


def _client_connected(port: int) -> bool:
    from app.models_mgmt import llama

    return bool(port) and llama._has_connected_clients(port)


async def idle_unload_once(now: float | None = None) -> list[str]:
    """One pass of the idle unload. Returns the stopped "runtime:alias" keys."""
    from app.models_mgmt import maya_s, radiance, strata_gateway
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
        module = {MAYA: maya_s, STRATA: strata_gateway, RADIANCE: radiance}[runtime]
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
    # 動いているランタイムに選択を合わせる処理（reconcile_selection）は回さない。要求による載せ替えのたびに
    # 利用者の既定の選択が書き換わっていた（2026-11-22）。選択は利用者が画面で変える。
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
    from app.models_mgmt import maya_s, radiance, strata, strata_gateway
    from app.models_mgmt.runtime_policy import prefer_models

    try:
        if maya_s.installed() and maya_s.get_settings().get("auto_start") and maya_s.list_instances():
            if await maya_s.ensure_ready(maya_s.ALIAS, start=True):
                return f"{MAYA}:{maya_s.ALIAS}"
        if strata.installed():
            for item in prefer_models(await asyncio.to_thread(strata_gateway.list_instances), STRATA):
                if item.get("auto_start") and item.get("activated"):
                    if await strata_gateway.ensure_ready(str(item["alias"])):
                        return f"{STRATA}:{item['alias']}"
        if radiance.installed():
            for item in prefer_models(await asyncio.to_thread(radiance.list_instances), RADIANCE):
                if item.get("auto_start"):
                    if await radiance.ensure_ready(str(item["alias"]), start=True):
                        return f"{RADIANCE}:{item['alias']}"
    except Exception:  # noqa: BLE001 - startup must continue
        logger.exception("Maya-S/Strata auto-start failed")
    return None
