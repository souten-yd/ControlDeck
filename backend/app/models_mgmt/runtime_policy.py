"""LLM runtime横断の選択・共通運用ポリシー。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import data_dir


class ChatDefaults(BaseModel):
    max_output_tokens: int = Field(default=2048, ge=64, le=131072)
    reasoning: Literal["off", "auto", "on"] = "off"
    timeout_seconds: int = Field(default=300, ge=10, le=1800)


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


async def environment() -> dict:
    from app.models_mgmt import llama, ollama

    policy = get_policy()
    detected = llama.detect_backends()
    installed = set(llama.installed_backends())
    ollama_status = await ollama.status()
    llama_status = llama.runtime_status()
    health = await llama.health() if llama_status["installed"] else {"ok": False}
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
            "running": bool(health.get("ok")) and llama_status.get("backend") == backend,
        })
    return {
        "platform": "Linux", "gpu": "AMD" if detected.get("rocm") else "GPU",
        "runtimes": runtimes, "policy": policy.model_dump(),
    }


async def apply_selection(policy: RuntimePolicy) -> None:
    """排他モードのruntime切替。サービス削除はせず、競合モデルだけ解放する。"""
    from app.models_mgmt import llama, ollama

    if policy.selected_runtime == "llama.cpp":
        if policy.selected_backend not in llama.installed_backends():
            raise ValueError(f"llama.cpp / {policy.selected_backend} は未導入です")
        current = llama.get_config().get("backend")
        if current != policy.selected_backend:
            was_running = (await llama.health()).get("ok", False)
            if was_running:
                llama.stop_instance()
            llama.switch_backend(policy.selected_backend)
            if was_running:
                ok, detail = llama.start_instance()
                if not ok:
                    raise RuntimeError(detail or "backend切替後の起動に失敗しました")
        if policy.coexistence == "exclusive":
            for model in await ollama.running_models():
                name = str(model.get("name") or model.get("model") or "")
                if name:
                    await ollama.unload(name)
    elif policy.coexistence == "exclusive":
        if (await llama.health()).get("ok", False):
            llama.stop_instance()
