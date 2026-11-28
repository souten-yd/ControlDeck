"""HuggingFace からの GGUF 取得。

目的（Nexarch #19 で追加）: 量子化ごとにサブフォルダが分かれた repo でも、選んだ量子化の
分割全片だけを列挙・取得し、repo 全体の他量子化を落とさないこと。取得後の登録は既存の
5 ランタイムの保存 API へ渡すこと。根拠: docs/PLAN-model-catalog.md「目録候補」の
Unsloth / ISTA-DASLab / Maya の GGUF は Q2_0/ や UD-Q4_K_XL/ などのサブフォルダに分割片が
置かれており、旧 list_model_files（最上位だけ）では 1 片も見つからなかった。
充分な取得・停止・再開の回帰は test_jobs_persistence.py が担う（ここでは偽 HF のみ）。
"""
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


def test_sly_register_avoids_used_ports_and_retries_only_on_conflict(tmp_path, monkeypatch):
    """目的: 取得後の Sly 登録が使用中のポートを避けて空きを選び、再試行は衝突の印だけで行うこと。

    根拠: run4 の再レビュー（minor）「ポート衝突の再試行を "ポート" の部分一致で判定して
    いた」。別名の重複など、ポート以外の失敗を空回りさせない。
    """
    from app.models_mgmt import local_llm, sly_radiance

    saved = tmp_path / "m" / "model.safetensors"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"x")
    monkeypatch.setattr(local_llm, "endpoint_ports", lambda: {8100, 8101})
    used: list[int] = []
    monkeypatch.setattr(sly_radiance, "save_instance",
                        lambda alias, *, model_path, port, **fields: used.append(port))

    assert hf._register_sly_radiance({"alias": "sly-x"}, [str(saved)]) == "sly-x"
    assert used == [8102]

    def raise_alias_duplicate(alias, *, model_path, port, **fields):
        raise sly_radiance.SlyRadianceError("同じ別名は既に登録されています")

    monkeypatch.setattr(sly_radiance, "save_instance", raise_alias_duplicate)
    with pytest.raises(sly_radiance.SlyRadianceError, match="同じ別名"):
        hf._register_sly_radiance({"alias": "sly-x"}, [str(saved)])


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


class _DownloadJob:
    def __init__(self, *, pause_requested: bool = False):
        self.pause_requested = pause_requested
        self.progress: list[tuple] = []

    def set_progress(self, *args, **kwargs):
        self.progress.append((args, kwargs))

    def log(self, *args, **kwargs):
        pass


def _tree_and_stream_client(entries, chunks):
    """ファイル一覧（tree）と本体の取得を 1 つの偽クライアントで返す。chunks を出し切る。"""
    class _Response:
        def __init__(self, payload):
            self.status_code, self._payload = 200, payload
            self.headers: dict[str, str] = {}

        def json(self):
            return self._payload

    class _Stream:
        def __init__(self):
            self.status_code, self.headers = 200, {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def aiter_bytes(self, size):
            for chunk in chunks:
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            return _Response(entries)

        def stream(self, method, url, headers=None):
            return _Stream()

    return _Client


def test_empty_files_takes_every_top_level_file_and_reads_its_size_at_start(
        tmp_path, monkeypatch):
    """目的: 目録の行から始まる files=[] の取得が、repo の最上位ファイル全部を落とし、
    大きさを開始時に HF の API（同じ一覧）から読むこと。

    根拠: 2026-11-28 の目録の取得は repo id しか持たない（大きさを推測しない）。
    以前は空の files を「取得するものがない」と断っていた（hf.download の分岐の順序）。
    """
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    entries = [
        {"type": "directory", "path": "sub", "size": 0},
        {"type": "file", "path": "README.md", "size": 100},
        {"type": "file", "path": ".hidden", "size": 5},
        {"type": "file", "path": "model-00001.safetensors", "size": 200},
        {"type": "file", "path": "sub/inner.safetensors", "size": 300},
    ]
    monkeypatch.setattr(hf.httpx, "AsyncClient",
                        lambda **kwargs: _tree_and_stream_client(entries, [b"DATA"])())
    job = _DownloadJob()

    result = asyncio.run(hf.download(job, "owner/name", [], fetch_sizes=True))
    saved = sorted(path.rsplit("/", 1)[-1] for path in result["files"])
    assert saved == ["README.md", "model-00001.safetensors"]   # 隠しファイル・サブフォルダは扱わない
    # 開始時に読んだ合計（100 + 200）を、進み具合の分母に使う。
    assert any(args[2] == 300 for args, _ in job.progress if len(args) > 2), job.progress


def test_part_is_kept_on_stop_and_removed_on_cancel(tmp_path, monkeypatch):
    """目的: 途中のファイル（.part）を、停止・サーバー停止では残し、取り消しでは消すこと。

    根拠: 利用者の要望（2026-11-28）で停止と取り消しの意味を分けた。停止で .part を消すと
    再開のときに最初から取り直しになる（docs/PLAN-model-catalog.md「決めた意味」）。
    """
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    monkeypatch.setattr(hf, "_ensure_free_space", lambda *args: None)

    def client():
        return _tree_and_stream_client([], [b"PARTIAL", asyncio.CancelledError()])()

    monkeypatch.setattr(hf.httpx, "AsyncClient", lambda **kwargs: client())
    target = tmp_path / "owner--name" / "model.gguf"
    partial = target.with_suffix(target.suffix + ".part")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(hf.download(_DownloadJob(pause_requested=True), "owner/name", ["model.gguf"]))
    assert partial.is_file() and partial.read_bytes() == b"PARTIAL"

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(hf.download(_DownloadJob(pause_requested=False), "owner/name", ["model.gguf"]))
    assert not partial.exists()


def test_redirect_keeps_token_only_on_the_first_host(tmp_path, monkeypatch):
    """目的: 取得の redirect で、gated repo のトークン（Authorization）を CDN など別ホストへ
    送らないこと。https 以外の redirect 先は断ること。

    根拠: run4 のレビュー条件「redirect の SSRF と認証漏洩を試験する」。以前はトークンを
    クライアントの既定ヘッダにしていたので、redirect を追う各段（CDN）にも付いていた。
    """
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    monkeypatch.setattr(hf, "_ensure_free_space", lambda *args: None)
    monkeypatch.setattr(hf, "_headers", lambda: {"User-Agent": "t", "Authorization": "Bearer SECRET"})

    seen: list[tuple[str, dict]] = []

    class _Stream:
        def __init__(self, status, headers):
            self.status_code, self.headers = status, headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def aiter_bytes(self, size):
            yield b"DATA"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, headers=None):
            seen.append((url, dict(headers or {})))
            if url.startswith("https://huggingface.co/"):
                return _Stream(302, {"location": "https://cdn.example.com/model.gguf"})
            return _Stream(200, {})

    monkeypatch.setattr(hf.httpx, "AsyncClient", lambda **kwargs: _Client())

    asyncio.run(hf.download(_DownloadJob(), "owner/name", ["model.gguf"]))
    assert seen[0][0].startswith("https://huggingface.co/")
    assert seen[0][1].get("Authorization") == "Bearer SECRET"
    assert seen[1][0] == "https://cdn.example.com/model.gguf"
    assert "Authorization" not in seen[1][1]
    assert (tmp_path / "owner--name" / "model.gguf").read_bytes() == b"DATA"

    class _HttpRedirect(_Client):
        def stream(self, method, url, headers=None):
            return _Stream(302, {"location": "http://cdn.example.com/model.gguf"})

    monkeypatch.setattr(hf.httpx, "AsyncClient", lambda **kwargs: _HttpRedirect())
    with pytest.raises(hf.HfError):
        asyncio.run(hf.download(_DownloadJob(), "owner/name", ["other.gguf"]))


def test_download_rejects_bad_repo_revision_and_escaping_paths(tmp_path, monkeypatch):
    """目的: 取得の repo・revision・ファイル名の不正値（パスの逃げ出し・URL への細工）を、
    通信の前に断ること。

    根拠: run4 のレビュー条件「repo/revision/files の不正値を試験する」。ここを通すと、
    保存先の外へ書いたり別の URL を叩いたりする。
    """
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)

    def refused(repo, files, revision="main"):
        with pytest.raises(hf.HfError):
            asyncio.run(hf.download(_DownloadJob(), repo, files, revision=revision))

    refused("owner", ["a.gguf"])                 # owner/name でない
    refused("owner/name", ["../escape.gguf"])    # パスの逃げ出し
    refused("owner/name", ["/abs.gguf"])         # 絶対パス
    refused("owner/name", ["a.gguf"], revision="main/../other")
    refused("owner/name", ["a\\b.gguf"])         # 区切りの細工
    # URL へ補間すると要求先・保存名が変わる（クエリ・断片・エスケープ）。run4 のレビュー。
    refused("owner/name", ["a.gguf?download=1"])
    refused("owner/name", ["a.gguf#frag"])
    refused("owner/name", ["a%2f.gguf"])


# ---- サブフォルダの量子化と分割片（Nexarch #19） --------------------------------------------


def _quantized_tree():
    """2 つの量子化フォルダを持つ repo の偽 tree（大きさは HF から読んだ値として扱う）。"""
    return [
        {"type": "directory", "path": "Q2_0", "size": 0},
        {"type": "file", "path": "README.md", "size": 10},
        {"type": "file", "path": "Q2_0/model-Q2_0-00001-of-00002.gguf", "size": 100},
        {"type": "file", "path": "Q2_0/model-Q2_0-00002-of-00002.gguf", "size": 200},
        {"type": "file", "path": "IQ2_XS/model-IQ2_XS-00001-of-00002.gguf", "size": 300},
        {"type": "file", "path": "IQ2_XS/model-IQ2_XS-00002-of-00002.gguf", "size": 400},
    ]


def test_quantization_files_lists_all_shards_in_subdirectories(monkeypatch):
    """目的: サブフォルダの分割 GGUF を全片列挙し、他量子化を混ぜないこと。

    根拠: 依頼「HF はサブディレクトリの GGUF 分割全片を正しく列挙し選択した量子化だけ取得」。
    Q2_0 と IQ2_XS が部分一致しないことも確かめる（`Q2_0` が `IQ2_XS` に当たらない）。
    """
    import asyncio

    _tree(monkeypatch, _quantized_tree())
    files = asyncio.run(hf.list_quantization_files("owner/name", quantization="Q2_0"))
    assert [item["path"] for item in files] == [
        "Q2_0/model-Q2_0-00001-of-00002.gguf",
        "Q2_0/model-Q2_0-00002-of-00002.gguf",
    ]
    assert sum(item["size"] for item in files) == 300
    other = asyncio.run(hf.list_quantization_files("owner/name", quantization="IQ2_XS"))
    assert len(other) == 2 and all("IQ2_XS" in item["path"] for item in other)


def test_quantization_files_refuses_an_incomplete_shard_set(monkeypatch):
    """目的: 分割片が 1 片だけの量子化を「取得できる」と出さないこと。

    根拠: 分割 GGUF は 1 片だけでは起動できない（既存 test_incomplete_shard_set_is_flagged と
    同じ守りを、サブフォルダの量子化列挙にも効かせる。新しい別の試験ではない）。
    """
    import asyncio

    entries = [item for item in _quantized_tree()
               if not item["path"].endswith("00002-of-00002.gguf")]
    _tree(monkeypatch, entries)
    with pytest.raises(hf.HfError, match="分割"):
        asyncio.run(hf.list_quantization_files("owner/name", quantization="Q2_0"))


def test_download_quantization_takes_only_the_selected_subdirectory(tmp_path, monkeypatch):
    """目的: files=[] + 量子化指定で、その量子化の分割全片だけを取得すること。

    根拠: 目録の量子化行から始まる取得が repo 全体を落とさないこと。HF への通信は偽物で、
    ファイル名・大きさは tree API から読む（大きさを推測しない）。
    """
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    monkeypatch.setattr(hf, "_ensure_free_space", lambda *args: None)
    monkeypatch.setattr(hf.httpx, "AsyncClient",
                        lambda **kwargs: _tree_and_stream_client(_quantized_tree(), [b"GGUF"])())
    job = _DownloadJob()
    result = asyncio.run(hf.download(job, "owner/name", [], fetch_sizes=True,
                                     quantization="Q2_0"))
    saved = [path.rsplit("/", 1)[-1] for path in result["files"]]
    assert saved == ["model-Q2_0-00001-of-00002.gguf", "model-Q2_0-00002-of-00002.gguf"]
    assert not any("IQ2_XS" in name for name in saved)
    assert any(args[2] == 300 for args, _ in job.progress if len(args) > 2), job.progress


def test_download_refuses_files_from_another_quantization(tmp_path, monkeypatch):
    """目的: 明示指定された files に他量子化が混ざっていたら取得の前に断ること。

    根拠: 「repo 全体の他量子化を落とさない」。画面の選択と repo の不一致で別の重みを
    落とす前に止める（取得後の発見より無駄が少ない）。
    """
    import asyncio

    monkeypatch.setattr(hf, "ensure_external_allowed", lambda what: None)
    with pytest.raises(hf.HfError, match="属さない"):
        asyncio.run(hf.download(_DownloadJob(), "owner/name",
                                ["IQ2_XS/model-IQ2_XS-00001-of-00002.gguf"],
                                fetch_sizes=True, quantization="Q2_0"))


def test_register_dispatch_covers_all_five_runtimes(tmp_path, monkeypatch):
    """目的: 取得後の登録が既存の 5 ランタイムの保存 API へ正しく渡ること。

    根拠: 依頼「取得後既存登録へ渡す、既存の runtime 登録/起動互換は維持」。Maya は
    probe から登録するので受け付けない。Radiance は .rad ファイル、Strata は分割 GGUF の
    フォルダを渡す（Sly の safetensors と混ぜない）。
    """
    from app.models_mgmt import llama, radiance, sly_radiance, strata_catalog, strata_gateway

    saved = tmp_path / "owner--name" / "model.gguf"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"GGUF")
    called: list[tuple] = []

    monkeypatch.setattr(llama, "save_instance",
                        lambda alias, patch: called.append(("llama", alias, patch)))
    monkeypatch.setattr(radiance, "save_instance",
                        lambda alias, **kw: called.append(("radiance", alias, kw)))
    monkeypatch.setattr(sly_radiance, "save_instance",
                        lambda alias, **kw: called.append(("sly", alias, kw)))
    monkeypatch.setattr(strata_gateway, "save_instance",
                        lambda alias, **kw: called.append(("strata", alias, kw)))
    monkeypatch.setattr(strata_catalog, "is_valid", lambda family, model: True)

    assert hf._register_dispatch({"runtime": "llama", "alias": "a"}, [str(saved)]) == "a"
    assert hf._register_dispatch({"runtime": "radiance", "alias": "b"}, [str(saved)]) == "b"
    assert hf._register_dispatch({"runtime": "sly-radiance", "alias": "c"}, [str(saved)]) == "c"
    assert hf._register_dispatch(
        {"runtime": "strata", "alias": "d", "family": "qwen", "model": "q2_0"},
        [str(saved)],
    ) == "d"
    assert [item[0] for item in called] == ["llama", "radiance", "sly", "strata"]
    assert called[1][2]["model_path"] == str(saved)          # .rad はファイルのまま
    assert called[3][2]["model_path"] == str(saved.parent)   # Strata はフォルダ
    assert called[3][2]["family"] == "qwen"
    with pytest.raises(hf.HfError, match="対応していない"):
        hf._register_dispatch({"runtime": "maya", "alias": "m"}, [str(saved)])
