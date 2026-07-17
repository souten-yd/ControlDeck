"""LLM runtime横断の選択・共通運用ポリシー。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import data_dir


class ChatDefaults(BaseModel):
    # 長文回答と20ノード級のworkflow JSONを途中で切らない初期値。モデル/VRAMに
    # 合わせてModel画面から64〜131072の範囲で変更できる。
    max_output_tokens: int = Field(default=8192, ge=64, le=131072)
    reasoning: Literal["off", "auto", "on"] = "off"
    timeout_seconds: int = Field(default=300, ge=10, le=1800)


class DeepResearchSettings(BaseModel):
    evidence_context_chars: int = Field(default=90000, ge=12000, le=500000)
    max_report_tokens: int = Field(default=32768, ge=8192, le=131072)
    timeout_seconds: int = Field(default=1800, ge=300, le=7200)


class AmdGpuSettings(BaseModel):
    enabled: bool = False
    profile: Literal["quiet", "balanced", "full", "custom"] = "quiet"
    power_limit_watts: int = Field(default=210, ge=1, le=2000)
    memory_clock_mode: Literal["auto", "minimum", "limit"] = "auto"
    memory_clock_level: int = Field(default=0, ge=0, le=63)
    core_clock_mode: Literal["auto", "limit"] = "auto"
    core_clock_level: int = Field(default=0, ge=0, le=63)


class RuntimePolicy(BaseModel):
    selected_runtime: Literal["ollama", "llama.cpp"] = "ollama"
    selected_backend: Literal["rocm", "vulkan", ""] = ""
    coexistence: Literal["exclusive", "coexist"] = "exclusive"
    idle_unload_enabled: bool = False
    idle_unload_minutes: int = Field(default=30, ge=1, le=1440)
    max_loaded_models: int = Field(default=1, ge=1, le=16)
    default_model_ref: str = Field(default="", max_length=512)
    assistant_name: str = Field(default="AIアシスタント", min_length=1, max_length=64)
    chat: ChatDefaults = Field(default_factory=ChatDefaults)
    deep_research: DeepResearchSettings = Field(default_factory=DeepResearchSettings)
    amd_gpu: AmdGpuSettings = Field(default_factory=AmdGpuSettings)


def _path() -> Path:
    return data_dir() / "model-runtime-policy.json"


def _derived_default() -> RuntimePolicy:
    """既存llama設定がある環境は、その選択を初期値として引き継ぐ。"""
    try:
        from app.models_mgmt import llama

        cfg = llama.get_config()
        backend = str(cfg.get("backend") or "")
        if llama.is_installed() and backend in ("rocm", "vulkan"):
            return RuntimePolicy(selected_runtime="llama.cpp", selected_backend=backend)
    except Exception:
        pass
    return RuntimePolicy()


def get_policy() -> RuntimePolicy:
    path = _path()
    if path.exists():
        try:
            return RuntimePolicy.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return _derived_default()


def save_policy(policy: RuntimePolicy) -> RuntimePolicy:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return policy


def ensure_gpu_profile(*, force: bool = False, base_url: str = "") -> dict:
    """すべてのLLMロード/生成経路から呼ぶ共通preflight。"""
    from app.models_mgmt import amd_gpu

    result = amd_gpu.apply_profile(get_policy().amd_gpu, force=force)
    if base_url:
        from app.models_mgmt import llama

        llama.mark_used_by_base_url(base_url)
    return result


async def environment() -> dict:
    from app.models_mgmt import amd_gpu, llama, ollama

    policy = get_policy()
    detected = llama.detect_backends()
    installed = set(llama.installed_backends())
    ollama_status = await ollama.status()
    llama_status = llama.runtime_status()
    health_states = await asyncio.gather(*(
        llama.health(str(item["alias"])) for item in llama_status.get("instances", [])
    )) if llama_status["installed"] else []
    any_llama_running = any(state.get("ok") for state in health_states)
    runtimes = [{
        "id": "ollama", "runtime": "ollama", "backend": "auto", "label": "Ollama",
        "available": bool(ollama_status.get("available")), "installed": True,
        "selected": policy.selected_runtime == "ollama",
    }]
    for backend, label in (("rocm", "llama.cpp / ROCm"), ("vulkan", "llama.cpp / Vulkan")):
        if not detected.get(backend) and backend not in installed:
            continue
        runtimes.append({
            "id": f"llama.cpp:{backend}", "runtime": "llama.cpp", "backend": backend,
            "label": label, "available": bool(detected.get(backend)),
            "installed": backend in installed,
            "selected": policy.selected_runtime == "llama.cpp" and policy.selected_backend == backend,
            "running": any_llama_running and llama_status.get("backend") == backend,
        })
    gpu_caps = amd_gpu.capabilities()
    if gpu_caps:
        pwr = gpu_caps["power"]
        gpu_caps["presets"] = {
            "quiet": {"power_limit_watts": pwr["min_watts"],
                      "memory_clock_mode": "limit" if gpu_caps["memory"]["supported"] else "auto",
                      "memory_clock_level": max(0, len(gpu_caps["memory"]["levels"]) - 2),
                      "core_clock_mode": "auto", "core_clock_level": 0},
            "balanced": {"power_limit_watts": round((pwr["min_watts"] + pwr["max_watts"]) / 2),
                         "memory_clock_mode": "auto", "memory_clock_level": 0,
                         "core_clock_mode": "auto", "core_clock_level": 0},
            "full": {"power_limit_watts": pwr["default_watts"], "memory_clock_mode": "auto",
                     "memory_clock_level": 0, "core_clock_mode": "auto", "core_clock_level": 0},
        }
    return {
        "platform": "Linux", "gpu": "AMD" if detected.get("rocm") else "GPU",
        "runtimes": runtimes, "policy": policy.model_dump(), "amd_gpu": gpu_caps,
    }


def normalize_gpu_profile(policy: RuntimePolicy) -> RuntimePolicy:
    """presetを実機能力から具体値へ解決して、サーバー保存値を自己完結させる。"""
    from app.models_mgmt import amd_gpu

    settings = policy.amd_gpu
    if not settings.enabled:
        return policy
    caps = amd_gpu.capabilities()
    if caps is None:
        raise ValueError("電力・VRAM周波数制御に対応するAMD GPUがありません")
    pwr = caps["power"]
    if settings.profile == "quiet":
        settings.power_limit_watts = pwr["min_watts"]
        settings.memory_clock_mode = "limit" if caps["memory"]["supported"] else "auto"
        settings.memory_clock_level = max(0, len(caps["memory"]["levels"]) - 2)
        settings.core_clock_mode = "auto"
        settings.core_clock_level = 0
    elif settings.profile == "balanced":
        settings.power_limit_watts = round((pwr["min_watts"] + pwr["max_watts"]) / 2)
        settings.memory_clock_mode = "auto"
        settings.memory_clock_level = 0
        settings.core_clock_mode = "auto"
        settings.core_clock_level = 0
    elif settings.profile == "full":
        settings.power_limit_watts = pwr["default_watts"]
        settings.memory_clock_mode = "auto"
        settings.memory_clock_level = 0
        settings.core_clock_mode = "auto"
        settings.core_clock_level = 0
    elif settings.profile == "custom":
        # customは実機DPM level内でMCLK/SCLKを個別指定できる。
        # preset（balanced/full）へ戻した時は各分岐で必ずautoへ戻す。
        pass
    if not pwr["min_watts"] <= settings.power_limit_watts <= pwr["max_watts"]:
        raise ValueError(f"AMD GPU電力上限は{pwr['min_watts']}〜{pwr['max_watts']}Wで指定してください")
    if settings.memory_clock_mode != "auto" and not caps["memory"]["supported"]:
        raise ValueError("このAMD GPUはVRAM周波数levelの変更に対応していません")
    levels = caps["memory"]["levels"]
    if settings.memory_clock_mode == "limit" and not any(
        item["level"] == settings.memory_clock_level for item in levels
    ):
        raise ValueError("VRAM周波数levelが実機の範囲外です")
    core_levels = caps["core"]["levels"]
    if settings.core_clock_mode == "limit" and not any(
        item["level"] == settings.core_clock_level for item in core_levels
    ):
        raise ValueError("GPUコア周波数levelが実機の範囲外です")
    return policy


async def apply_selection(policy: RuntimePolicy) -> None:
    """排他モードのruntime切替。サービス削除はせず、競合モデルだけ解放する。"""
    from app.models_mgmt import llama, ollama

    if policy.selected_runtime == "llama.cpp":
        if policy.selected_backend not in llama.installed_backends():
            raise ValueError(f"llama.cpp / {policy.selected_backend} は未導入です")
        current = llama.get_config().get("backend")
        if current != policy.selected_backend:
            running = [str(item["alias"]) for item in llama.list_instances() if item.get("loaded")]
            for alias in running:
                llama.stop_instance(alias)
            llama.switch_backend(policy.selected_backend)
            for alias in running:
                ok, detail = llama.start_instance(alias)
                if not ok:
                    raise RuntimeError(detail or f"backend切替後の起動に失敗しました: {alias}")
        if policy.coexistence == "exclusive":
            for model in await ollama.running_models():
                name = str(model.get("name") or model.get("model") or "")
                if name:
                    await ollama.unload(name)
    elif policy.coexistence == "exclusive":
        instances = llama.list_instances()
        for item in instances:
            if item.get("loaded"):
                llama.stop_instance(str(item["alias"]))
        if not instances and (await llama.health()).get("ok", False):
            llama.stop_instance()


async def _wait_llama_health(alias: str, seconds: int = 90) -> bool:
    from app.models_mgmt import llama

    for _ in range(seconds):
        if (await llama.health(alias)).get("ok"):
            return True
        await asyncio.sleep(1)
    return False


async def prepare_deep_research_context(base_url: str, model: str) -> dict:
    """モデル個別のDeep Research CTXを適用し、復元に必要な状態を返す。"""
    from urllib.parse import urlsplit

    from app.models_mgmt import llama, ollama

    result = {
        "enabled": False,
        "model": model,
        "requested_tokens": None,
        "applied": False,
        "runtime": "unknown",
        "request_context_tokens": None,
        "previous_tokens": None,
        "changed": False,
        "was_loaded": False,
        "reason": "",
    }
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[:-3].rstrip("/")
    if normalized == ollama.base_url().rstrip("/"):
        config = ollama.get_model_config(model)
        requested = int(config.get("deep_research_num_ctx") or 0)
        normal = int(config.get("num_ctx") or 0)
        result.update({"runtime": "ollama", "requested_tokens": requested or None,
                       "previous_tokens": normal or None})
        if requested <= 0:
            result.update({
                "applied": normal > 0, "request_context_tokens": normal or None,
                "reason": "専用CTX未設定のためモデル個別の通常CTXを使用",
            })
            return result
        if requested == normal:
            result.update({
                "enabled": True, "applied": True, "request_context_tokens": requested,
                "reason": "通常CTXと同じため切替不要",
            })
            return result
        running = await ollama.running_models()
        result["was_loaded"] = any(
            str(item.get("name") or item.get("model") or "") == model for item in running
        )
        result.update({
            "enabled": True, "applied": True, "request_context_tokens": requested,
            "changed": requested != normal,
            "reason": "モデル個別Deep Research CTXをOllama requestへ適用",
        })
        return result

    parsed = urlsplit(base_url)
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        result["reason"] = "外部OpenAI互換runtimeはrequest単位CTX変更を保証できません"
        return result
    instance = next((item for item in llama.list_instances() if int(item.get("port", 0)) == parsed.port), None)
    if instance is None:
        result["reason"] = "管理対象runtimeではありません"
        return result
    result["runtime"] = "llama.cpp"
    alias = str(instance.get("alias") or "")
    current = int(instance.get("ctx_size") or 0)
    requested = int(instance.get("deep_research_ctx_size") or 0)
    result.update({"model": alias, "requested_tokens": requested or None,
                   "previous_tokens": current, "was_loaded": bool(instance.get("loaded"))})
    if requested <= 0:
        result["reason"] = "モデル個別設定なし（通常CTXを使用）"
        return result
    result["enabled"] = True
    if current == requested:
        result.update({"applied": True, "reason": "通常CTXと同じため再ロード不要"})
        return result

    try:
        llama.save_instance(alias, {"ctx_size": requested})
        ok, detail = await asyncio.to_thread(llama.start_instance, alias)
        if not ok:
            raise RuntimeError(detail or "llama.cppの再起動に失敗")
        if not await _wait_llama_health(alias):
            raise RuntimeError("要求CTXで再ロード後、health確認がtimeout")
        result.update({"applied": True, "changed": True,
                       "reason": "モデル個別Deep Research CTXでllama.cppを再ロード"})
        return result
    except Exception as exc:
        llama.save_instance(alias, {"ctx_size": current})
        if result["was_loaded"]:
            await asyncio.to_thread(llama.start_instance, alias)
        else:
            llama.stop_instance(alias)
        result["reason"] = f"適用失敗のため復元: {type(exc).__name__}"
        return result


async def restore_deep_research_context(state: dict) -> dict:
    """Deep ResearchでCTXを変更した場合だけ、実行前の通常モデル状態へ戻す。"""
    from app.models_mgmt import llama, ollama

    restored = {"restored": False, "restore_reason": "変更なし"}
    if not state.get("changed"):
        return restored
    try:
        if state.get("runtime") == "ollama":
            model = str(state.get("model") or "")
            await ollama.unload(model)
            if state.get("was_loaded"):
                await ollama.load(model, options=ollama.effective_options(model))
            restored.update({"restored": True, "restore_reason": "通常モデルCTXへ復元"})
            return restored
        if state.get("runtime") == "llama.cpp":
            alias = str(state.get("model") or "")
            previous = int(state.get("previous_tokens") or 0)
            llama.save_instance(alias, {"ctx_size": previous})
            if state.get("was_loaded"):
                ok, detail = await asyncio.to_thread(llama.start_instance, alias)
                if not ok:
                    raise RuntimeError(detail or "通常CTXへの再起動に失敗")
                if not await _wait_llama_health(alias):
                    raise RuntimeError("通常CTXへ再ロード後、health確認がtimeout")
            else:
                llama.stop_instance(alias)
            restored.update({"restored": True, "restore_reason": "通常モデルCTXへ復元"})
            return restored
    except Exception as exc:
        restored["restore_reason"] = f"復元失敗: {type(exc).__name__}"
    return restored
