"""統合後の再設計 R3-1: 文献ごとの AI 分析・審査基準と分類辞書の引用・MCP のナレッジ検索。

目的:
- 文献の AI 分析はローカル専用のモデルだけで動き、未確認の推論として原文の版付きで残ること。原文が変わったら
  分析を消さずに「stale（原文が変わった）」として持ち越し、古い承認を復活させないこと（統合の決まり 2）。
- 利用条件で AI に使えない文書（購入した規格など）は分析しないこと。
- 種類 law のコレクションを Patent の AI 支援が [基準n] で引き、種類 classification を分類候補として渡すこと
  （ResearchDataHub v0.2 にあった機能。v0.102 の移植で抜けていた）。
- MCP のナレッジ検索は相手を cloud とみなし、機密の資料を返さないこと（統合の決まり 3）。

根拠: 2026-11-21 利用者「各リポジトリの計画を ControlDeck 側に反映し、統合したときに整合性を持つように再検討して進める」。
docs/PLAN-knowledge-platform.md §10。
"""
from __future__ import annotations

import asyncio
import shutil

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


class Job:
    id, status = "job-r3", "running"

    def set_progress(self, *args):
        pass


def _local(monkeypatch, answer="## 何の文献か\nインバータの公報（推論）"):
    from app.addon_runtime import ai
    from app.patents import service

    sent = {}

    class Target:
        base_url, model, gateway_managed = "http://127.0.0.1:9/v1", "local-gguf", True

    async def local_target():
        return Target()

    async def complete(target, request, http_request, require_provenance=False):
        sent["local_only"] = request.local_only
        sent["prompt"] = request.messages[-1]["content"]
        return answer, None

    monkeypatch.setattr(service, "resolve_local_target", local_target)
    monkeypatch.setattr(ai, "_complete_with_host_admission", complete)
    return sent


def test_document_analysis_is_local_unreviewed_and_marked_stale_when_source_changes(monkeypatch):
    from app.knowledge import analysis, store

    sent = _local(monkeypatch)
    store.create_collection("lit", shard="patents", kind="patent")
    first = store.add_document("lit", [store.NewChunk("【請求項１】スイッチング素子を備えるインバータ。")], ext_id="jp-1",
                               body="【請求項１】スイッチング素子を備えるインバータ。", title="公報")
    ref = f"patents:{first['doc_id']}"
    asyncio.run(analysis.run(Job(), ref, "summary"))
    assert sent["local_only"] is True and "スイッチング素子" in sent["prompt"]
    notes = analysis.annotations_with_state(ref)
    assert notes[0]["basis"] == "model_inference" and notes[0]["review_status"] == "unreviewed" and not notes[0]["stale"]
    analysis.review(ref, notes[0]["id"], "checked")
    second = store.add_document("lit", [store.NewChunk("【請求項１】改訂後の請求項。")], ext_id="jp-1",
                                body="【請求項１】改訂後の請求項。", title="公報")
    carried = analysis.annotations_with_state(f"patents:{second['doc_id']}")
    assert len(carried) == 1 and carried[0]["stale"] and "インバータの公報" in carried[0]["content"]


def test_documents_that_may_not_reach_ai_are_not_analyzed(monkeypatch):
    from app.knowledge import analysis, store

    _local(monkeypatch)
    store.create_collection("std", shard="standards", kind="standard")
    doc = store.add_document("std", [store.NewChunk("IEC 61000-4-2")], title="IEC 61000-4-2", ai_use="prohibited")
    with pytest.raises(analysis.AnalysisUnavailable):
        asyncio.run(analysis.run(Job(), f"standards:{doc['doc_id']}", "summary"))


def test_assist_quotes_law_and_offers_classification_candidates(monkeypatch):
    from app.knowledge import ingest, store
    from app.patents import service

    async def no_embed(chunks, collection):
        return "", False

    monkeypatch.setattr(ingest, "_embed", no_embed)
    store.create_collection("guidelines", kind="law")
    store.add_document("guidelines", [store.NewChunk("進歩性の判断では、主引用発明との相違点について動機付けを検討する。")],
                       title="特許・実用新案審査基準 第III部第2章")
    store.create_collection("ipc")
    store.set_collection_kind("ipc", "classification")
    asyncio.run(ingest.ingest_classification("ipc", "H02M 7/48\tインバータ\nH02M 1/08\tスイッチング素子の制御回路\n説明の行"))
    out = asyncio.run(service._law_and_classes("inventive_step", "インバータ 動機付け 進歩性"))
    assert "[基準1] 特許・実用新案審査基準" in out
    out = asyncio.run(service._law_and_classes("search_plan", "インバータ スイッチング素子"))
    assert "H02M 7/48" in out and "[基準" not in out    # 検索式の設計は審査基準を引かない


def test_mcp_knowledge_search_treats_the_client_as_cloud(monkeypatch):
    from app.addons import agent_mcp
    from app.knowledge import context, store

    async def no_vector(question):
        return None, ""

    monkeypatch.setattr(context, "query_vector", no_vector)
    store.create_collection("secret")
    store.add_document("secret", [store.NewChunk("インバータの未公開アイデア")], title="社内メモ")
    store.create_collection("public-lit", shard="patents", sensitivity="public")
    store.add_document("public-lit", [store.NewChunk("インバータの公開公報")], title="公開公報")
    out = asyncio.run(agent_mcp._run_knowledge({"query": "インバータ"}))
    assert [s["title"] for s in out["sources"]] == ["公開公報"] and "未公開" not in out["text"]
    assert agent_mcp._knowledge_tool()["name"] == agent_mcp.KNOWLEDGE_TOOL
    with pytest.raises(Exception):
        asyncio.run(agent_mcp._run_knowledge({"query": "x", "extra": 1}))
