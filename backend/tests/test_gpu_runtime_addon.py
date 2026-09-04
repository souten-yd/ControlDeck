"""アドオンとして管理するGPUランタイム（llama.cpp / Lucebox）。"""
import asyncio

import pytest

from app.features import gpu_runtime, registry


def test_catalog_registers_both_runtimes_from_the_expected_repos():
    assert {"llama-cpp", "lucebox"} <= registry.KNOWN_FEATURES
    assert registry.FEATURES["llama-cpp"]["repo"] == "souten-yd/llama-builder"
    assert registry.FEATURES["lucebox"]["repo"] == "souten-yd/AMDLucebox"
    assert registry.FEATURES["llama-cpp"]["kind"] == "gpu-runtime"


def test_status_reports_llama_backends_and_rocm_series(monkeypatch):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "runtime_status", lambda: {
        "installed": True, "tag": "llama-gpu-b10687", "backend": "rocm",
        "detected_backends": {"rocm": True, "vulkan": True, "cuda": False},
        "installed_backends": ["rocm", "vulkan"],
        "backend_labels": llama.BACKEND_LABELS,
        "rocm_series_major": 10, "host_rocm_version": "10.0.0", "warning": "",
    })
    state = gpu_runtime.status("llama-cpp")
    assert state["installed"] and state["version"] == "llama-gpu-b10687"
    assert state["detail"]["active_backend"] == "rocm"
    assert state["detail"]["installed_backends"] == ["rocm", "vulkan"]
    assert state["detail"]["rocm_series_major"] == 10


def test_status_surfaces_missing_gpu_for_lucebox(monkeypatch):
    from app.models_mgmt import lucebox

    monkeypatch.setattr(lucebox, "is_installed", lambda: False)
    monkeypatch.setattr(lucebox, "_gfx_targets", lambda: [110000])
    state = gpu_runtime.status("lucebox")
    assert state["installed"] is False
    assert state["available"] is False
    assert "gfx1201" in state["error"]


def test_install_llama_targets_detected_backends_and_prefers_rocm(monkeypatch):
    from app.models_mgmt import llama

    calls = []
    monkeypatch.setattr(llama, "detect_backends", lambda: {"rocm": True, "vulkan": True, "cuda": True})

    async def _latest():
        return "llama-gpu-b10687"

    async def _install(_job, backend, tag):
        calls.append((backend, tag))
        return {"backend": backend, "tag": tag, "sha256": f"sha256:{backend}"}

    monkeypatch.setattr(llama, "latest_tag", _latest)
    monkeypatch.setattr(llama, "install_stream", _install)
    monkeypatch.setattr(llama, "switch_backend", lambda backend, tag: {"backend": backend})
    monkeypatch.setattr(llama, "backend_warning", lambda _backend: "")

    result = asyncio.run(gpu_runtime.install("llama-cpp", None))
    # CUDA は llama.cpp の選択肢から外れている（Ollama を使う方針）。
    assert calls == [("rocm", "llama-gpu-b10687"), ("vulkan", "llama-gpu-b10687")]
    assert result["active_backend"] == "rocm"
    assert result["version"] == "llama-gpu-b10687"


def test_install_llama_fails_clearly_without_a_usable_backend(monkeypatch):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, "detect_backends", lambda: {"rocm": False, "vulkan": False, "cuda": True})
    with pytest.raises(gpu_runtime.GpuRuntimeError) as excinfo:
        asyncio.run(gpu_runtime.install("llama-cpp", None))
    assert "ROCm" in str(excinfo.value)


def test_install_lucebox_defaults_to_rocm10(monkeypatch):
    from app.models_mgmt import lucebox

    seen = {}
    monkeypatch.setattr(lucebox, "detect", lambda: {"available": True, "reason": ""})

    async def _install(_job, *, track):
        seen["track"] = track
        return {"tag": "lucebox-298031aa-r1", "track": track, "version": "lucebox-298031aa-r1"}

    monkeypatch.setattr(lucebox, "install_stream", _install)
    asyncio.run(gpu_runtime.install("lucebox", None))
    assert seen["track"] == "rocm10"
    # 明示指定があればそちらを使う（ホストが ROCm 7 系のときの退避先）。
    asyncio.run(gpu_runtime.install("lucebox", None, options={"track": "rocm7"}))
    assert seen["track"] == "rocm7"


def test_install_lucebox_refuses_unknown_track(monkeypatch):
    from app.models_mgmt import lucebox

    monkeypatch.setattr(lucebox, "detect", lambda: {"available": True, "reason": ""})
    with pytest.raises(gpu_runtime.GpuRuntimeError):
        asyncio.run(gpu_runtime.install("lucebox", None, options={"track": "rocm99"}))


def test_uninstall_protects_llama_cpp():
    """llama.cpp を消すと登録済みモデルが全部起動できなくなる。アドオンからは消させない。"""
    with pytest.raises(gpu_runtime.GpuRuntimeError):
        gpu_runtime.uninstall("llama-cpp")


def test_features_api_lists_gpu_runtimes(admin_client):
    response = admin_client.get("/api/v1/features")
    assert response.status_code == 200
    listed = {item["id"]: item for item in response.json()}
    assert listed["lucebox"]["kind"] == "gpu-runtime"
    assert listed["lucebox"]["runtime"]["runtime"] == "lucebox"
    # route_gated ではないので、有効化の一手間を求めない。
    assert listed["lucebox"]["route_gated"] is False


def test_install_job_rejects_an_unknown_track(admin_client):
    response = admin_client.post("/api/v1/features/lucebox/install-jobs",
                                 json={"track": "rocm99"},
                                 headers={"X-Requested-With": "ControlDeck"})
    assert response.status_code == 422


def test_release_status_is_only_for_gpu_runtimes(admin_client):
    response = admin_client.get("/api/v1/features/opencode/release-status")
    assert response.status_code == 422
