"""OpenCode に渡す前に ControlDeck の調査で調べる（chat_persist._research_for_code）。

目的: 2026-11-18 利用者の依頼「『調べて OpenCode に渡す』をボタンを押さずに」。OpenCode の会話で「〜を調べて反映して」と
送ると、同じ 1 回の送信の中で調べ、出典つきの根拠を OpenCode への依頼に付ける。
  - 調べた根拠は見出しを付けて返し、回答の meta は OpenCode のまま（道具の一覧は同じもの）。出典と調べた経過は残す
  - 量は上限まで（引き継ぎと合わせて OpenCode のコンテキストを超えない）
  - 調べられなくても OpenCode の作業は止めない（空を返し、meta を元に戻す）
"""
from __future__ import annotations


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
        buf["meta"] = {**buf.get("meta", {}), "mode": "research", "progress": [{"phase": "search"}],
                       "sources": [{"reference_id": "R1", "url": "https://sqlite.org/wal.html"}]}
        return [{"role": "system", "content": "以下は根拠です。\n\n[R1] SQLite WAL\nWAL は…\nhttps://sqlite.org/wal.html" + "詳細。" * 2000},
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
    assert buf["meta"]["progress"] == [{"phase": "search"}]          # 調べた経過も残す
    short = asyncio.run(cp._research_for_code(job, {"meta": {"mode": "code"}}, "c1", "q", {"base_url": "x", "model": "m"}, 1_200))
    assert len(short) <= 1_200 + 10                                   # 量の上限

    async def broken(*args, **kwargs):
        raise RuntimeError("search down")

    monkeypatch.setattr(cp, "_server_research", broken)
    buf = {"meta": {"mode": "code", "tools": tools}}
    assert asyncio.run(cp._research_for_code(job, buf, "c1", "q", {"base_url": "x", "model": "m"})) == ""
    assert buf["meta"] == {"mode": "code", "tools": tools}
