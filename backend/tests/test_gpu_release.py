"""GPUランタイム配布物の取得・検証・展開（ネットワーク不要な部分）。"""
import io
import tarfile

import pytest

from app.models_mgmt import gpu_release


def _targz(path, entries):
    with tarfile.open(path, "w:gz") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_extract_keeps_layout_and_finds_binary(tmp_path):
    archive = tmp_path / "runtime.tar.gz"
    _targz(archive, {
        "lucebox-r9700/server/build/dflash_server": b"ELF",
        "lucebox-r9700/BUILD_INFO.json": b"{}",
    })
    extracted = gpu_release.extract_archive(archive, tmp_path / "extracted")
    binary = gpu_release.find_binary(extracted, "dflash_server")
    assert binary is not None
    # 配布物の相対レイアウト（server/build/）を崩さない。同梱ライブラリの解決に要る。
    assert binary.relative_to(extracted).as_posix() == "lucebox-r9700/server/build/dflash_server"


def test_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    _targz(archive, {"../escaped": b"x"})
    with pytest.raises(gpu_release.ReleaseError):
        gpu_release.extract_archive(archive, tmp_path / "extracted")
    assert not (tmp_path / "escaped").exists()


def test_extract_rejects_links_pointing_outside(tmp_path):
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("evil")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(gpu_release.ReleaseError):
        gpu_release.extract_archive(archive, tmp_path / "extracted")

    relative = tmp_path / "relative.tar.gz"
    with tarfile.open(relative, "w:gz") as tar:
        info = tarfile.TarInfo("pkg/lib/escape.so")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../../../etc/passwd"
        tar.addfile(info)
    with pytest.raises(gpu_release.ReleaseError):
        gpu_release.extract_archive(relative, tmp_path / "extracted2")


def test_extract_keeps_soname_symlinks(tmp_path):
    """共有ライブラリの soname リンクは配布物に必ず含まれる。壊さず展開する。"""
    archive = tmp_path / "libs.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"ELF"
        real = tarfile.TarInfo("pkg/lib/libggml-hip.so.1")
        real.size = len(payload)
        tar.addfile(real, io.BytesIO(payload))
        link = tarfile.TarInfo("pkg/lib/libggml-hip.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "libggml-hip.so.1"
        tar.addfile(link)
    extracted = gpu_release.extract_archive(archive, tmp_path / "extracted")
    linked = extracted / "pkg" / "lib" / "libggml-hip.so"
    assert linked.is_symlink() and linked.resolve().name == "libggml-hip.so.1"


def test_extract_rejects_unknown_format(tmp_path):
    archive = tmp_path / "runtime.zip"
    archive.write_bytes(b"PK")
    with pytest.raises(gpu_release.ReleaseError):
        gpu_release.extract_archive(archive, tmp_path / "extracted")


def test_download_verifies_published_checksum(tmp_path, monkeypatch):
    import hashlib

    payload = b"runtime-bytes"
    digest = hashlib.sha256(payload).hexdigest()

    class _Response:
        status_code = 200

        async def aiter_bytes(self, _size):
            yield payload

    class _Stream:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, *_args):
            return False

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return _Stream()

    monkeypatch.setattr(gpu_release.httpx, "AsyncClient", _Client)
    asset = {"name": "a.tar.gz", "size": len(payload), "download_url": "https://example/a"}
    import asyncio

    target = tmp_path / "a.tar.gz"
    assert asyncio.run(gpu_release.download_asset(None, asset, target, expected_sha256=digest)) == digest
    with pytest.raises(gpu_release.ReleaseError):
        asyncio.run(gpu_release.download_asset(None, asset, target, expected_sha256="0" * 64))
    # 不一致のファイルは残さない。次の導入で「検証済み」と誤認させないため。
    assert not target.exists()


def test_checksums_parse_only_wellformed_lines(monkeypatch):
    """SHA256SUMS は壊れた行を含みうる。読める行だけを採り、例外にしない。"""
    import asyncio

    digest = "deadbeef" * 8
    body = f"{digest}  lucebox.tar.zst\ngarbage line\nzz  short.tar\n{digest} *star.tar\n"

    class _Response:
        status_code = 200
        text = body

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url):
            return _Response()

    monkeypatch.setattr(gpu_release.httpx, "AsyncClient", _Client)
    assets = [{"name": "SHA256SUMS", "size": len(body), "download_url": "https://example/s"}]
    assert asyncio.run(gpu_release.fetch_checksums(assets)) == {
        "lucebox.tar.zst": digest, "star.tar": digest,
    }
    # SHA256SUMS が無いリリースでも失敗させない（照合をスキップする）。
    assert asyncio.run(gpu_release.fetch_checksums([])) == {}


def test_prune_versions_keeps_requested(tmp_path):
    for name in ("v1", "v2", "v3"):
        (tmp_path / name).mkdir()
    (tmp_path / "current").symlink_to(tmp_path / "v3", target_is_directory=True)
    removed = gpu_release.prune_versions(tmp_path, ["v3", "current"])
    assert removed == ["v1", "v2"]
    assert (tmp_path / "v3").is_dir() and (tmp_path / "current").is_symlink()


def test_relink_swaps_target_in_place(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    link = tmp_path / "current"
    gpu_release.relink(link, tmp_path / "a")
    assert link.resolve() == (tmp_path / "a").resolve()
    gpu_release.relink(link, tmp_path / "b")
    assert link.resolve() == (tmp_path / "b").resolve()
