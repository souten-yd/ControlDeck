"""GPU runtime add-on adapter. Strata remains strictly opt-in."""
from __future__ import annotations

from typing import Any

LLAMA_FEATURE = "llama-cpp"
STRATA_FEATURE = "strata"
FEATURE_IDS = (LLAMA_FEATURE, STRATA_FEATURE)


class GpuRuntimeError(RuntimeError):
    """Friendly error for GPU runtime operations."""


def _llama():
    from app.models_mgmt import llama
    return llama


def _strata():
    from app.models_mgmt import strata
    return strata


def is_gpu_runtime(feature_id: str) -> bool:
    return feature_id in FEATURE_IDS


def _llama_status() -> dict:
    llama = _llama()
    state = llama.runtime_status()
    installed = bool(state["installed"])
    detected = state["detected_backends"]
    backends = [b for b in state["installed_backends"] if b in llama.SELECTABLE_BACKENDS]
    selectable = [b for b in llama.SELECTABLE_BACKENDS if detected.get(b)]
    return {
        "installed": installed, "managed": installed, "version": str(state.get("tag") or ""),
        "healthy": installed, "error": "" if installed or selectable else "利用できるGPUバックエンドがありません",
        "available": bool(selectable or backends),
        "detail": {
            "runtime": "llama.cpp", "active_backend": str(state.get("backend") or ""),
            "installed_backends": backends, "selectable_backends": selectable,
            "backend_labels": state["backend_labels"],
            "rocm_series_major": state["rocm_series_major"],
            "host_rocm_version": state["host_rocm_version"],
            "warning": str(state.get("warning") or ""),
        },
    }


def _strata_status() -> dict:
    state = _strata().runtime_status()
    return {
        "installed": state["installed"], "managed": state["installed"],
        "version": state["version"], "healthy": state["installed"],
        "error": state["reason"], "available": state["available"],
        "detail": {
            "runtime": "strata", "cli_path": state["cli_path"],
            "models_dir": state["models_dir"], "warning": state["warning"],
        },
    }


def status(feature_id: str) -> dict:
    if feature_id == LLAMA_FEATURE:
        return _llama_status()
    if feature_id == STRATA_FEATURE:
        return _strata_status()
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")


async def release_status(feature_id: str) -> dict:
    if feature_id == LLAMA_FEATURE:
        return {"feature_id": feature_id, **await _llama().available_update()}
    if feature_id == STRATA_FEATURE:
        return {"feature_id": feature_id, **await _strata().available_update()}
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")


async def install(feature_id: str, job: Any = None, *, options: dict | None = None) -> dict:
    options = options or {}
    if feature_id == LLAMA_FEATURE:
        return await _install_llama(job, options)
    if feature_id == STRATA_FEATURE:
        try:
            return {"runtime": "strata", **await _strata().install_stream(job)}
        except _strata().StrataError as exc:
            raise GpuRuntimeError(str(exc)) from exc
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")


async def _install_llama(job: Any, options: dict) -> dict:
    llama = _llama()
    detected = llama.detect_backends()
    requested = [b for b in options.get("backends", []) if b in llama.SELECTABLE_BACKENDS]
    targets = requested or [b for b in llama.SELECTABLE_BACKENDS if detected.get(b)]
    if not targets:
        raise GpuRuntimeError("ROCm / Vulkanのいずれも検出できませんでした")
    tag = await llama.latest_tag()
    results = []
    for backend in targets:
        results.append(await llama.install_stream(job, backend, tag))
    preferred = "rocm" if "rocm" in targets else targets[0]
    llama.switch_backend(preferred, tag)
    return {
        "runtime": "llama.cpp", "version": tag, "backends": targets,
        "active_backend": preferred, "warning": llama.backend_warning(preferred),
        "sha256": next((r["sha256"] for r in results if r["backend"] == preferred), ""),
    }


async def update(feature_id: str, job: Any = None) -> dict:
    if feature_id == LLAMA_FEATURE:
        llama = _llama()
        if not llama.is_installed():
            raise GpuRuntimeError("ControlDeckが導入したllama.cppがありません")
        try:
            return {"runtime": "llama.cpp", **await llama.update_stream(job)}
        except RuntimeError as exc:
            raise GpuRuntimeError(str(exc)) from exc
    if feature_id == STRATA_FEATURE:
        try:
            return {"runtime": "strata", **await _strata().update_stream(job)}
        except _strata().StrataError as exc:
            raise GpuRuntimeError(str(exc)) from exc
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")


def uninstall(feature_id: str) -> dict:
    if feature_id == STRATA_FEATURE:
        try:
            return _strata().uninstall()
        except _strata().StrataError as exc:
            raise GpuRuntimeError(str(exc)) from exc
    if feature_id == LLAMA_FEATURE:
        raise GpuRuntimeError("llama.cppはモデル管理画面から扱います。アドオン削除には対応しません")
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")
