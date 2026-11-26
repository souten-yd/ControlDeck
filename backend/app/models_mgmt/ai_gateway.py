"""Capability-based AI target resolution for generic host consumers.

This module is deliberately inside ControlDeck. Callers such as add-ons ask for a
text or vision capability and never select Ollama, llama.cpp, a port, or a model.
ControlDeck owns that policy and may change provider implementations without
changing the add-on contract.
"""
from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

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
    from app.models_mgmt.runtime_policy import get_policy
    preferred = get_policy().preferred_models.get("llama.cpp", "")
    candidates.sort(key=lambda item: (
        not bool(item.get("loaded")),
        str(item.get("alias")) != preferred,
        _order(item.get("order")),
        str(item.get("alias") or ""),
    ))
    selected = candidates[0]
    base_url = str(selected.get("base_url") or "").strip()
    model = str(selected.get("alias") or "").strip()
    if not base_url or not model:
        raise AITargetUnavailable("llama.cpp AI target の設定が不完全です")
    return AITarget(base_url=base_url, model=model, gateway_managed=True)


def _strata_target(capability: AICapability) -> AITarget:
    from app.models_mgmt import strata, strata_gateway
    if not strata.installed():
        raise AITargetUnavailable("Strataは未導入です")
    candidates = [item for item in strata_gateway.list_instances() if item.get("activated")]
    if capability == "vision.analyze":
        candidates = [item for item in candidates if item.get("vision", "on") != "off"]
    if not candidates:
        raise AITargetUnavailable(f"{capability} に対応する起動確認済みStrataモデルがありません")
    from app.models_mgmt.runtime_policy import get_policy
    preferred = get_policy().preferred_models.get("strata", "")
    selected = min(candidates, key=lambda item: (not item["loaded"], item["alias"] != preferred, item["alias"]))
    return AITarget(base_url=selected["base_url"], model=selected["alias"], gateway_managed=True)


def _radiance_target(capability: AICapability) -> AITarget:
    from app.models_mgmt import radiance
    if not radiance.installed():
        raise AITargetUnavailable("Radianceは未導入です")
    candidates = radiance.list_instances()
    if not candidates:
        raise AITargetUnavailable(f"{capability} に使えるRadianceモデルが登録されていません")
    # The containers carry their vision tower, so text and vision share one model.
    from app.models_mgmt.runtime_policy import get_policy
    preferred = get_policy().preferred_models.get("radiance", "")
    selected = min(candidates, key=lambda item: (not item["loaded"], item["alias"] != preferred, item["alias"]))
    return AITarget(base_url=selected["base_url"], model=selected["alias"], gateway_managed=True)


def _sly_radiance_target(capability: AICapability) -> AITarget:
    from app.models_mgmt import sly_radiance
    if not sly_radiance.installed():
        raise AITargetUnavailable("Sly-Radianceは未導入です")
    if capability == "vision.analyze":
        # The container runs with --limit-mm-per-prompt.image 0: text only.
        raise AITargetUnavailable("Sly-Radianceは画像を読めません（テキスト専用）")
    candidates = sly_radiance.list_instances()
    if not candidates:
        raise AITargetUnavailable(f"{capability} に使えるSly-Radianceモデルが登録されていません")
    from app.models_mgmt.runtime_policy import get_policy
    preferred = get_policy().preferred_models.get("sly-radiance", "")
    selected = min(candidates, key=lambda item: (not item["loaded"], item["alias"] != preferred, item["alias"]))
    return AITarget(base_url=selected["base_url"], model=selected["alias"], gateway_managed=True)


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
    from app.models_mgmt.runtime_policy import get_policy
    default_name = ollama.normalize_model_name(str(get_policy().preferred_models.get("ollama")
                                                  or ollama.get_settings().get("default_model") or ""))
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


def _upstream() -> str:
    from app.models_mgmt import gateway

    return gateway.upstream_base()


async def _upstream_target(upstream: str) -> AITarget:
    """ミラー: 上流の LLM（箱の中の中継 → 本番のゲートウェイ）を「ローカルの対象」にする。

    モデル名は auto（本番の Models で選んだものへ流れる）。中継が生きていてモデルが 1 つ以上
    あるときだけ使えるとする。中継は外部のモデル（cloud:）を断り、機密の印も本番へ渡る。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{upstream}/models")
        models = response.json().get("data") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise AITargetUnavailable("上流の LLM（中継）へ接続できません") from exc
    if not models:
        raise AITargetUnavailable("上流の LLM にモデルがありません")
    from app.models_mgmt.gateway import AUTO_MODEL

    return AITarget(base_url=upstream, model=AUTO_MODEL, gateway_managed=True)


async def resolve_ai_target(capability: AICapability) -> AITarget:
    """Resolve a text/vision request according to ControlDeck runtime policy."""
    from app.models_mgmt.runtime_policy import get_policy

    upstream = _upstream()
    if upstream:
        return await _upstream_target(upstream)
    runtime = str(get_policy().selected_runtime)
    if runtime == "llama.cpp":
        return await asyncio.to_thread(_llama_target, capability)
    if runtime == "strata":
        return await asyncio.to_thread(_strata_target, capability)
    if runtime == "radiance":
        return await asyncio.to_thread(_radiance_target, capability)
    if runtime == "sly-radiance":
        return await asyncio.to_thread(_sly_radiance_target, capability)
    if runtime == "maya-s":
        from app.models_mgmt import maya_s
        alias = await asyncio.to_thread(maya_s.running_alias)
        state = await maya_s.health(alias)
        if not state["ok"]:
            raise AITargetUnavailable("Mayaのテキストモデルを明示的に起動してください")
        instance = await asyncio.to_thread(maya_s.get_instance, alias)
        # 動いているエンジンが答えた images を正とする（設定ファイルを変えても、起動し直すまで変わらない）
        reads_images = state.get("images") if isinstance(state.get("images"), bool) else instance.get("vision")
        if capability == "vision.analyze" and not reads_images:
            if instance.get("vision_switched_off"):
                raise AITargetUnavailable("このモデルは画像の読み取りを切にしています（モデルの個別設定で入にして起動し直してください）")
            if instance.get("vision"):
                raise AITargetUnavailable("設定で画像の読み取りを入にしたあと、まだ起動し直していません（停止して起動してください）")
            raise AITargetUnavailable("Mayaの画像入力が設定されていません（再セットアップで有効になります）")
        return AITarget(base_url=instance["base_url"], model=instance["alias"], gateway_managed=True)
    if runtime == "ollama":
        return await _ollama_target(capability)
    raise AITargetUnavailable(f"選択中 runtime {runtime!r} は AI gateway に未対応です")


async def capability_available(capability: AICapability) -> bool:
    try:
        await resolve_ai_target(capability)
    except AITargetUnavailable:
        return False
    return True


def require_local_target(target: AITarget) -> None:
    """Verify Host-managed local inference, not merely a localhost HTTP URL."""
    from app.models_mgmt import llama

    if not target.gateway_managed:
        raise AITargetUnavailable("local-only execution is not verified for this runtime")
    if _upstream() and target.base_url == _upstream():
        # ミラーの上流は箱の中の loopback の中継で、先は本番のローカルのモデル（中継が外部を断る）
        return
    try:
        url = urlsplit(target.base_url)
        if (url.scheme != "http" or not ipaddress.ip_address(url.hostname or "").is_loopback
                or not url.port or url.username or url.password or url.query or url.fragment
                or url.path not in ("", "/v1")):
            raise ValueError()
    except ValueError as exc:
        raise AITargetUnavailable("local-only endpoint is invalid") from exc
    from app.models_mgmt import local_llm
    instances = local_llm.list_instances()
    instance = next((item for item in instances if item.get("alias") == target.model
                     and item.get("base_url") == target.base_url), None)
    if not instance or instance.get("role", "llm") != "llm":
        raise AITargetUnavailable("local-only model is not Host managed")
    model = Path(str(instance.get("model_path") or "")).resolve()
    if model.suffix.lower() != ".gguf" or not model.is_file():
        raise AITargetUnavailable("local-only model file is unavailable")


async def local_capability_available(capability: AICapability) -> bool:
    try:
        target = await resolve_ai_target(capability)
        await asyncio.to_thread(require_local_target, target)
    except (AITargetUnavailable, OSError):
        return False
    return True
