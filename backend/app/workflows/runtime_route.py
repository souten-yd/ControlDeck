"""Live LLM runtime routing using availability, model, VRAM and context state."""
from __future__ import annotations

import json
from typing import Any


class RuntimeRouteError(ValueError):
    pass


def _candidate_config(raw: Any) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeRouteError("runtime候補はJSON arrayにしてください") from exc
    if not isinstance(raw, list) or len(raw) > 20:
        raise RuntimeRouteError("runtime候補は20件以内のJSON arrayにしてください")
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeRouteError(f"runtime候補{index + 1}はobjectにしてください")
        base_url = str(item.get("base_url") or "").rstrip("/")
        model = str(item.get("model") or "").strip()
        if not base_url.startswith(("http://", "https://")) or not model:
            raise RuntimeRouteError(f"runtime候補{index + 1}にはhttp(s) base_urlとmodelが必要です")
        result.append({
            "base_url": base_url, "model": model,
            "priority": max(-100, min(int(item.get("priority") or 0), 100)),
            "context_window": max(0, min(int(item.get("context_window") or 0), 1_048_576)),
            "vram_required_mb": max(0, min(int(item.get("vram_required_mb") or 0), 1_048_576)),
        })
    return result


async def runtime_snapshot() -> dict[str, Any]:
    """Collect bounded local runtime state; unavailable sensors remain None."""
    from app.models_mgmt import llama, ollama
    from app.models_mgmt.providers import list_providers
    from app.monitoring.collector import collector

    providers = await list_providers(include_unavailable=True)
    try:
        ollama_models = await ollama.list_models()
    except Exception:
        ollama_models = []
    llama_instances = llama.list_instances()
    gpu = (collector.latest or {}).get("gpu") or {}
    total = gpu.get("vram_total_bytes")
    used = gpu.get("vram_used_bytes")
    free = max(0, int(total) - int(used)) if total is not None and used is not None else None
    return {
        "gpu": {
            "name": gpu.get("name"), "vram_total_bytes": total,
            "vram_used_bytes": used, "vram_free_bytes": free,
        },
        "providers": [{
            "id": item.get("id"), "provider": item.get("provider"),
            "base_url": item.get("base_url"), "available": bool(item.get("available")),
            "selected": bool(item.get("selected")), "managed": bool(item.get("managed")),
            "models": list(item.get("models") or [])[:50],
        } for item in providers[:30]],
        "models": [{
            "runtime": "ollama", "base_url": ollama.base_url().rstrip("/") + "/v1",
            "model": item.get("name"), "loaded": bool(item.get("loaded")),
            "vram_bytes": item.get("vram"),
            "context_window": int(ollama.get_model_config(str(item.get("name") or "")).get("num_ctx") or 0),
        } for item in ollama_models[:50]] + [{
            "runtime": "llama.cpp", "base_url": str(item.get("base_url") or f"http://127.0.0.1:{item.get('port', 8080)}/v1").rstrip("/"),
            "model": str(item.get("alias") or ""), "loaded": bool(item.get("loaded")),
            "vram_bytes": None, "context_window": int(item.get("ctx_size") or 0),
        } for item in llama_instances[:20] if str(item.get("role", "llm")) == "llm"],
    }


async def choose_runtime(
    *, candidates: Any = None, min_context: int = 0, min_free_vram_mb: int = 0,
    strategy: str = "balanced", allow_unavailable: bool = False,
) -> dict[str, Any]:
    if strategy not in {"balanced", "availability", "loaded", "context", "vram"}:
        raise RuntimeRouteError("runtime route strategyが不正です")
    if min_context < 0 or min_context > 1_048_576:
        raise RuntimeRouteError("必要contextは0〜1,048,576にしてください")
    if min_free_vram_mb < 0 or min_free_vram_mb > 1_048_576:
        raise RuntimeRouteError("必要空きVRAMは0〜1,048,576MiBにしてください")
    snapshot = await runtime_snapshot()
    configured = _candidate_config(candidates)
    providers = {str(item["base_url"]).rstrip("/"): item for item in snapshot["providers"]}
    models = {
        (str(item["base_url"]).rstrip("/"), str(item["model"])): item
        for item in snapshot["models"]
    }
    if not configured:
        for provider in snapshot["providers"]:
            for model in provider["models"][:10]:
                configured.append({
                    "base_url": str(provider["base_url"]).rstrip("/"), "model": str(model),
                    "priority": 0, "context_window": 0, "vram_required_mb": 0,
                })
    evaluated: list[dict[str, Any]] = []
    free_bytes = snapshot["gpu"]["vram_free_bytes"]
    for candidate in configured:
        provider = providers.get(candidate["base_url"])
        model_state = models.get((candidate["base_url"], candidate["model"]), {})
        available = bool(provider and provider["available"])
        loaded = bool(model_state.get("loaded"))
        context_window = int(candidate["context_window"] or model_state.get("context_window") or 0)
        required_free = max(min_free_vram_mb, int(candidate["vram_required_mb"])) * 1024 * 1024
        reasons: list[str] = []
        eligible = True
        if not available and not allow_unavailable:
            eligible = False
            reasons.append("runtime unavailable")
        if min_context and context_window < min_context:
            eligible = False
            reasons.append(f"context {context_window} < {min_context}")
        if required_free and (free_bytes is None or int(free_bytes) < required_free):
            eligible = False
            reasons.append("free VRAM不足またはN/A")
        score = int(candidate["priority"])
        score += 100 if available else -100
        score += 25 if provider and provider["selected"] else 0
        score += 30 if loaded else 0
        if strategy == "loaded":
            score += 80 if loaded else 0
        elif strategy == "context":
            score += min(100, context_window // 4096)
        elif strategy == "vram" and free_bytes is not None:
            score += min(100, int(free_bytes) // (1024 * 1024 * 1024))
        elif strategy == "availability":
            score += 40 if available else 0
        else:
            score += min(30, context_window // 8192)
        evaluated.append({
            **candidate, "available": available, "loaded": loaded,
            "selected_runtime": bool(provider and provider["selected"]),
            "detected_context_window": context_window, "eligible": eligible,
            "score": score, "reasons": reasons,
        })
    eligible = sorted((item for item in evaluated if item["eligible"]), key=lambda item: (-item["score"], item["base_url"], item["model"]))
    if not eligible:
        reasons = sorted({reason for item in evaluated for reason in item["reasons"]})
        detail = f"（{' / '.join(reasons[:3])}）" if reasons else ""
        raise RuntimeRouteError(f"条件を満たすLLM runtimeがありません{detail}")
    selected = eligible[0]
    return {
        "base_url": selected["base_url"], "model": selected["model"],
        "strategy": strategy, "score": selected["score"], "loaded": selected["loaded"],
        "available": selected["available"], "context_window": selected["detected_context_window"],
        "vram_free_bytes": free_bytes,
        "reason": " / ".join([
            "available" if selected["available"] else "start on demand",
            "loaded" if selected["loaded"] else "not loaded",
            f"context={selected['detected_context_window'] or 'N/A'}",
        ]),
        "candidates": evaluated, "runtime_snapshot": snapshot,
    }
