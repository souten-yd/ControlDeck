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
