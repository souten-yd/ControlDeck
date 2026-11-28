"""HuggingFace からの GGUF 取得。

目的（Nexarch #19 で追加）: 量子化ごとにサブフォルダが分かれた repo でも、選んだ量子化の
分割全片だけを列挙・取得し、repo 全体の他量子化を落とさないこと。取得後の登録は既存の
5 ランタイムの保存 API へ渡すこと。根拠: docs/PLAN-model-catalog.md「目録候補」の
Unsloth / ISTA-DASLab / Maya の GGUF は Q2_0/ や UD-Q4_K_XL/ などのサブフォルダに分割片が
置かれており、旧 list_model_files（最上位だけ）では 1 片も見つからなかった。
充分な取得・停止・再開の回帰は test_jobs_persistence.py が担う（ここでは偽 HF のみ）。

目的（レビュー #25 / 2026-11-28 で追加）: HF の API が報告した大きさより多い応答を
受け取っても書き込まず、秘密の会話ではファイル一覧の取得が 500 ではなく 403 で
断られること。根拠: 報告値を超える応答でディスクを埋められることと、
ensure_external_allowed の ExternalResearchBlocked が HfError でないため 500 に
なっていたこと。
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
        {"type": "file", "path": "README.md", "size": 4},          # 中身（b"DATA"）と同じ大きさ。足りないと完成にしない
        {"type": "file", "path": ".hidden", "size": 5},
        {"type": "file", "path": "model-00001.safetensors", "size": 4},
        {"type": "file", "path": "sub/inner.safetensors", "size": 300},
    ]
    monkeypatch.setattr(hf.httpx, "AsyncClient",
                        lambda **kwargs: _tree_and_stream_client(entries, [b"DATA"])())
    job = _DownloadJob()

    result = asyncio.run(hf.download(job, "owner/name", [], fetch_sizes=True))
    saved = sorted(path.rsplit("/", 1)[-1] for path in result["files"])
    assert saved == ["README.md", "model-00001.safetensors"]   # 隠しファイル・サブフォルダは扱わない
    # 開始時に読んだ合計（4 + 4）を、進み具合の分母に使う。
    assert any(args[2] == 8 for args, _ in job.progress if len(args) > 2), job.progress


def test_part_is_kept_on_stop_and_removed_on_cancel(tmp_path, monkeypatch):
    """目的: 途中のファイル（.part）を、停止・サーバー停止では残し、取り消しでは消すこと。

    根拠: 利用者の要望（2026-11-28）で停止と取り消しの意味を分けた。停止で .part を消すと
    再開のときに最初から取り直しになる（docs/PLAN-model-catalog.md「決めた意味」）。
    """
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    # 「サーバー停止中」は .part を残す側に倒れる。ほかの試験が残した印に左右されないよう外す（全体実行でだけ落ちた）
    from app.jobs import service as jobs_service
    monkeypatch.setattr(jobs_service, "is_shutting_down", lambda: False)
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
                        lambda **kwargs: _tree_and_stream_client(
                            [{**item, "size": 4} if item["path"].endswith(".gguf") else item for item in _quantized_tree()],
                            [b"GGUF"])())   # 大きさは中身（b"GGUF"）と同じ。足りないと完成にしない
    job = _DownloadJob()
    result = asyncio.run(hf.download(job, "owner/name", [], fetch_sizes=True,
                                     quantization="Q2_0"))
    saved = [path.rsplit("/", 1)[-1] for path in result["files"]]
    assert saved == ["model-Q2_0-00001-of-00002.gguf", "model-Q2_0-00002-of-00002.gguf"]
    assert not any("IQ2_XS" in name for name in saved)
    assert any(args[2] == 8 for args, _ in job.progress if len(args) > 2), job.progress


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

    first = saved.with_name("model-00001-of-00002.gguf")
    second = saved.with_name("model-00002-of-00002.gguf")
    first.write_bytes(b"GGUF")
    second.write_bytes(b"GGUF")

    assert hf._register_dispatch({"runtime": "llama", "alias": "a"}, [str(saved)]) == "a"
    assert hf._register_dispatch({"runtime": "radiance", "alias": "b"}, [str(saved)]) == "b"
    assert hf._register_dispatch({"runtime": "sly-radiance", "alias": "c"}, [str(saved)]) == "c"
    assert hf._register_dispatch(
        {"runtime": "strata", "alias": "d", "family": "qwen", "model": "q2_0"},
        [str(second), str(first)],   # 渡る順番が入れ替わっても 00001 を選ぶ
    ) == "d"
    assert [item[0] for item in called] == ["llama", "radiance", "sly", "strata"]
    assert all("port" in item[2] for item in called[1:])      # 3 つとも port は必須
    assert called[1][2]["model_path"] == str(saved)          # .rad はファイルのまま
    assert called[3][2]["model_path"] == str(first)          # Strata は最初の分割片
    assert called[3][2]["family"] == "qwen"
    with pytest.raises(hf.HfError, match="対応していない"):
        hf._register_dispatch({"runtime": "maya", "alias": "m"}, [str(saved)])


def test_register_chooses_a_free_port_for_radiance_and_strata(tmp_path, monkeypatch):
    """目的: ポート未指定の取得後登録が、既存 API の必須 port を空きポートで埋めること。

    根拠: Radiance / Strata の save_instance は port が必須（既定なし）。旧 adapter は
    未指定のとき port を渡さず、数 GB の取得の直後に TypeError で失敗していた。
    """
    from app.models_mgmt import local_llm, radiance, strata_catalog, strata_gateway

    saved = tmp_path / "m" / "model-00001-of-00002.gguf"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"GGUF")
    monkeypatch.setattr(local_llm, "endpoint_ports", lambda: {8100})
    ports: list[int] = []
    monkeypatch.setattr(radiance, "save_instance", lambda alias, **kw: ports.append(kw["port"]))
    monkeypatch.setattr(strata_gateway, "save_instance", lambda alias, **kw: ports.append(kw["port"]))
    monkeypatch.setattr(strata_catalog, "is_valid", lambda family, model: True)

    hf._register_dispatch({"runtime": "radiance", "alias": "r"}, [str(saved)])
    hf._register_dispatch(
        {"runtime": "strata", "alias": "s", "family": "qwen", "model": "q2_0"}, [str(saved)])
    assert ports == [8101, 8101]


def test_shard_set_rejects_duplicate_index_and_mixed_folders():
    """目的: 分割片は「同じフォルダ・同じ stem・同じ total の 1..total」が揃うこと。

    根拠: リーダーの diff 確認（2026-11-28）。片数だけ見ると、index の重複や別フォルダの
    混在でも「揃った」と判定され、起動できない組み合わせを取得してしまう。
    """
    good = ["Q2_0/model-00001-of-00002.gguf", "Q2_0/model-00002-of-00002.gguf"]
    hf._ensure_complete_shards(good)                       # 正しい組は通る

    for bad in (
        ["Q2_0/model-00001-of-00002.gguf"] * 2,             # index 1 の重複
        ["Q2_0/model-00001-of-00002.gguf",                  # 別フォルダの混在
         "Q8_0/model-00002-of-00002.gguf"],
        ["Q2_0/model-00001-of-00002.gguf",                  # total が食い違う
         "Q2_0/model-00002-of-00003.gguf"],
    ):
        with pytest.raises(hf.HfError, match="分割"):
            hf._ensure_complete_shards(bad)


def test_quantization_does_not_pick_a_sibling_variant(monkeypatch):
    """目的: Maya-M を選んだときに Maya-M-Derisked まで拾わないこと。

    根拠: リーダーの diff 確認（2026-11-28）。同じ repo に別 variant のフォルダが並ぶと、
    部分一致で両方を取ってしまう（選択した量子化だけを取る約束に反する）。
    """
    import asyncio

    tree = [
        {"type": "file", "path": "Maya-M/GLM-Maya-M-00001-of-00002.gguf", "size": 10},
        {"type": "file", "path": "Maya-M/GLM-Maya-M-00002-of-00002.gguf", "size": 20},
        {"type": "file", "path": "Maya-M-Derisked/GLM-Derisked-00001-of-00002.gguf", "size": 30},
        {"type": "file", "path": "Maya-M-Derisked/GLM-Derisked-00002-of-00002.gguf", "size": 40},
    ]
    _tree(monkeypatch, tree)
    files = asyncio.run(hf.list_quantization_files("owner/name", quantization="Maya-M"))
    assert [item["path"] for item in files] == [
        "Maya-M/GLM-Maya-M-00001-of-00002.gguf",
        "Maya-M/GLM-Maya-M-00002-of-00002.gguf",
    ]
    other = asyncio.run(hf.list_quantization_files("owner/name", quantization="Maya-M-Derisked"))
    assert len(other) == 2 and all("Derisked" in item["path"] for item in other)


def test_invalid_existing_target_is_not_treated_as_downloaded(tmp_path, monkeypatch):
    """目的: symlink・大きさ違いの既存ファイルを「取得済み」として飛ばさないこと。

    根拠: 先行点検（2026-11-28）。壊れた残り物があったとき、そのまま使うと起動できない
    重みを登録してしまう。HF の tree から読んだ大きさと照合して取り直す。
    """
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    target = tmp_path / "owner--name" / "model.gguf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"OLD")                       # HF の大きさ 5 と違う

    monkeypatch.setattr(hf.httpx, "AsyncClient",
                        lambda **kwargs: _tree_and_stream_client(
                            [{"type": "file", "path": "model.gguf", "size": 5}], [b"FRESH"])())
    result = asyncio.run(hf.download(_DownloadJob(), "owner/name", ["model.gguf"],
                                     fetch_sizes=True))
    assert target.read_bytes() == b"FRESH", result

    link_source = tmp_path / "real.gguf"
    link_source.write_bytes(b"REAL")
    target.unlink()
    target.symlink_to(link_source)

    def no_client(**kwargs):
        raise AssertionError("不正な既存ファイルは取得済み扱いにしない")

    monkeypatch.setattr(hf.httpx, "AsyncClient", no_client)
    with pytest.raises(hf.HfError, match="保存先がファイルではありません"):
        asyncio.run(hf.download(_DownloadJob(), "owner/name", ["model.gguf"]))


def test_download_does_not_write_more_than_the_reported_size(tmp_path, monkeypatch):
    """目的: HF の API が報告した大きさより多い応答が来ても、その分は書き込まないこと。

    根拠: レビュー #25 の要修正 3 / 2026-11-28。破損・悪意のある CDN が大きな body を
    返すと、従来は無制限に .part へ書き続けてディスクを埋めていた。報告値を上限に
    打ち切り、余分は書かずに失敗させる（途中ファイルは残すが最大でも報告値）。
    """
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    monkeypatch.setattr(hf, "_ensure_free_space", lambda *args: None)
    entries = [{"type": "file", "path": "model.gguf", "size": 4}]
    monkeypatch.setattr(hf.httpx, "AsyncClient",
                        lambda **kwargs: _tree_and_stream_client(entries, [b"1234567890"])())

    with pytest.raises(hf.HfError, match="大きさ"):
        asyncio.run(hf.download(_DownloadJob(), "owner/name", ["model.gguf"], fetch_sizes=True))

    target = tmp_path / "owner--name" / "model.gguf"
    assert not target.exists()                      # 壊れた応答を完成ファイルにしない
    partial = target.with_suffix(target.suffix + ".part")
    assert partial.read_bytes() == b"1234"         # 報告値の 4 バイトだけ書く


def test_hf_file_endpoints_return_403_when_external_research_is_blocked(
        admin_client, monkeypatch):
    """目的: ファイル一覧（と検索）が ExternalResearchBlocked を 500 ではなく 403 に
    変換して返すこと。

    根拠: レビュー #25 の要修正 5 / 2026-11-28。hf.py の ensure_external_allowed は
    HfError ではないので、従来の `except hf.HfError` では捕捉されず 500 になっていた。
    秘密の会話の拒否は利用者へ理由が見える形（403）で返す。
    """
    from app.security.offline import ExternalResearchBlocked

    def blocked(what):
        raise ExternalResearchBlocked(f"外部への調査は止めています（秘密の会話）: {what}")

    monkeypatch.setattr(hf, "ensure_external_allowed", blocked)

    files = admin_client.get("/api/v1/models/hf/repos/org/name/files")
    assert files.status_code == 403, files.text
    assert "秘密" in files.json()["detail"]

    catalog_files = admin_client.get(
        "/api/v1/models/catalog/sly-radiance/entries/swift-heretic/files")
    assert catalog_files.status_code == 403, catalog_files.text
    assert "秘密" in catalog_files.json()["detail"]

    search = admin_client.get("/api/v1/models/hf/search", params={"q": "qwen"})
    assert search.status_code == 403, search.text


def test_quantization_filename_match_does_not_pick_other_quants_or_mmproj():
    """量子化の照合が別の量子化（Q4_K→Q4_K_M・Q4_0→Q4_0_4_4）と視覚の投影（mmproj）を拾わない。
    2026-11-28 Claude のレビュー: `_` を境界にしていたため、数 GB 余計に落とし、登録に使う本体が違うものになりえた。"""
    from app.models_mgmt.hf import _quantization_matches as m

    assert m("X-Q4_K.gguf", "Q4_K") and not m("X-Q4_K_M.gguf", "Q4_K")
    assert m("X-Q4_0.gguf", "Q4_0") and not m("X-Q4_0_4_4.gguf", "Q4_0")
    assert m("X-Q8_0.gguf", "Q8_0") and not m("mmproj-X-Q8_0.gguf", "Q8_0")
    assert m("X-Q4_K_M-00001-of-00003.gguf", "Q4_K_M") and m("X-Q4_K_M.gguf.part", "Q4_K_M")
    assert not m("X-IQ4_XS.gguf", "Q4_XS")
    assert m("UD-Q4_K_XL/X-UD-Q4_K_XL-00001-of-00002.gguf", "UD-Q4_K_XL")


def test_short_download_is_not_finished(tmp_path, monkeypatch):
    """受け取った大きさが HF の報告に届かないとき、完成にせず .part を残す（再開で続きを取る）。
    2026-11-28 Claude のレビュー: 接続が途中で切れても例外にならず、足りないファイルが完成として登録されえた。"""
    import asyncio

    from app.models_mgmt import libraries

    monkeypatch.setattr(libraries, "default_models_dir", lambda: tmp_path)
    monkeypatch.setattr(hf, "_ensure_free_space", lambda *args: None)
    tree = [{"type": "file", "path": "model.safetensors", "size": 10}]
    monkeypatch.setattr(hf.httpx, "AsyncClient", lambda **kwargs: _tree_and_stream_client(tree, [b"DATA"])())
    with pytest.raises(hf.HfError):
        asyncio.run(hf.download(_DownloadJob(), "owner/name", [], fetch_sizes=True))
    folder = tmp_path / "owner--name"
    assert not (folder / "model.safetensors").exists() and (folder / "model.safetensors.part").exists()
