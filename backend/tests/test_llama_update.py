"""llama.cpp の最新リリース解決と更新（ROCm 10 統一）。"""
import asyncio

import pytest

from app.models_mgmt import gpu_release, llama


def test_tag_ordering_is_numeric_not_lexicographic():
    """b9544 < b10001 < b10687。文字列比較だと逆転して「更新なし」と誤判定する。"""
    tags = ["llama-gpu-b10001", "llama-gpu-b9544", "llama-gpu-b10687"]
    assert sorted(tags, key=llama._tag_sort_key) == [
        "llama-gpu-b9544", "llama-gpu-b10001", "llama-gpu-b10687",
    ]


def test_available_update_compares_against_the_installed_tag(monkeypatch):
    monkeypatch.setattr(llama, "get_config", lambda: {"tag": "llama-gpu-b10001"})
    monkeypatch.setattr(llama, "installed_backends", lambda *_args: ["vulkan"])

    async def _release(_repo, *, tag="", use_cache=True):
        return {"tag": "llama-gpu-b10687", "published_at": "2026-08-30T01:16:56Z", "assets": []}

    monkeypatch.setattr(gpu_release, "fetch_release", _release)
    result = asyncio.run(llama.available_update())
    assert result["update_available"] is True
    assert result["latest_tag"] == "llama-gpu-b10687"

    async def _older(_repo, *, tag="", use_cache=True):
        return {"tag": "llama-gpu-b9544", "published_at": "", "assets": []}

    monkeypatch.setattr(gpu_release, "fetch_release", _older)
    assert asyncio.run(llama.available_update())["update_available"] is False


def test_available_update_reports_network_failure_without_raising(monkeypatch):
    monkeypatch.setattr(llama, "get_config", lambda: {"tag": "llama-gpu-b10001"})
    monkeypatch.setattr(llama, "installed_backends", lambda *_args: ["vulkan"])

    async def _fail(_repo, *, tag="", use_cache=True):
        raise gpu_release.ReleaseError("GitHub APIの利用制限に達しました")

    monkeypatch.setattr(gpu_release, "fetch_release", _fail)
    result = asyncio.run(llama.available_update())
    assert result["update_available"] is False and result["error"]


def test_update_stream_refreshes_every_installed_backend(monkeypatch):
    """Vulkan と ROCm を両方入れている環境では、両方を同じタグへ揃える。

    片方だけ更新すると、バックエンド切り替えのたびに版が飛ぶ。
    """
    monkeypatch.setattr(llama, "get_config", lambda: {"tag": "llama-gpu-b10001", "backend": "vulkan"})
    monkeypatch.setattr(llama, "installed_backends", lambda tag="": ["rocm", "vulkan"])
    monkeypatch.setattr(llama, "installed_tags", lambda: ["llama-gpu-b10687", "llama-gpu-b10001"])
    monkeypatch.setattr(llama, "save_config", lambda patch: patch)
    monkeypatch.setattr(gpu_release, "prune_versions", lambda _root, _keep: [])
    monkeypatch.setattr(gpu_release, "invalidate_cache", lambda _repo: None)

    async def _latest():
        return "llama-gpu-b10687"

    async def _release(_repo, *, tag="", use_cache=True):
        return {"tag": tag, "published_at": "", "assets": [
            {"name": "llama-linux-amd-vulkan-b10687.tar.gz", "size": 1, "download_url": "u"},
            {"name": "llama-linux-rocm-r9700-b10687.tar.gz", "size": 1, "download_url": "u"},
            {"name": "llama-linux-cuda-b10687.tar.gz", "size": 1, "download_url": "u"},
        ]}

    async def _checksums(_assets):
        return {}

    fetched = []

    async def _fetch_backend(_job, backend, tag, _assets, _sums):
        fetched.append((backend, tag))
        return {"backend": backend, "tag": tag, "sha256": "sha256:x"}

    switched = []
    monkeypatch.setattr(llama, "latest_tag", _latest)
    monkeypatch.setattr(gpu_release, "fetch_release", _release)
    monkeypatch.setattr(gpu_release, "fetch_checksums", _checksums)
    monkeypatch.setattr(llama, "_fetch_backend", _fetch_backend)
    monkeypatch.setattr(llama, "switch_backend", lambda backend, tag: switched.append((backend, tag)))

    result = asyncio.run(llama.update_stream(None))
    assert fetched == [("rocm", "llama-gpu-b10687"), ("vulkan", "llama-gpu-b10687")]
    # current が指していた backend のまま最新版へ上げる。
    assert switched == [("vulkan", "llama-gpu-b10687")]
    assert result["previous_version"] == "llama-gpu-b10001" and result["updated"] is True


def test_update_stream_is_a_noop_when_already_latest(monkeypatch):
    monkeypatch.setattr(llama, "get_config", lambda: {"tag": "llama-gpu-b10687", "backend": "rocm"})
    monkeypatch.setattr(llama, "installed_backends", lambda tag="": ["rocm"])
    monkeypatch.setattr(llama, "backend_warning", lambda _backend: "")

    async def _latest():
        return "llama-gpu-b10687"

    monkeypatch.setattr(llama, "latest_tag", _latest)
    result = asyncio.run(llama.update_stream(None))
    assert result["updated"] is False and result["version"] == "llama-gpu-b10687"


def test_update_stream_requires_a_managed_install(monkeypatch):
    monkeypatch.setattr(llama, "get_config", lambda: {"tag": "", "backend": ""})
    with pytest.raises(RuntimeError):
        asyncio.run(llama.update_stream(None))


def test_rocm_builds_are_declared_as_rocm10(monkeypatch):
    assert llama.ROCM_SERIES_MAJOR == 10
    assert "ROCm 10" in llama.BACKEND_LABELS["rocm"]
    monkeypatch.setattr(llama, "host_rocm_version", lambda: "10.0.0")
    assert llama.backend_warning("rocm") == ""
    assert llama.backend_warning("vulkan") == ""
    monkeypatch.setattr(llama, "host_rocm_version", lambda: "7.2.1")
    warning = llama.backend_warning("rocm")
    assert "7.2.1" in warning and "Vulkan" in warning
