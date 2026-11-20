"""ナレッジの作り直しと、会話の資料・AI の回答の扱い。

目的:
- AI の回答（「web回答: …」）は推論（model_inference）として保存し、旧データも作り直しで直すこと。
- 別の会話の資料（雑談・AI の回答）を、横断検索・アシスタントのナレッジ検索に混ぜないこと（その会話の中では使う）。
- 埋め込みの無い断片・別のモデルの断片を、いまのモデルで作り直すこと。
- 原本 PDF のある古い公報を新しい取り込み（ページ・書誌・図）で作り直し、案件の先行文献の参照を付け替えること。
- 計画の表示は何も書かないこと。

根拠: 2026-11-21 利用者「RAG もデータの再構築が必要ではないか。今のデータは使い物になるか」。本番で調べると AI の回答 57 件が
原文と同じ扱いで、ResearchDataHub から移した公報は埋め込み・書誌が無かった。
"""
from __future__ import annotations

import asyncio
import shutil

import numpy as np
import pytest

from app.config import data_dir


@pytest.fixture(autouse=True)
def fresh():
    from app.knowledge import storage, store

    shutil.rmtree(data_dir() / "knowledge", ignore_errors=True)
    storage.config_path().unlink(missing_ok=True)
    storage._cache = None
    store._schema_ready.clear()
    yield


def _fake_embed(monkeypatch, dim=4):
    from app.workflows import rag

    async def embed(texts, base_url, model, api_key):
        return [np.ones(dim, dtype=np.float32) for _ in texts]

    monkeypatch.setattr(rag, "embed", embed)
    monkeypatch.setattr(rag, "_managed_embed_defaults", lambda: {"embed_model": "now-model"})


def test_other_conversations_are_not_mixed_into_cross_search():
    from app.knowledge import documents, store

    store.create_collection("chat-aaaa", kind="conversation")
    store.create_collection("chat-bbbb", kind="conversation")
    store.create_collection("lit")
    store.add_document("chat-aaaa", [store.NewChunk("インバータの話（会話 A）")], title="web回答: A", basis="model_inference")
    store.add_document("chat-bbbb", [store.NewChunk("インバータの話（会話 B）")], title="web回答: B", basis="model_inference")
    store.add_document("lit", [store.NewChunk("インバータの資料")], title="資料")
    titles = {d["title"] for d in documents.search_documents("インバータ", consumer="local", mode="fulltext")}
    assert titles == {"資料"}
    bound = {d["title"] for d in documents.search_documents("インバータ", consumer="local", bound=["chat-aaaa"], mode="fulltext")}
    assert bound == {"資料", "web回答: A"}
    assert {h["collection"] for h in store.search_all("インバータ", consumer="local", mode="fulltext")} == {"lit"}


def test_chat_answers_are_saved_as_inference(client, monkeypatch):
    from app.knowledge import store
    from app.workflows import chat_persist, rag

    from app.database import SessionLocal
    from app.models import Conversation

    _fake_embed(monkeypatch)
    monkeypatch.setattr(chat_persist, "_collection_embed_override", lambda: {})
    with SessionLocal() as db:   # DB はテスト全体で共有。会話の行が要る
        if db.get(Conversation, "rb-conv") is None:
            db.add(Conversation(id="rb-conv", owner_user_id=None))
            db.commit()
    asyncio.run(chat_persist.register_chat_document("rb-conv", "回答の本文。" * 30, "web回答: 天気", basis="model_inference"))
    asyncio.run(chat_persist.register_chat_document("rb-conv", "検索結果の本文。" * 30, "web検索: 天気"))
    docs = {d["title"]: d for d in store.list_documents(chat_persist.conversation_collection("rb-conv"))}
    assert docs["web回答: 天気"]["basis"] == "model_inference" and docs["web検索: 天気"]["basis"] == "source_fact"
    assert rag.collection_exists(chat_persist.conversation_collection("rb-conv"))


def test_plan_writes_nothing_and_apply_fixes_answers_and_embeddings(monkeypatch):
    from app.knowledge import rebuild, store

    _fake_embed(monkeypatch)
    store.create_collection("chat-cccc", kind="conversation")
    old = store.add_document("chat-cccc", [store.NewChunk("回答", vector=np.ones(3, np.float32))], title="web回答: 昼食",
                             model="old-model")
    store.add_document("chat-cccc", [store.NewChunk("資料")], title="添付: memo.txt")
    todo = rebuild.plan()
    assert todo["retag_ai_answers"] == [f"general:{old['doc_id']}"] and len(todo["reembed"]) == 2
    assert store.list_documents("chat-cccc")[0]["basis"] == "source_fact"     # 計画では書かない
    out = asyncio.run(rebuild.apply())
    assert out["retagged"] == 1 and out["reembedded"] == 2 and not out["errors"]
    assert rebuild.plan() == {"retag_ai_answers": [], "reembed": [], "reingest_pdf": [], "describe_figures": []}


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="poppler-utils が無い")
def test_old_patent_pdf_is_reingested_and_case_refs_follow(tmp_path, monkeypatch):
    from app.knowledge import documents, ingest, rebuild, store
    from app.patents import store as pstore
    from tests.test_knowledge_documents import _pdf

    async def no_embed(chunks, collection):
        return "", False

    monkeypatch.setattr(ingest, "_embed", no_embed)
    pdf = _pdf(["(51)Int.Cl. H02M 7/48\nInverter with switching element and enough words here.", "Claim 1. An inverter."])
    store.create_collection("rdh-pdf-imports", shard="patents", kind="generic")
    old = store.add_document("rdh-pdf-imports", [store.NewChunk("old text")], title="old.pdf", ext_id="pdf:x",
                             body="old text")
    sha = store.put_blob("patents", pdf, "application/pdf", role="original")
    conn = store.connect("patents")
    conn.execute("INSERT INTO document_assets(doc_id, sha256, role, ord, meta) VALUES(?,?,?,?,?)",
                 (old["doc_id"], sha, "original", 0, "{}"))
    conn.commit()
    conn.close()
    case = pstore.create_case("インバータ")
    pstore.add_ref(case["id"], doc_ref=f"patents:{old['doc_id']}", category="X")
    assert rebuild.plan()["reingest_pdf"] == [f"patents:{old['doc_id']}"]
    out = asyncio.run(rebuild.apply(describe_figures=False))
    new_ref = out["reingested"][0]
    doc = documents.get_document(new_ref)
    assert doc["meta"]["pdf"]["page_count"] == 2 and doc["meta"]["biblio"]["ipc"] == ["H02M 7/48"]
    assert pstore.get_case(case["id"])["refs"][0]["doc_ref"] == new_ref


def test_embedding_input_is_capped_for_the_embedding_server(monkeypatch):
    """llama.cpp の埋め込みは 1 件 512 トークンまで。1200 文字の断片は HTTP 500 で断られた（2026-11-21 本番）。
    埋め込みに送る文字だけを上限で切り、断片の本文は切らない。"""
    from app.workflows import rag

    seen = []

    async def embed(texts, base_url, model, api_key):
        seen.extend(texts)
        return [np.ones(4, dtype=np.float32) for _ in texts]

    monkeypatch.setattr(rag, "embed", embed)
    asyncio.run(rag._embed_batched(["あ" * 1200, "短い"], {"embed_base_url": "x", "embed_model": "m"}, ""))
    assert len(seen[0]) == rag.EMBED_MAX_CHARS and seen[1] == "短い"


def test_figure_reading_is_planned_but_left_to_the_server(monkeypatch, capsys):
    """図表の読み取りは画像モデル（GPU）を使うので、サーバーの外の CLI では行わず、画面からの作り直しに回す。"""
    from app.knowledge import rebuild, store

    store.create_collection("lit", shard="patents", kind="patent")
    doc = store.add_document("lit", [store.NewChunk("本文")], title="公報",
                             meta={"pdf": {"figure_pages": [2], "figures_described": []}})
    assert rebuild.plan()["describe_figures"] == [f"patents:{doc['doc_id']}"]
    called = []
    monkeypatch.setattr(rebuild, "_reingest", lambda *a, **k: called.append(a))
    rebuild.main(["--apply"])
    assert called == [] and "画面" in capsys.readouterr().out
