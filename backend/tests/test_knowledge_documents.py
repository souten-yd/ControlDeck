"""文書を単位にした RAG（書誌の関係・全文か骨格・規格・PDF・AI アシスタントの道具）。

目的:
- 特許・規格は文書を単位に探し、入るなら全文、入らなければ骨格（要約・請求項・適用範囲・引用規格）と該当箇所を
  元の順で渡すこと。reranker の上位 k 件で文書の中身を削らないこと。
- 公報番号・規格番号で書誌の関係（引用する・引用される・別の版）を結び、検索で 1 hop 広げること。
- 規格は条項で分割し、番号で引け、利用判定で本文の保存・AI 利用を止めること。旧版を現行にしないこと。
- PDF からページの文章・図のページの画像・原本を保存し、ページを locator に持つこと。
- AI アシスタントの道具は、cloud のモデルへ機密・AI 利用の許されない規格を渡さないこと。

根拠: 2026-11-21 利用者「特許・規格は書誌全体が関係する。上位何件に絞れない。コンテキストが十分なら全文を入れて回答」
「PDF から画像・文章・波形グラフ・表を抽出して記録し、体系的に引き出す規格検索」。docs/research-patent-standards-rag.md。
"""
from __future__ import annotations

import asyncio
import shutil
import zlib

import pytest

from app.config import data_dir
from tests.conftest import CSRF_HEADERS


@pytest.fixture(autouse=True)
def fresh():
    from app.knowledge import storage, store

    shutil.rmtree(data_dir() / "knowledge", ignore_errors=True)
    storage.config_path().unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()
    yield
    store._schema_ready.clear()


PATENT_A = ("【書誌事項】特開2026-116114\n【要約】【課題】スイッチング損失を減らす。\n【特許請求の範囲】\n【請求項１】\n"
            "スイッチング素子と、制御部と、を備えるインバータ。\n【請求項２】\n前記制御部がＰＷＭ制御する請求項１のインバータ。\n"
            "【発明の詳細な説明】\n【技術分野】電力変換。\n" + "実施例の説明。" * 400 + "\n先行技術: 特許第7123456号、IEC 61000-4-2:2008 に準拠。")
PATENT_B = "【書誌事項】特許第7123456号\n【特許請求の範囲】\n【請求項１】\nゲート駆動回路を備える電力変換装置。\n" + "従来例。" * 50
STANDARD = ("1 適用範囲\nこの規格は静電気放電イミュニティ試験について規定する。\n"
            "2 引用規格\nIEC 60050-161 は、この規格に引用されることによって、この規格の規定の一部を構成する。\n"
            "3 用語及び定義\n3.1 接触放電\n電極を供試装置に接触させて放電する方法。\n"
            "5 試験レベル\n接触放電の試験レベルは 2 kV、4 kV、6 kV、8 kV とする。\n")


def _patents():
    from app.knowledge import store

    store.create_collection("lit", shard="patents", kind="patent")
    a = store.add_document("lit", [store.NewChunk(PATENT_A[i:i + 600]) for i in range(0, len(PATENT_A), 600)],
                           title="特開2026-116114 インバータ", body=PATENT_A)
    b = store.add_document("lit", [store.NewChunk(PATENT_B)], title="特許第7123456号 電力変換装置", body=PATENT_B)
    return f"patents:{a['doc_id']}", f"patents:{b['doc_id']}"


def test_bibliographic_links_and_one_hop_expansion():
    from app.knowledge import documents

    a, b = _patents()
    related = documents.related_documents(a)
    assert {"ref": b, "relation": "cites"}.items() <= related[0].items()
    assert documents.related_documents(b)[0]["relation"] == "cited_by"
    ranked = documents.search_documents("スイッチング損失", names=["lit"], consumer="local", mode="fulltext")
    assert ranked[0]["ref"] == a and any(d["ref"] == b and d["relation"] == "cites" for d in ranked)
    exact = documents.search_documents("特許第7123456号", names=["lit"], consumer="local", mode="fulltext")
    assert exact[0]["ref"] == b and exact[0]["relation"] == "exact"


def test_whole_documents_when_they_fit_else_outline_with_all_claims():
    from app.knowledge import context, documents

    a, _ = _patents()
    ranked = documents.search_documents("インバータ 請求項", names=["lit"], consumer="local", mode="fulltext")
    full = context.build("q", ranked, budget_chars=200_000)
    assert full.mode == "full" and PATENT_A in full.text and full.sources[0]["label"] == "D1"
    small = context.build("q", ranked, budget_chars=3_000, max_docs=1)
    assert small.mode == "outline"
    assert "【請求項１】" in small.text and "【請求項２】" in small.text     # 請求項は削らない
    assert len(small.text) < 3_600


def test_cloud_consumers_get_no_confidential_or_prohibited_documents():
    from app.knowledge import documents, store

    _patents()
    store.create_collection("pub", shard="patents", kind="patent", sensitivity="public")
    store.add_document("pub", [store.NewChunk("インバータの公開公報")], title="公開")
    store.add_document("pub", [store.NewChunk("インバータの規格本文")], title="規格", ai_use="prohibited")
    cloud = documents.search_documents("インバータ", consumer="cloud", mode="fulltext")
    assert [d["title"] for d in cloud] == ["公開"]
    local = documents.search_documents("インバータ", consumer="local", mode="fulltext")
    assert "規格" not in [d["title"] for d in local] and len(local) >= 2
    human = documents.search_documents("インバータ", consumer="human", mode="fulltext")
    assert "規格" in [d["title"] for d in human]


def test_standards_are_split_by_clause_and_gated_by_license():
    from app.knowledge import documents, standards

    async def no_embed(chunks, collection):
        return "", False

    from app.knowledge import ingest
    ingest_embed = ingest._embed
    ingest._embed = no_embed
    try:
        allowed = asyncio.run(standards.register(designation="JIS C 61000-4-2", title="静電気放電イミュニティ試験",
                                                 edition="2012", instrument="written_permission", text=STANDARD,
                                                 permission_ref="JSA-AI-1", reviewer="owner", reviewed_on="2026-11-21",
                                                 granted_storage="shared_server", granted_ai_use="local_only"))
        bought = asyncio.run(standards.register(designation="IEC 61000-4-3:2020", title="放射イミュニティ",
                                                instrument="purchase", text="本文" * 100))
    finally:
        ingest._embed = ingest_embed
    assert allowed["license"]["storage"] == "shared_server" and allowed["fulltext_stored"]
    assert bought["license"]["ai_use"] == "prohibited" and not bought["fulltext_stored"]
    assert documents.document_text(bought["doc_ref"]) == ""                    # 購入した本文は持たない
    hits = standards.search("試験レベル")
    assert hits[0]["clauses"][0]["clause"] == "5"
    assert standards.search("JIS C 61000-4-2")[0]["exact"]
    assert "IEC 61000-4-3" in standards.search("IEC 61000-4-3")[0]["designation"]
    rows = {s["designation"]: s for s in standards.list_standards()}
    assert rows["IEC 61000-4-3:2020"]["storage"] == "personal_copy"
    outline = documents.document_outline(allowed["doc_ref"], [], limit=5000)
    assert "適用範囲" in outline and "引用規格" in outline


def test_new_edition_supersedes_the_current_one():
    from app.knowledge import standards

    for edition in ("2008", "2025"):
        asyncio.run(standards.register(designation=f"IEC 61000-4-2:{edition}", title="ESD", instrument="purchase"))
    status = {s["edition"]: s["status"] for s in standards.list_standards()}
    assert status == {"2008": "superseded", "2025": "current"}
    assert [r["edition"] for r in standards.search("IEC 61000-4-2")] == ["2025"]


def _pdf(pages: list[str]) -> bytes:
    """ASCII だけの最小の PDF（テスト用）。"""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    font = len(pages) * 2 + 3
    for i, text in enumerate(pages):
        page_no, content_no = 3 + i * 2, 4 + i * 2
        kids.append(f"{page_no} 0 R")
        lines = "".join(f"BT /F1 12 Tf 40 {760 - 18 * j} Td ({line}) Tj ET\n" for j, line in enumerate(text.split("\n")))
        stream = zlib.compress(lines.encode())
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font} 0 R >> >> "
                    f"/Contents {content_no} 0 R >>")
        objs.append((f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n", stream))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        if isinstance(obj, tuple):
            out += f"{n} 0 obj\n{obj[0]}".encode() + obj[1] + b"\nendstream\nendobj\n"
        else:
            out += f"{n} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="poppler-utils が無い")
def test_pdf_pages_figures_and_original_are_kept(tmp_path, monkeypatch):
    from app.knowledge import documents, ingest, pdf_ingest, store

    async def no_embed(chunks, collection):
        return "", False

    async def vlm(png, job=None):
        assert png.startswith(b"\x89PNG")
        return "Figure 1: block diagram of the inverter (inference)"

    monkeypatch.setattr(ingest, "_embed", no_embed)
    monkeypatch.setattr(pdf_ingest, "describe_page", vlm)
    path = tmp_path / "spec.pdf"
    path.write_bytes(_pdf(["1 Scope\nThis document specifies the inverter test with enough words to skip OCR here.",
                           "Figure 1 shows the block diagram\nTable 1 lists the voltage levels for the inverter test."]))
    store.create_collection("docs")
    result = asyncio.run(ingest.ingest_pdf("docs", path, title="Spec"))
    assert result["pdf"]["page_count"] == 2 and result["pdf"]["figure_pages"] == [2]
    assert result["pdf"]["figures_described"] == [2]
    doc = documents.get_document(f"general:{result['doc_id']}")
    roles = sorted(a["role"] for a in doc["assets"])
    assert roles == ["original", "page"]
    assert store.blob_path(next(a["sha256"] for a in doc["assets"] if a["role"] == "original")).read_bytes() == path.read_bytes()
    hits = store.search(["docs"], "block diagram", mode="fulltext")
    assert any(h["locator"].get("kind") == "figure" and h["locator"]["page"] == 2 for h in hits)
    assert any(h["locator"].get("page") == 1 for h in store.search(["docs"], "Scope inverter", mode="fulltext"))
    assert doc["annotations"][0]["basis"] == "model_inference"


def test_assistant_tool_respects_the_consumer(monkeypatch):
    from app.knowledge import context, store
    from app.workflows import chat_tools

    _patents()

    async def no_vector(question):
        return None, ""

    monkeypatch.setattr(context, "query_vector", no_vector)
    call = {"name": chat_tools.SEARCH_KNOWLEDGE, "arguments": '{"query": "スイッチング損失"}'}
    text, record = asyncio.run(chat_tools.run_knowledge(call, consumer="local", bound=[], max_chars=200_000))
    assert record["mode"] == "full" and "【請求項１】" in text and "[D1]" in text
    text, record = asyncio.run(chat_tools.run_knowledge(call, consumer="cloud", bound=[], max_chars=200_000))
    assert record["count"] == 0     # 機密の公報は cloud へ出さない
    assert store.has_shared_collections()


def test_knowledge_api_find_document_and_asset(admin_client, monkeypatch):
    from app.knowledge import context, store

    async def no_vector(question):
        return None, ""

    monkeypatch.setattr(context, "query_vector", no_vector)
    a, b = _patents()
    sha = store.put_blob("patents", b"\x89PNG\r\n\x1a\nfig", "image/png", role="figure")
    conn = store.connect("patents")
    conn.execute("INSERT INTO document_assets(doc_id, sha256, role, ord, meta) VALUES(?,?,?,?,?)",
                 (int(a.split(":")[1]), sha, "figure", 0, "{}"))
    conn.commit()
    conn.close()
    r = admin_client.post("/api/v1/knowledge/find", json={"query": "スイッチング損失", "scope": "patents"}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["results"][0]["ref"] == a
    doc = admin_client.get(f"/api/v1/knowledge/documents/{a}").json()
    assert doc["related"][0]["ref"] == b and doc["assets"][0]["url"].endswith(sha)
    assert admin_client.get(doc["assets"][0]["url"]).content.startswith(b"\x89PNG")
    assert admin_client.get("/api/v1/knowledge/assets/" + "0" * 64).status_code == 404
    assert admin_client.get("/api/v1/knowledge/documents/../etc").status_code in (404, 422)
    decided = admin_client.post("/api/v1/knowledge/standards/decide", json={"designation": "JIS C 0001"},
                                headers=CSRF_HEADERS).json()
    assert decided["ai_use"] == "prohibited"
