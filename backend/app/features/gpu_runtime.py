"""アドオンとして管理する GPU 推論ランタイム（llama.cpp / Lucebox）。

npm/pip/release-bundle と違い、これらは GitHub リリースの tar 配布物で、
導入先も `data/runtimes/<runtime>/` と決まっている。実体の取得・展開・
current 張り替えは各ランタイムのモジュールが持っているので、ここは
アドオン管理 UI（導入 / 更新 / 削除 / 状態）へつなぐ薄いアダプタに徹する。

「初期設定でのセットアップ」= install（このマシンで動くバックエンドを揃える）、
「更新」= update（導入済みバックエンドをまとめて最新リリースへ）に対応する。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("control_deck.features.gpu_runtime")

LLAMA_FEATURE = "llama-cpp"
LUCEBOX_FEATURE = "lucebox"
FEATURE_IDS = (LLAMA_FEATURE, LUCEBOX_FEATURE)


class GpuRuntimeError(RuntimeError):
    """利用者向けメッセージを持つ GPU ランタイム操作エラー。"""


def _llama():
    from app.models_mgmt import llama

    return llama


def _lucebox():
    from app.models_mgmt import lucebox

    return lucebox


def is_gpu_runtime(feature_id: str) -> bool:
    return feature_id in FEATURE_IDS


# ---- 状態（同期・ネットワーク非依存） ----


def _llama_status() -> dict:
    llama = _llama()
    state = llama.runtime_status()
    installed = bool(state["installed"])
    detected = state["detected_backends"]
    backends = [b for b in state["installed_backends"] if b in llama.SELECTABLE_BACKENDS]
    selectable = [b for b in llama.SELECTABLE_BACKENDS if detected.get(b)]
    return {
        "installed": installed,
        "managed": installed,
        "version": str(state.get("tag") or ""),
        "healthy": installed,
        "error": "" if installed or selectable else "このPCで使えるGPUバックエンドを検出できません",
        "available": bool(selectable or backends),
        "detail": {
            "runtime": "llama.cpp",
            "active_backend": str(state.get("backend") or ""),
            "installed_backends": backends,
            "selectable_backends": selectable,
            "backend_labels": state["backend_labels"],
            "rocm_series_major": state["rocm_series_major"],
            "host_rocm_version": state["host_rocm_version"],
            "warning": str(state.get("warning") or ""),
        },
    }


def _lucebox_status() -> dict:
    lucebox = _lucebox()
    state = lucebox.runtime_status()
    installed = bool(state["installed"])
    environment = state["environment"]
    return {
        "installed": installed,
        "managed": installed,
        "version": str(state.get("tag") or ""),
        "healthy": installed,
        "error": "" if installed or environment["available"] else environment["reason"],
        "available": bool(environment["available"] or installed),
        "detail": {
            "runtime": "lucebox",
            "track": state["track"],
            "track_label": state["track_label"],
            "tracks": state["tracks"],
            "recommended_track": state["recommended_track"],
            "default_track": state["default_track"],
            "upstream": state["upstream"],
            "installed_versions": state["installed_versions"],
            "gpu": environment,
            "warning": str(state.get("warning") or ""),
        },
    }


def status(feature_id: str) -> dict:
    if feature_id == LLAMA_FEATURE:
        return _llama_status()
    if feature_id == LUCEBOX_FEATURE:
        return _lucebox_status()
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")


# ---- リリース確認（非同期・GitHub 参照） ----


async def release_status(feature_id: str) -> dict:
    """更新の有無を返す。GitHub 側の失敗は error として返し、例外にしない。"""
    if feature_id == LLAMA_FEATURE:
        return {"feature_id": feature_id, **await _llama().available_update()}
    if feature_id == LUCEBOX_FEATURE:
        return {"feature_id": feature_id, **await _lucebox().available_update()}
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")


# ---- 導入・更新・削除 ----


async def install(feature_id: str, job: Any = None, *, options: dict | None = None) -> dict:
    """初期セットアップ。このPCで動く構成を既定値のまま一発で揃える。"""
    options = options or {}
    if feature_id == LLAMA_FEATURE:
        return await _install_llama(job, options)
    if feature_id == LUCEBOX_FEATURE:
        return await _install_lucebox(job, options)
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")


async def _install_llama(job: Any, options: dict) -> dict:
    llama = _llama()
    detected = llama.detect_backends()
    requested = [b for b in options.get("backends", []) if b in llama.SELECTABLE_BACKENDS]
    targets = requested or [b for b in llama.SELECTABLE_BACKENDS if detected.get(b)]
    if not targets:
        raise GpuRuntimeError(
            "ROCm / Vulkan のいずれも検出できませんでした。GPUドライバの導入状況を確認してください"
        )
    tag = await llama.latest_tag()
    results = []
    for backend in targets:
        results.append(await llama.install_stream(job, backend, tag))
    # ROCm が使えるなら ROCm を current にする（ROCm 10 統一構成の既定）。
    preferred = "rocm" if "rocm" in targets else targets[0]
    llama.switch_backend(preferred, tag)
    warning = llama.backend_warning(preferred)
    return {
        "runtime": "llama.cpp", "version": tag, "backends": targets,
        "active_backend": preferred, "warning": warning,
        "sha256": next((r["sha256"] for r in results if r["backend"] == preferred), ""),
    }


async def _install_lucebox(job: Any, options: dict) -> dict:
    lucebox = _lucebox()
    environment = lucebox.detect()
    if not environment["available"] and not options.get("force"):
        raise GpuRuntimeError(environment["reason"] or "このPCではLuceboxを利用できません")
    track = str(options.get("track") or lucebox.DEFAULT_TRACK)
    if track not in lucebox.TRACKS:
        raise GpuRuntimeError(f"未知のトラックです: {track}")
    try:
        result = await lucebox.install_stream(job, track=track)
    except lucebox.LuceboxError as exc:
        raise GpuRuntimeError(str(exc)) from exc
    return {"runtime": "lucebox", **result}


async def update(feature_id: str, job: Any = None) -> dict:
    """導入済み構成を最新リリースへ更新する。構成（backend/track）は変えない。"""
    if feature_id == LLAMA_FEATURE:
        llama = _llama()
        if not llama.is_installed():
            raise GpuRuntimeError("Control Deck が導入した llama.cpp がありません")
        try:
            return {"runtime": "llama.cpp", **await llama.update_stream(job)}
        except RuntimeError as exc:
            raise GpuRuntimeError(str(exc)) from exc
    if feature_id == LUCEBOX_FEATURE:
        lucebox = _lucebox()
        try:
            return {"runtime": "lucebox", **await lucebox.update_stream(job)}
        except lucebox.LuceboxError as exc:
            raise GpuRuntimeError(str(exc)) from exc
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")


def uninstall(feature_id: str) -> dict:
    """導入物を削除する。モデルファイルと登録済みのモデル設定は残す。"""
    if feature_id == LUCEBOX_FEATURE:
        return _lucebox().uninstall()
    if feature_id == LLAMA_FEATURE:
        # llama.cpp は Ollama 非利用時の主ランタイムで、消すと登録済みモデルが
        # すべて起動できなくなる。アドオン画面からの一括削除は用意しない。
        raise GpuRuntimeError(
            "llama.cpp はモデル管理画面から扱います。アドオンからの削除には対応していません"
        )
    raise GpuRuntimeError(f"未知のGPUランタイムです: {feature_id}")
