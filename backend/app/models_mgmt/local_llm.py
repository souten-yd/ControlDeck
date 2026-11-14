"""Common interface for ControlDeck-managed llama.cpp instances.

Strata is an independent optional Terminal add-on and does not implicitly
replace or alter the ControlDeck local LLM gateway.
"""
from __future__ import annotations

import asyncio
from typing import Any

LLAMA = "llama.cpp"
STRATA = "strata"
MAYA_S = "maya-s"
RUNTIMES = (LLAMA, STRATA, MAYA_S)


def module_for(runtime: str):
    from app.models_mgmt import llama, strata_gateway, maya_s
    if runtime == MAYA_S:
        return maya_s
    if runtime == STRATA:
        return strata_gateway
    if runtime == LLAMA:
        return llama
    raise KeyError(f"未知のローカルランタイムです: {runtime}")


def _llama_instances() -> list[dict]:
    from app.models_mgmt import llama

    try:
        return [{**item, "runtime": LLAMA} for item in llama.list_instances()]
    except Exception:  # noqa: BLE001 - 片方の設定破損で全体を落とさない
        return []


def _strata_instances() -> list[dict]:
    from app.models_mgmt import strata, strata_gateway
    # Removed optional runtime must not leave selectable ghost gateway models.
    return strata_gateway.list_instances() if strata.installed() else []


def _maya_s_instances() -> list[dict]:
    from app.models_mgmt import maya_s
    # Never advertise a ghost model when code or GGUF is not prepared.
    return maya_s.list_instances() if maya_s.installed() else []


def list_instances(*, runtime: str = "", role: str = "") -> list[dict]:
    """全ローカルランタイムのモデル設定を優先度順で返す。

    """
    items: list[dict] = []
    if runtime in ("", LLAMA):
        items += _llama_instances()
    if runtime in ("", STRATA):
        items += _strata_instances()
    if runtime in ("", MAYA_S):
        items += _maya_s_instances()
    if role:
        items = [item for item in items if str(item.get("role", "llm")) == role]
    return items


def llm_instances(*, runtime: str = "") -> list[dict]:
    """チャットに使える LLM だけ（embedding/reranker を除く）。"""
    return list_instances(runtime=runtime, role="llm")


def find(alias: str) -> dict | None:
    return next((item for item in list_instances() if str(item.get("alias")) == alias), None)


def runtime_of(alias: str) -> str:
    item = find(alias)
    return str(item.get("runtime")) if item else ""


def get_instance(alias: str) -> dict:
    item = find(alias)
    if item is None:
        raise KeyError(f"モデル設定が見つかりません: {alias}")
    return module_for(str(item["runtime"])).get_instance(alias)


def alias_taken_by_other_runtime(alias: str, runtime: str) -> str:
    """別ランタイムが同じ alias を使っていれば、そのランタイム名を返す。"""
    item = find(alias)
    if item is None:
        return ""
    other = str(item.get("runtime") or "")
    return other if other and other != runtime else ""


def port_taken_by_other_runtime(port: int, runtime: str) -> str:
    for item in list_instances():
        if int(item.get("port") or 0) != int(port):
            continue
        other = str(item.get("runtime") or "")
        if other and other != runtime:
            return other
    return ""


def instance_for_port(port: int) -> dict | None:
    return next((item for item in list_instances() if int(item.get("port") or 0) == int(port)), None)


def endpoint_ports() -> set[int]:
    return {int(item["port"]) for item in list_instances() if item.get("port")}


def residency_key(instance: dict) -> str:
    """ロード実測と GPU リースが共有する同一性キー（ランタイム別プレフィクス付き）。"""
    return module_for(str(instance.get("runtime") or LLAMA)).residency_key(instance)


def residency_key_for_alias(alias: str) -> str:
    item = find(alias)
    if item is None:
        return ""
    return module_for(str(item["runtime"])).residency_key(get_instance(alias))


async def health(alias: str) -> dict:
    item = find(alias)
    if item is None:
        return {"ok": False, "status_code": None}
    return await module_for(str(item["runtime"])).health(alias)


async def health_map(instances: list[dict] | None = None) -> dict[str, dict]:
    items = list_instances() if instances is None else instances
    states = await asyncio.gather(*(health(str(item["alias"])) for item in items))
    return {str(item["alias"]): state for item, state in zip(items, states, strict=True)}


async def ensure_ready(alias: str, *, timeout_seconds: int = 240) -> bool:
    item = find(alias)
    if item is None:
        return False
    return await module_for(str(item["runtime"])).ensure_ready(alias, timeout_seconds=timeout_seconds)


async def ensure_ready_by_base_url(base_url: str, *, timeout_seconds: int = 240) -> bool:
    from urllib.parse import urlsplit

    parsed = urlsplit(base_url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port:
        return False
    instance = instance_for_port(parsed.port)
    if instance is None:
        return False
    return await ensure_ready(str(instance["alias"]), timeout_seconds=timeout_seconds)


def start_instance(alias: str) -> tuple[bool, str]:
    item = find(alias)
    if item is None:
        return False, f"モデル設定が見つかりません: {alias}"
    return module_for(str(item["runtime"])).start_instance(alias)


def stop_instance(alias: str) -> tuple[bool, str]:
    item = find(alias)
    if item is None:
        return False, f"モデル設定が見つかりません: {alias}"
    return module_for(str(item["runtime"])).stop_instance(alias)


def mark_used_by_base_url(base_url: str) -> str | None:
    from app.models_mgmt import llama

    return llama.mark_used_by_base_url(base_url)


async def await_capacity(alias: str, port: int, needed_tokens: int, *,
                         timeout_seconds: float) -> dict[str, Any]:
    """KV プールの空きを待つ。

    llama.cpp uses a shared KV cache.
    """
    if runtime_of(alias) == LLAMA:
        from app.models_mgmt import llama

        return await llama.await_capacity(port, needed_tokens, timeout_seconds=timeout_seconds)
    return {"ok": True, "waited_seconds": 0.0, "reason": "capacity-not-tracked"}


def installed_runtimes() -> dict[str, bool]:
    from app.models_mgmt import llama

    from app.models_mgmt import strata, maya_s

    return {LLAMA: llama.is_installed(), STRATA: strata.installed(),
            MAYA_S: maya_s.installed()}
