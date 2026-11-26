"""Web 収集エンジン W1（共通の取得・抽出・SSRF）。

目的:
- 内部のアドレス（ループバック・プライベート・リンクローカル＝クラウドのメタデータ・CGNAT・IPv4 射影の IPv6）へ接続しないこと。
- DNS の答えに内部のアドレスが 1 つでも混ざれば拒むこと。リダイレクトの先も各段で検査すること。
- 接続は**検査した IP にそのまま**行うこと（検査の後で別に名前解決しない。DNS rebinding の対策）。Host は元の名前のまま。
- 失敗を status で区別すること（security_rejected / blocked / captcha / too_large / http_error）。
- Trafilatura が無くても、見出し・段落の区切りを残して本文を取り出すこと（以前は 1 行に連結）。
- Deep Research の `_page_text` と Workflow の `web.scrape` が同じ安全な取得を使うこと。

根拠: docs/research-web-collector.md §1・§3、docs/PLAN-web-collector.md W1（2026-11-21 利用者「検索基盤の強化」）。
外部へは出ない（名前の解決は差し替え、相手はローカルの試験サーバー）。
"""
from __future__ import annotations

import asyncio
import http.server
import threading

import pytest

from app.web_collector import egress, extract, fetch

ARTICLE = """<html><head><title>ゲート駆動の損失</title><link rel="canonical" href="/a"></head><body>
<nav>メニュー ホーム 製品</nav>
<article><h1>ゲート駆動の損失を減らす</h1><p>窒化ガリウムのスイッチング素子は速く切り替わる。</p>
<h2>方法</h2><p>ゲート抵抗を小さくし、負バイアスで誤点弧を防ぐ。</p>
<table><tr><th>条件</th><th>損失</th></tr><tr><td>従来</td><td>10 W</td></tr></table>
<a href="https://example.com/next">次へ</a></article><footer>著作権</footer></body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict = {}
    seen_hosts: list = []

    def do_GET(self):  # noqa: N802
        self.seen_hosts.append(self.headers.get("Host"))
        status, headers, body = self.routes.get(self.path, (404, {}, b"not found"))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _Handler.routes = {}
    _Handler.seen_hosts = []
    yield httpd.server_address[1]
    httpd.shutdown()


def _policy(port: int, **names) -> egress.Policy:
    """試験サーバー（127.0.0.1）だけを公開とみなす。名前の解決は差し替え（外へ出ない）。"""
    return egress.Policy(ports=frozenset({port}), resolver_override={k.replace("_", "."): v for k, v in names.items()},
                         allow_networks=("127.0.0.1/32",))


def test_internal_addresses_are_not_public():
    for address in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.1", "169.254.169.254", "100.64.0.1",
                    "::1", "fe80::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "0.0.0.0", "224.0.0.1", "198.18.0.1"):
        assert not egress.is_public(address), address
    for address in ("8.8.8.8", "2001:4860:4860::8888", "133.11.0.1"):
        assert egress.is_public(address), address


def test_dns_answers_mixing_an_internal_address_are_rejected():
    policy = egress.Policy(resolver_override={"mixed.test": ["8.8.8.8", "10.0.0.1"]})
    with pytest.raises(egress.EgressRejected, match="内部のアドレス"):
        asyncio.run(egress.resolve_public("mixed.test", 443, policy))
    with pytest.raises(egress.EgressRejected, match="ポート"):
        asyncio.run(egress.resolve_public("example.com", 22, egress.DEFAULT_POLICY))


def test_collect_refuses_internal_targets_without_connecting():
    for url in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "file:///etc/passwd",
                "http://10.1.2.3/admin"):
        page = asyncio.run(fetch.collect(url))
        assert page["status"] == "security_rejected", (url, page)


def test_connection_is_pinned_to_the_checked_address_and_keeps_the_host_name(server):
    _Handler.routes = {"/a": (200, {"Content-Type": "text/html; charset=utf-8"}, ARTICLE.encode())}
    # site.test は実際の DNS に無い。差し替えた答え（検査済み）の IP にそのまま接続した場合だけ取れる
    page = asyncio.run(fetch.collect(f"http://site.test:{server}/a", policy=_policy(server, site_test=["127.0.0.1"])))
    assert page["status"] == "success", page
    assert _Handler.seen_hosts[-1] == f"site.test:{server}"
    assert "窒化ガリウム" in page["markdown"] and page["title"] == "ゲート駆動の損失"
    assert page["canonical_url"].endswith("/a") and page["content_sha256"]


def test_redirect_to_an_internal_host_is_checked_at_each_hop(server):
    _Handler.routes = {"/r": (302, {"Location": f"http://intranet.test:{server}/secret"}, b""),
                       "/secret": (200, {"Content-Type": "text/html"}, b"<p>secret</p>")}
    policy = _policy(server, site_test=["127.0.0.1"], intranet_test=["10.0.0.7"])
    page = asyncio.run(fetch.collect(f"http://site.test:{server}/r", policy=policy))
    assert page["status"] == "security_rejected" and "10.0.0.7" in page["error"]
    assert "/secret" not in [h for h in _Handler.seen_hosts if h and "intranet" in h]


def test_failures_are_classified(server):
    _Handler.routes = {"/forbidden": (403, {"Content-Type": "text/html"}, b"<p>no</p>"),
                       "/captcha": (403, {"Content-Type": "text/html"}, b"<div class='g-recaptcha'>Are you a robot?</div>"),
                       "/big": (200, {"Content-Type": "text/html"}, b"x" * 5000),
                       "/zip": (200, {"Content-Type": "application/zip"}, b"PK")}
    policy = _policy(server, site_test=["127.0.0.1"])
    run = lambda path, **kw: asyncio.run(fetch.collect(f"http://site.test:{server}{path}", policy=policy, **kw))  # noqa: E731
    assert run("/forbidden")["status"] == "blocked"
    assert run("/captcha")["status"] == "captcha"
    assert run("/big", max_bytes=1000)["status"] == "too_large"
    assert run("/zip")["status"] == "unsupported"
    assert run("/missing")["status"] == "http_error"


def test_fallback_extraction_keeps_headings_and_paragraphs(monkeypatch):
    monkeypatch.setattr(extract, "worker_python", lambda: None)
    out = asyncio.run(extract.extract(ARTICLE, "https://example.com/a"))
    text = out["markdown"]
    assert "# ゲート駆動の損失を減らす" in text and "## 方法" in text
    assert "メニュー" not in text and "著作権" not in text
    assert text.index("窒化ガリウム") < text.index("ゲート抵抗") and "\n" in text
    assert out["links"][0]["url"] == "https://example.com/next" and out["extractor"] == "beautifulsoup"
    assert "trafilatura_not_installed" in out["warnings"]


def test_japanese_legacy_encodings_are_decoded():
    body = "<html><head><meta charset=Shift_JIS></head><body><p>特許の調査</p></body></html>".encode("cp932")
    assert "特許の調査" in fetch._decode(body, "text/html")
    assert "特許" in fetch._decode("<p>特許</p>".encode("euc-jp"), "text/html; charset=EUC-JP")


def test_deep_research_and_workflow_scrape_share_the_guarded_fetch(server, monkeypatch):
    from app.workflows import chat_router, nodes

    assert asyncio.run(chat_router._page_text("http://127.0.0.1/")) == ""
    with pytest.raises(nodes.NodeError, match="内部のアドレス|公開"):
        asyncio.run(nodes.node_scrape({"url": "http://169.254.169.254/latest/meta-data/"}, {}))
    _Handler.routes = {"/a": (200, {"Content-Type": "text/html; charset=utf-8"}, ARTICLE.encode())}
    policy = _policy(server, site_test=["127.0.0.1"])
    monkeypatch.setattr(egress, "DEFAULT_POLICY", policy)
    monkeypatch.setattr(fetch.egress, "DEFAULT_POLICY", policy)
    text = asyncio.run(chat_router._page_text(f"http://site.test:{server}/a", 3500))
    assert "窒化ガリウム" in text and "メニュー" not in text
    out = asyncio.run(nodes.node_scrape({"url": f"http://site.test:{server}/a", "selector": "h2"}, {}))
    assert out["first"] == "方法" and out["status_code"] == 200
    out = asyncio.run(nodes.node_scrape({"url": f"http://site.test:{server}/a"}, {}))
    assert "窒化ガリウム" in out["text"] and "窒化ガリウム" in out["markdown"]


@pytest.mark.skipif(not __import__("os").environ.get("CONTROL_DECK_TEST_WEB_EXTRACT_PYTHON"),
                    reason="Trafilatura の入った venv を CONTROL_DECK_TEST_WEB_EXTRACT_PYTHON で渡したときだけ（CI は外へ出ない）")
def test_trafilatura_worker_extracts_markdown_in_a_separate_process(monkeypatch):
    """本物の Trafilatura を専用 venv の子プロセスで動かす。表・見出しを Markdown で残し、メニュー・フッターを落とす。
    短すぎる抽出は網羅寄りでやり直す。worker は使い回し、壊れたら起こし直す。"""
    import os
    from pathlib import Path

    python = Path(os.environ["CONTROL_DECK_TEST_WEB_EXTRACT_PYTHON"])
    monkeypatch.setattr(extract, "worker_python", lambda: python)

    async def run():
        first = await extract.extract(ARTICLE, "https://example.com/a")
        second = await extract.extract("<html><body><div id=app></div><p>短い</p></body></html>", "https://example.com/b")
        await extract._worker.stop()                       # 壊れた・殺された後も次の要求で起こし直す
        third = await extract.extract(ARTICLE, "https://example.com/a", "documentation")
        version = await extract.worker_version(python)
        await extract.shutdown()
        return first, second, third, version

    first, second, third, version = asyncio.run(run())
    assert first["extractor"].startswith("trafilatura 2.") and version.startswith("2.")
    md = first["markdown"]
    assert "窒化ガリウム" in md and "メニュー" not in md and "著作権" not in md
    assert "|" in md and "10 W" in md                    # 表は Markdown の表で残る
    assert first["title"].startswith("ゲート駆動の損失") and first["links"][0]["url"] == "https://example.com/next"
    assert "短い" in second["markdown"]
    assert "窒化ガリウム" in third["markdown"]


def test_knowledge_and_patent_url_import_use_the_body_not_the_whole_page(server, monkeypatch):
    """ナレッジ・Patent の URL 取り込みと Workflow のページ解析（scrape_tools）も共通の安全な取得を使い、
    取り込む文字はページ全体ではなく本文（メニュー・フッターを除く）にする。以前は内部のアドレスにも届き、全体の文字を入れていた。"""
    from app.workflows import scrape_tools as st

    with pytest.raises(st.ScrapeError):
        asyncio.run(st.fetch("http://169.254.169.254/latest/meta-data/"))
    _Handler.routes = {"/a": (200, {"Content-Type": "text/html; charset=utf-8"}, ARTICLE.encode())}
    policy = _policy(server, site_test=["127.0.0.1"])
    monkeypatch.setattr(egress, "DEFAULT_POLICY", policy)
    monkeypatch.setattr(extract, "worker_python", lambda: None)
    text, title, final = asyncio.run(st.fetch_document(f"http://site.test:{server}/a"))
    assert "窒化ガリウム" in text and "メニュー" not in text and "著作権" not in text
    assert title == "ゲート駆動の損失" and final.endswith("/a")
    html, status, _ = asyncio.run(st.fetch(f"http://site.test:{server}/a"))
    assert status == 200 and "<nav>" in html                           # ページ解析には HTML 全体を返す


def test_bibliographic_meta_tags_are_kept_for_patents_and_papers():
    """Google Patents・論文のページは出願人・発明者・DOI を本文ではなく meta（DC.* / citation_*）に持つ。拾って本文の先頭に置く。
    根拠: 2026-11-21 本番の評価（特開2026-116114 の Google Patents で、出願人・発明者が本文に無く meta にだけあった）。"""
    html = ('<html><head><meta name="DC.type" content="patent"><meta name="DC.title" content="半導体装置 ">'
            '<meta name="citation_patent_application_number" content="JP:2025100480A">'
            '<meta name="citation_patent_publication_number" content="JP:2026116114:A">'
            '<meta name="DC.date" content="2025-06-16"><meta name="DC.date" content="2026-07-09">'
            '<meta name="DC.contributor" content="一貴 島田"><meta name="DC.contributor" content="Renesas Electronics Corp">'
            '<meta property="og:site_name" content="Google Patents"></head><body><p>本文</p></body></html>')
    data = fetch.biblio(html)
    assert data["出願人・権利者（法人）"] == ["Renesas Electronics Corp"] and data["発明者・著者"] == ["一貴 島田"]
    assert data["日付"] == ["2025-06-16", "2026-07-09"] and data["公開番号"] == ["JP:2026116114:A"]
    block = fetch.biblio_block(data)
    assert block.startswith("## 書誌（JP2026116114A 半導体装置）") and "出願人・権利者（法人）: Renesas Electronics Corp" in block
    assert "JP2026116114A" in block and "JP2025100480A" in block            # 詰めた番号でも見つかる
    us = fetch.biblio('<title>US6285999B1 - Method for node ranking - Google Patents</title><meta name="DC.type" content="patent">'
                      '<meta name="DC.contributor" content="Lawrence Page"><meta name="DC.contributor" content="Leland Stanford Junior University">')
    assert us["出願人・権利者（法人）"] == ["Leland Stanford Junior University"] and us["発明者・著者"] == ["Lawrence Page"]
    assert us["公開番号"] == ["US6285999B1"] and fetch.biblio_block(us).startswith("## 書誌（US6285999B1")


def test_web_documents_are_split_by_headings_without_changing_the_collection(monkeypatch):
    """Web から取り込む文書は見出しで分ける（2026-11-21 本番の評価で検索の当たり 19→23/26）。コレクションの設定は変えない。"""
    from app.workflows import rag

    async def fake_embed(texts, *a, **k):
        import numpy as np
        return [np.ones(3, dtype=np.float32) for _ in texts]

    monkeypatch.setattr(rag, "embed", fake_embed)
    text = "# 題\n\n## 方法\n\nゲート抵抗を小さくする。\n\n## 効果\n\n損失が減る。"
    asyncio.run(rag.add_document("web-md-test", text, "https://example.com/a", strategy="markdown"))
    rows = rag.chunk_rows("web-md-test")
    assert any(r["text"].startswith("[題 > 効果]") for r in rows)
    assert rag.get_config("web-md-test")["strategy"] == "recursive"



def test_structured_table_preserves_merged_headers_and_cells():
    """目的・根拠: W4（PLAN-web-collector §7）。測定条件の列を失わず結合セルを複製しない。"""
    from app.web_collector.html_tables import extract_tables

    html = ('<table><caption>Benchmark</caption><tr><th rowspan="2" scope="rowgroup">GPU</th>'
            '<th colspan="2">Speed</th></tr><tr><th>Decode</th><th>Prefill</th></tr>'
            '<tr><td>R9700</td><td>60</td><td>420</td></tr></table>')
    tables, warnings = extract_tables(html)
    assert not warnings and len(tables) == 1
    table = tables[0]
    assert (table["caption"], table["rows"], table["cols"]) == ("Benchmark", 3, 3)
    assert [(c["row"], c["col"], c["rowspan"], c["colspan"]) for c in table["cells"][:4]] == [
        (0, 0, 2, 1), (0, 1, 1, 2), (1, 1, 1, 1), (1, 2, 1, 1)]
    assert table["cells"][-2]["text"] == "60"
    assert extract_tables('<table><tr><td>layout</td></tr></table>')[0] == []
    assert extract_tables('<table><tr><th rowspan="0">x</th></tr></table>')[0] == []


def test_collect_returns_table_metadata_without_changing_body(server, monkeypatch):
    """目的: W4a は既存の共通取得結果に表を加算し、Markdown本文を維持する。"""
    monkeypatch.setattr(extract, "worker_python", lambda: None)
    _Handler.routes = {"/a": (200, {"Content-Type": "text/html; charset=utf-8"}, ARTICLE.encode())}
    out = asyncio.run(fetch.collect(f"http://site.test:{server}/a", policy=_policy(server, site_test=["127.0.0.1"])))
    assert out["status"] == "success" and "窒化ガリウム" in out["markdown"]
    assert len(out["tables"]) == 1
    assert out["tables"][0]["cells"][-1]["text"] == "10 W"
