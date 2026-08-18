"""HuggingFace からの GGUF 取得。"""
from __future__ import annotations

import json

import pytest

from app.models_mgmt import hf


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload or []

    def json(self):
        return self._payload


def _tree(monkeypatch, entries, status=200):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            return _Resp(status, entries)

    monkeypatch.setattr(hf.httpx, "AsyncClient", lambda **kwargs: _Client())


def test_repo_files_lists_only_gguf_variants(monkeypatch):
    _tree(monkeypatch, [
        {"type": "directory", "path": "imgs", "size": 0},
        {"type": "file", "path": "README.md", "size": 100},
        {"type": "file", "path": "model-Q4_K_M.gguf", "size": 4_000_000_000},
        {"type": "file", "path": "model-Q8_0.gguf", "size": 8_000_000_000},
    ])
    variants = pytest.run = None
    import asyncio

    variants = asyncio.run(hf.list_repo_files("owner/name"))
    assert [v["name"] for v in variants] == ["model-Q4_K_M.gguf", "model-Q8_0.gguf"]
    assert variants[0]["size"] == 4_000_000_000
    assert variants[0]["sharded"] is False


def test_sharded_gguf_is_grouped_with_total_size(monkeypatch):
    """分割GGUFは1つだけ落としても使えないので、まとめて扱う。"""
    import asyncio

    _tree(monkeypatch, [
        {"type": "file", "path": "big-00001-of-00003.gguf", "size": 10},
        {"type": "file", "path": "big-00002-of-00003.gguf", "size": 20},
        {"type": "file", "path": "big-00003-of-00003.gguf", "size": 30},
    ])
    variants = asyncio.run(hf.list_repo_files("owner/name"))
    assert len(variants) == 1
    group = variants[0]
    assert group["sharded"] is True
    assert group["size"] == 60          # 合計サイズで判断させる
    assert len(group["files"]) == 3
    assert group["complete"] is True


def test_incomplete_shard_set_is_flagged(monkeypatch):
    import asyncio

    _tree(monkeypatch, [
        {"type": "file", "path": "big-00001-of-00003.gguf", "size": 10},
        {"type": "file", "path": "big-00002-of-00003.gguf", "size": 20},
    ])
    assert asyncio.run(hf.list_repo_files("owner/name"))[0]["complete"] is False


def test_repo_name_must_be_owner_slash_name():
    import asyncio

    with pytest.raises(hf.HfError, match="owner/name"):
        asyncio.run(hf.list_repo_files("../etc/passwd"))


def test_gated_repo_reports_token_requirement(monkeypatch):
    import asyncio

    _tree(monkeypatch, [], status=401)
    with pytest.raises(hf.HfError, match="トークン"):
        asyncio.run(hf.list_repo_files("owner/name"))


def test_free_space_is_checked_before_downloading(tmp_path):
    """入らないものを落とし始めない（途中で詰まると中途半端に残る）。"""
    import shutil as real_shutil

    total, used, free = real_shutil.disk_usage(tmp_path)
    with pytest.raises(hf.HfError, match="空き容量"):
        hf._ensure_free_space(tmp_path, free + 10 * 1024**3)
    # 収まる要求は通る
    hf._ensure_free_space(tmp_path, 1024)


def test_download_saves_under_repo_directory_and_can_register(tmp_path, monkeypatch):
    import asyncio

    from app.models_mgmt import libraries, llama

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)

    class _Job:
        def set_progress(self, *a, **k):
            pass

        def log(self, *a, **k):
            pass

    class _Stream:
        def __init__(self, payload):
            self.status_code, self._payload = 200, payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def aiter_bytes(self, size):
            yield self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, headers=None):
            return _Stream(b"GGUF-DATA")

    monkeypatch.setattr(hf.httpx, "AsyncClient", lambda **kwargs: _Client())
    saved_patch = {}
    monkeypatch.setattr(llama, "save_instance",
                        lambda alias, patch: saved_patch.update({"alias": alias, **patch}))

    result = asyncio.run(hf.download(
        _Job(), "owner/name", ["model-Q4.gguf"],
        register={"alias": "my-model", "role": "llm", "port": 8099},
    ))
    target = tmp_path / "owner--name" / "model-Q4.gguf"
    assert target.is_file()
    assert target.read_bytes() == b"GGUF-DATA"
    assert result["registered"] == "my-model"
    assert saved_patch["model_path"] == str(target)
    assert saved_patch["port"] == 8099
    # .part は残さない
    assert not list(target.parent.glob("*.part"))


def test_download_skips_files_already_present(tmp_path, monkeypatch):
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    existing = tmp_path / "owner--name" / "model.gguf"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"already-here")

    class _Job:
        def set_progress(self, *a, **k):
            pass

        def log(self, *a, **k):
            pass

    def _boom(**kwargs):
        raise AssertionError("既にあるファイルを再取得してはいけない")

    monkeypatch.setattr(hf.httpx, "AsyncClient", _boom)
    result = asyncio.run(hf.download(_Job(), "owner/name", ["model.gguf"]))
    assert result["files"] == [str(existing)]
    assert existing.read_bytes() == b"already-here"


def test_presets_find_files_across_libraries(tmp_path, monkeypatch):
    """既定ライブラリが変わっても、別の場所にある既存GGUFを再取得しない。"""
    from app.models_mgmt import libraries, role_presets

    other = tmp_path / "old-location"
    other.mkdir()
    (other / "bge-m3-FP16.gguf").write_bytes(b"GGUF")
    monkeypatch.setattr(libraries, "list_libraries", lambda: [
        {"id": "new", "mounted": True, "path": str(tmp_path / "empty")},
        {"id": "old", "mounted": True, "path": str(other)},
    ])
    found = role_presets._existing_file(role_presets.ROLE_PRESETS["bge-m3"])
    assert found == other / "bge-m3-FP16.gguf"
