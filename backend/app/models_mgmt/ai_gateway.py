"""Capability-based AI target resolution for generic host consumers.

This module is deliberately inside ControlDeck. Callers such as add-ons ask for a
text or vision capability and never select Ollama, llama.cpp, a port, or a model.
ControlDeck owns that policy and may change provider implementations without
changing the add-on contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


AICapability = Literal["text.generate", "vision.analyze"]


class AITargetUnavailable(RuntimeError):
    """No configured model can satisfy the requested AI capability."""


@dataclass(frozen=True, slots=True)
class AITarget:
    base_url: str
    model: str
    # Internal ControlDeck detail. When true, execution must use the same Broker
    # lease/admission path as the public /llm gateway before calling the provider.
    gateway_managed: bool = False


def _order(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 1_000_000
    return number if number > 0 else 1_000_000


def _llama_target(capability: AICapability) -> AITarget:
    from app.models_mgmt import llama

    candidates = [
        item for item in llama.list_instances()
        if str(item.get("role", "llm")) == "llm"
    ]
    if capability == "vision.analyze":
        candidates = [item for item in candidates if str(item.get("mmproj_path") or "").strip()]
    if not candidates:
        raise AITargetUnavailable(f"{capability} に対応する llama.cpp モデルがありません")
    candidates.sort(key=lambda item: (
        not bool(item.get("loaded")),
        _order(item.get("order")),
        str(item.get("alias") or ""),
    ))
    selected = candidates[0]
    base_url = str(selected.get("base_url") or "").strip()
    model = str(selected.get("alias") or "").strip()
    if not base_url or not model:
        raise AITargetUnavailable("llama.cpp AI target の設定が不完全です")
    return AITarget(base_url=base_url, model=model, gateway_managed=True)


async def _ollama_target(capability: AICapability) -> AITarget:
    from app.models_mgmt import ollama

    try:
        models = await ollama.list_models()
        running = await ollama.running_models()
    except ollama.OllamaError as exc:
        raise AITargetUnavailable("Ollama AI runtime を利用できません") from exc

    running_names = {
        ollama.normalize_model_name(str(item.get("name") or item.get("model") or ""))
        for item in running
    }
    default_name = ollama.normalize_model_name(
        str(ollama.get_settings().get("default_model") or "")
    )
    candidates: list[tuple[dict, dict]] = []
    for item in models:
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        config = ollama.get_model_config(name)
        if capability == "vision.analyze" and config.get("vlm_enabled") is not True:
            continue
        candidates.append((item, config))
    if not candidates:
        raise AITargetUnavailable(f"{capability} に対応する Ollama モデルがありません")

    def key(entry: tuple[dict, dict]) -> tuple[bool, bool, int, str]:
        item, config = entry
        name = str(item.get("name") or item.get("model") or "")
        normalized = ollama.normalize_model_name(name)
        return (
            normalized not in running_names,
            normalized != default_name,
            _order(config.get("order")),
            normalized,
        )

    selected, _config = min(candidates, key=key)
    model = str(selected.get("name") or selected.get("model") or "").strip()
    if not model:
        raise AITargetUnavailable("Ollama AI target の設定が不完全です")
    return AITarget(base_url=f"{ollama.base_url().rstrip('/')}/v1", model=model)


async def resolve_ai_target(capability: AICapability) -> AITarget:
    """Resolve a text/vision request according to ControlDeck runtime policy."""
    from app.models_mgmt.runtime_policy import get_policy

    runtime = str(get_policy().selected_runtime)
    if runtime == "llama.cpp":
        return _llama_target(capability)
    if runtime == "ollama":
        return await _ollama_target(capability)
    raise AITargetUnavailable(f"選択中 runtime {runtime!r} は AI gateway に未対応です")


async def capability_available(capability: AICapability) -> bool:
    try:
        await resolve_ai_target(capability)
    except AITargetUnavailable:
        return False
    return True
