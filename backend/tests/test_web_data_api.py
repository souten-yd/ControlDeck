"""公開データ API の直接利用（data_api）。

目的:
- URL の形で GitHub（repo・blob・issue・PR）・Wikipedia・OpenAlex（works・authors）・arXiv（abs）だけを判定し、それ以外は対象にしないこと。
- 固定の応答の見本から本文へ変換できること（README・issue のコメントの上位と件数の上限・Wikipedia の見出しと脚注の除去・
  OpenAlex の抄録の再構成・arXiv の Atom）。
- 上限（コメント件数・応答の大きさ）、失敗の扱い（403/429 は止めて理由を返し HTML へ戻らない／404 は HTML へ戻る／
  内部アドレスは止める／XML の宣言は読まない）。
- `fetch.collect` が前段で data_api を使い（fetch_method="data_api"）、`data_api=False` や対象外の URL では使わないこと。
根拠: docs/HANDOFF-web-collector.md「W2 で最初にやること」（GitHub README の見出し・OpenAlex の 403・Wikipedia REST）。外へは通信しない。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.web_collector import data_api as da
from app.web_collector import egress, fetch


def run(coro):
    return asyncio.run(coro)


def fake(routes: dict, calls: list | None = None):
    async def get(url, headers, max_bytes):
        if calls is not None:
            calls.append(url)
        for prefix, value in routes.items():
            if url.startswith(prefix):
                if isinstance(value, da.Resp):
                    return value
                return da.Resp(200, value if isinstance(value, bytes) else json.dumps(value).encode(), {})
        return da.Resp(404, b"{}", {})
    return get


@pytest.mark.parametrize("url,kind", [
    ("https://github.com/ggml-org/llama.cpp", "github"),
    ("https://github.com/ggml-org/llama.cpp/issues/7", "github"),
    ("https://github.com/o/r/pull/3", "github"),
    ("https://github.com/o/r/blob/main/docs/a.md", "github"),
    ("https://openalex.org/works/W123456", "openalex_work"),
    ("https://openalex.org/A5023888391", "openalex_author"),
])
def test_match_supported(url, kind):
    assert da.match(url)[0] == kind


@pytest.mark.parametrize("url", [
    "https://github.com/orgs/x/people", "https://github.com/ggml-org", "https://github.com/o/r/actions", "https://github.com/search?q=a",
    "https://ja.wikipedia.org/wiki/%E7%AA%92%E5%8C%96", "https://arxiv.org/abs/2401.01234v2", "https://evil.test/wiki/x", "https://wikipedia.org.evil.test/wiki/x",
    "https://openalex.org/works?filter=a", "https://arxiv.org/pdf/2401.01234", "https://example.com/", "ftp://github.com/o/r",
    "https://github.com:8443/o/r",
])
def test_match_rejects_others(url):
    assert da.match(url) is None


def test_github_repo_readme_keeps_headings_and_meta():
    get = fake({"https://api.github.com/repos/o/r/readme": da.Resp(200, "# 題\n## 使い方\n[docs](docs/a.md)".encode()),
                "https://api.github.com/repos/o/r": {"full_name": "o/r", "description": "説明", "stargazers_count": 5, "language": "C++",
                                                     "pushed_at": "2026-11-20T00:00:00Z", "default_branch": "main"}})
    doc = run(da.github_document("o", "r", "", get))
    assert "★5" in doc.text and "更新 2026-11-20" in doc.text and "## 使い方" in doc.text
    assert doc.links == [{"text": "docs", "url": "https://github.com/o/r/blob/main/docs/a.md"}]


def test_github_issue_comments_are_top_and_capped():
    comments = [{"body": f"c{i}", "user": {"login": "u"}, "reactions": {"total_count": i}} for i in range(40)]
    get = fake({"https://api.github.com/repos/o/r/issues/7/comments": comments,
                "https://api.github.com/repos/o/r/issues/7": {"state": "open", "comments": 40, "title": "t", "body": "本文", "labels": [{"name": "bug"}],
                                                                "pull_request": {}}})
    doc = run(da.github_document("o", "r", "issues/7", get))
    assert doc.kind == "github_pull" and doc.text.count("\n- (") == da.COMMENTS_MAX and "c39" in doc.text and "c0 " not in doc.text and "全 40 件" in doc.text


def test_github_blob_binary_and_truncation():
    get = fake({"https://raw.githubusercontent.com/o/r/main/a.bin": da.Resp(200, b"\x00\x01"),
                "https://raw.githubusercontent.com/o/r/main/a.md": da.Resp(200, b"# a", truncated=True)})
    with pytest.raises(da.DataApiError):
        run(da.github_document("o", "r", "blob/main/a.bin", get))
    doc = run(da.github_document("o", "r", "blob/main/a.md", get))
    assert doc.text == "# a" and doc.warnings


def test_rate_limit_and_not_found_are_distinguished():
    limited = fake({"https://api.github.com/": da.Resp(403, b"{}", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "123"})})
    got = run(da.fetch("https://github.com/o/r", get=limited))
    assert got["status"] == "blocked" and "残り 0" in got["error"] and "回避しません" in got["error"]
    assert run(da.fetch("https://github.com/o/r", get=fake({})))["status"] == "fallback"
    assert run(da.fetch("https://example.com/", get=fake({}))) is None


WORK = {"id": "https://openalex.org/W1", "display_name": "GaN の高速スイッチング", "publication_year": 2025, "doi": "https://doi.org/10.1/x",
        "cited_by_count": 12, "authorships": [{"author": {"display_name": f"著者{i}"}} for i in range(40)],
        "abstract_inverted_index": {"GaN": [0, 4], "devices": [1], "switch": [2], "fast.": [3], "Loss": [5]}}


def test_openalex_work_rebuilds_abstract():
    assert da.abstract_from_index(WORK["abstract_inverted_index"]) == "GaN devices switch fast. GaN Loss"
    assert da.abstract_from_index(None) == "" and da.abstract_from_index({"a": "bad"}) == ""
    doc = run(da.openalex_document("openalex_work", "W1", fake({"https://api.openalex.org/works/W1": WORK})))
    assert "GaN devices switch fast." in doc.text and "被引用数: 12" in doc.text and "全 40 名" in doc.text and "## 抄録" in doc.text


def test_openalex_author_survives_works_failure_but_stops_on_429():
    author = {"id": "https://openalex.org/A1", "display_name": "山田", "works_count": 3, "cited_by_count": 9, "summary_stats": {"h_index": 2}}
    doc = run(da.openalex_document("openalex_author", "A1", fake({"https://api.openalex.org/authors/A1": author})))
    assert "h指数: 2" in doc.text
    limited = fake({"https://api.openalex.org/authors/A1": author, "https://api.openalex.org/works?": da.Resp(429, b"{}")})
    with pytest.raises(da.DataApiError) as err:
        run(da.openalex_document("openalex_author", "A1", limited))
    assert err.value.stop


def test_http_get_rejects_internal_address_and_foreign_redirect():
    policy = egress.Policy(resolver_override={"api.github.com": ["10.0.0.5"]})
    with pytest.raises(da.DataApiError) as err:
        run(da.http_get("https://api.github.com/repos/o/r", {}, 1000, policy=policy))
    assert err.value.stop and err.value.status == "security_rejected"
    assert da._allowed_host("api.github.com") and not da._allowed_host("evil.test") and not da._allowed_host("ja.wikipedia.org")


def test_collect_uses_data_api_before_html(monkeypatch):
    calls = []

    async def fake_fetch(url, **kw):
        calls.append(url)
        return {"status": "success", "kind": "github", "title": "o/r README", "markdown": "# o/r\n## 使い方", "links": [], "language": "",
                "warnings": [], "metadata": {}, "extractor": "data_api:github_repo", "http_status": 200}

    monkeypatch.setattr(da, "fetch", fake_fetch)
    page = run(fetch.collect("https://github.com/o/r"))
    assert page["status"] == "success" and page["fetch_method"] == "data_api" and page["markdown"].startswith("# o/r")
    run(fetch.collect("https://127.0.0.1:1/x", data_api=False))
    assert calls == ["https://github.com/o/r"]


def test_collect_stops_on_blocked_and_never_falls_back(monkeypatch):
    async def blocked(url, **kw):
        return {"status": "blocked", "error": "GitHub の API が断りました HTTP 403", "http_status": 403, "kind": "github"}

    monkeypatch.setattr(da, "fetch", blocked)
    page = run(fetch.collect("https://github.com/o/r"))
    assert page["status"] == "blocked" and page["fetch_method"] == "data_api" and "403" in page["error"] and not page["markdown"]
