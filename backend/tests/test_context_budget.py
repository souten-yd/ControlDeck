"""LLM を使う機能の量を、モデルのコンテキスト長から決める（context_budget）と、チャットから OpenCode への引き継ぎ。

目的: 2026-11-18 利用者の依頼「コンテキスト長（512K〜1M）を考慮した値に、Deep Research・検索・調査・RAG・チャット・
コード開発で自動設定」「チャットの結果を同じ会話で OpenCode に引き継ぐ」「OpenCode の開発中に調査を挟んで反映」。
  - 窓が広いほど多く入れるが、根拠の本文は上限で止める（長い入力ほど精度が落ちる: RULER・NoLiMa・Context Rot）
  - 短い窓では、根拠＋回答の分が窓を超えない
  - 窓が分からないときは従来の固定値（Deep Research 90,000 文字前後）と同程度
  - 引き継ぐのは前回の OpenCode の回答より後のチャット・調査（出典つき）。上限を超えたら古いほうから落とす
"""
from __future__ import annotations

from app.models_mgmt import context_budget as cb


def test_bigger_windows_get_more_but_evidence_is_capped():
    small, mid, big, huge = (cb.for_window(w) for w in (32_768, 131_072, 524_288, 1_048_576))
    assert small.research_evidence_chars < mid.research_evidence_chars < big.research_evidence_chars
    assert big.research_evidence_chars == huge.research_evidence_chars == cb.EVIDENCE_CAP_TOKENS * cb.CHARS_PER_TOKEN
    assert small.rag_top_k == 4 and mid.rag_top_k == 6 and big.rag_top_k == huge.rag_top_k == 8
    assert huge.research_report_tokens > big.research_report_tokens
    assert huge.handoff_chars > mid.handoff_chars


def test_short_windows_leave_room_for_the_answer():
    for window in (8_192, 16_384, 32_768):
        b = cb.for_window(window)
        used = b.research_evidence_chars // cb.CHARS_PER_TOKEN + b.output_tokens
        assert used < window
        assert b.research_report_tokens <= window // 2


def test_unknown_window_is_close_to_the_old_fixed_values():
    b = cb.for_window(0)
    assert not b.known and b.window == cb.DEFAULT_WINDOW
    assert 80_000 <= b.research_evidence_chars <= 110_000   # 旧既定 90,000 文字
    assert b.research_report_tokens == 32_768               # 旧既定。短くしない（レビュー指摘）


def test_exhaustive_stays_within_the_cap_and_the_window():
    for window in (8_192, 32_768, 131_072, 1_048_576):
        b = cb.for_window(window)
        assert b.exhaustive_evidence_chars <= int(cb.EVIDENCE_CAP_TOKENS * 1.5) * cb.CHARS_PER_TOKEN
        assert b.exhaustive_evidence_chars // cb.CHARS_PER_TOKEN + b.exhaustive_report_tokens <= window


def test_custom_deep_research_values_from_before_stay_in_use():
    from app.models_mgmt.runtime_policy import DeepResearchSettings

    assert DeepResearchSettings.model_validate({"evidence_context_chars": 300000}).auto_from_context is False
    assert DeepResearchSettings.model_validate({}).auto_from_context is True
    assert DeepResearchSettings.model_validate({"evidence_context_chars": 300000, "auto_from_context": True}).auto_from_context


def test_for_model_uses_the_managed_window(monkeypatch):
    from app.models_mgmt import compaction

    monkeypatch.setattr(compaction, "window_for", lambda base_url, model: 1_048_576)
    assert cb.for_model("http://127.0.0.1:8080/v1", "maya").window == 1_048_576
    monkeypatch.setattr(compaction, "window_for", lambda base_url, model: (_ for _ in ()).throw(RuntimeError()))
    assert cb.for_model("http://x", "y").known is False


def _add(db, conv_id, role, content, *, mode=None, status="completed", sources=None, at=0, session=None):
    import json
    from datetime import datetime, timedelta

    from app.models import ChatMessage
    from app.workflows.chat_persist import _new_id

    meta = {"mode": mode} if mode else {}
    if sources:
        meta["sources"] = sources
    if session:
        meta["opencode_session"] = session
    row = ChatMessage(id=_new_id(), conversation_id=conv_id, role=role, content=content, status=status,
                      meta_json=json.dumps(meta) if meta else None,
                      created_at=datetime(2026, 11, 18) + timedelta(seconds=at))
    db.add(row)
    db.commit()
    return row.id


def test_handoff_carries_chat_and_research_after_the_last_opencode_answer(admin_client):
    from tests.conftest import CSRF_HEADERS

    from app.database import SessionLocal
    from app.workflows.chat_persist import handoff_context

    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    db = SessionLocal()
    try:
        _add(db, conv_id, "user", "古い相談", at=1)
        _add(db, conv_id, "assistant", "古い回答", mode="chat", at=2)
        _add(db, conv_id, "user", "実装して", at=3)
        _add(db, conv_id, "assistant", "実装しました", mode="code", at=4, session="ses1")   # ここより後だけ
        _add(db, conv_id, "user", "SQLite の WAL を調べて。" + "詳しく。" * 80, at=5)
        _add(db, conv_id, "assistant", "WAL は書き込みを別ファイルに…", mode="deep", at=6,
             sources=[{"reference_id": "R1", "title": "SQLite WAL", "url": "https://sqlite.org/wal.html"}])
        _add(db, conv_id, "assistant", "途中で失敗", mode="chat", status="failed", at=7)
        current = _add(db, conv_id, "user", "調べた結果で直して", at=8)
    finally:
        db.close()
    text, count = handoff_context(conv_id, current, 50_000)
    assert count == 2
    assert "SQLite の WAL を調べて" in text and "WAL は書き込みを別ファイルに" in text
    assert "調査（Deep Research）" in text and "https://sqlite.org/wal.html" in text
    assert "古い相談" not in text and "実装しました" not in text and "途中で失敗" not in text
    assert "調べた結果で直して" not in text      # 今回の発言は依頼として別に付く

    # 上限を超えたら古いほうから落とし、最新の結論を残す
    text, count = handoff_context(conv_id, current, 400)
    assert count == 1 and "WAL は書き込み" in text and "省略" in text
    assert len(text) <= 400 + 10


def test_nothing_to_hand_off_right_after_an_opencode_answer_even_if_it_was_stopped(admin_client):
    from tests.conftest import CSRF_HEADERS

    from app.database import SessionLocal
    from app.workflows.chat_persist import handoff_context

    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    db = SessionLocal()
    try:
        _add(db, conv_id, "user", "実装して", at=1)
        _add(db, conv_id, "assistant", "途中で止めた", mode="code", status="canceled", at=2, session="ses1")
        current = _add(db, conv_id, "user", "続けて", at=3)
    finally:
        db.close()
    assert handoff_context(conv_id, current, 50_000) == ("", 0)


def test_research_for_code_returns_evidence_and_keeps_the_opencode_answer(monkeypatch):
    """「調べて反映して」: 調べた根拠を OpenCode への依頼に付け、回答の meta は OpenCode のまま（出典は残す）。"""
    import asyncio

    import app.workflows.chat_persist as cp
    from app.workflows import assistant_planner

    async def fake_decide(query, base_url, model, api_key=""):
        return assistant_planner.AssistantPlan(mode="web", reason="t", steps=[
            assistant_planner.ResearchStep(tool="web", query="sqlite wal")], decided_by="rule")

    async def fake_research(job, buf, conv_id, query, params, plan):
        assert plan is not None and plan.steps[0].query == "sqlite wal"
        buf["meta"] = {**buf.get("meta", {}), "mode": "research", "sources": [{"reference_id": "R1", "url": "https://sqlite.org/wal.html"}]}
        return [{"role": "system", "content": "以下は根拠です。\n\n[R1] SQLite WAL\nWAL は…\nhttps://sqlite.org/wal.html"},
                {"role": "user", "content": query}]

    monkeypatch.setattr(assistant_planner, "decide", fake_decide)
    monkeypatch.setattr(cp, "_server_research", fake_research)
    logs = []
    job = type("J", (), {"log": lambda self, *a, **k: logs.append(k.get("label"))})()
    tools: list = []
    buf = {"meta": {"mode": "code", "tools": tools}}
    note = asyncio.run(cp._research_for_code(job, buf, "c1", "WAL を調べて反映して", {"base_url": "x", "model": "m"}))
    assert note.startswith("## 調べた結果") and "[R1] SQLite WAL" in note and "以下は根拠です" not in note
    assert buf["meta"]["mode"] == "code" and buf["meta"]["tools"] is tools
    assert buf["meta"]["sources"][0]["reference_id"] == "R1"

    async def broken(*args, **kwargs):
        raise RuntimeError("search down")

    monkeypatch.setattr(cp, "_server_research", broken)
    buf = {"meta": {"mode": "code", "tools": tools}}
    assert asyncio.run(cp._research_for_code(job, buf, "c1", "q", {"base_url": "x", "model": "m"})) == ""
    assert buf["meta"] == {"mode": "code", "tools": tools}
