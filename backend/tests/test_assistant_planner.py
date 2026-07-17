import asyncio

from tests.conftest import CSRF_HEADERS


def test_rule_plan_combines_web_and_academic():
    from app.workflows.assistant_planner import rule_plan

    plan = rule_plan("最新のLLM論文とWebニュースを横断して比較して")
    assert plan is not None
    assert plan.mode == "research"
    assert [step.tool for step in plan.steps] == ["web", "academic"]
    assert plan.decided_by == "rule"


def test_llm_plan_uses_structured_json(monkeypatch):
    import app.workflows.assistant_planner as planner

    async def fake_llm(*args, **kwargs):
        assert kwargs["disable_thinking"] is True
        assert kwargs["temperature"] == 0
        assert kwargs["response_format"]["type"] == "json_schema"
        return '```json\n{"mode":"research","reason":"比較調査", "steps":[' \
               '{"tool":"web","query":"製品の現状"},{"tool":"academic","query":"関連研究"}],"max_iterations":3}\n```'

    monkeypatch.setattr(planner, "_llm", fake_llm)
    plan = asyncio.run(planner.decide("製品の実績と研究上の評価をまとめて", "http://local/v1", "test"))
    assert plan.mode == "research"
    assert plan.decided_by == "llm"
    assert len(plan.steps) == 2


def test_invalid_llm_plan_falls_back_to_chat(monkeypatch):
    import app.workflows.assistant_planner as planner

    async def fake_llm(*args, **kwargs):
        return "JSONではありません"

    monkeypatch.setattr(planner, "_llm", fake_llm)
    plan = asyncio.run(planner.decide("どの方法が良さそうか考えて", "http://local/v1", "test"))
    assert plan.mode == "chat"
    assert plan.decided_by == "fallback"


def test_route_api_returns_validated_plan(admin_client, monkeypatch):
    import app.workflows.assistant_planner as planner

    async def fake_decide(content, base_url, model):
        return planner.AssistantPlan(mode="web", reason="現在情報", decided_by="llm")

    monkeypatch.setattr(planner, "decide", fake_decide)
    response = admin_client.post(
        "/api/v1/chat/route", json={"content": "調べて", "model": "test"}, headers=CSRF_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "web"
    assert response.json()["decided_by"] == "llm"


def test_send_rejects_mismatched_plan(admin_client):
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    response = admin_client.post(
        f"/api/v1/chat/conversations/{conv_id}/send",
        json={
            "content": "調査", "mode": "web",
            "plan": {"mode": "academic", "reason": "論文", "steps": [], "max_iterations": 1},
        },
        headers=CSRF_HEADERS,
    )
    assert response.status_code == 422


def test_research_combines_sources_and_deduplicates(monkeypatch):
    import app.workflows.assistant_planner as planner
    import app.workflows.chat_persist as chat
    from app.workflows import chat_router, external_search

    async def fake_web(body, query, limit):
        return [
            {"title": "共通資料", "url": "https://example.test/shared", "snippet": "Web側"},
            {"title": "最新情報", "url": "https://example.test/web", "snippet": "新しい情報"},
        ]

    async def fake_academic(query, limit):
        return {"results": [
            {"title": "共通資料", "url": "https://example.test/shared", "snippet": "論文側", "source": "OpenAlex"},
            {"title": "研究結果", "url": "https://example.test/paper", "snippet": "検証結果", "source": "arXiv"},
        ]}

    async def fake_evaluate(*args, **kwargs):
        return planner.ResearchEvaluation(sufficient=True, reason="十分")

    monkeypatch.setattr(chat_router, "_web_results", fake_web)
    monkeypatch.setattr(external_search, "federated", fake_academic)
    monkeypatch.setattr(planner, "evaluate", fake_evaluate)

    class FakeJob:
        def __init__(self):
            self.events = []

        def log(self, message, **data):
            self.events.append((message, data))

    plan = planner.AssistantPlan(
        mode="research", reason="横断調査", max_iterations=3,
        steps=[planner.ResearchStep(tool="web", query="q"), planner.ResearchStep(tool="academic", query="q")],
    )
    job = FakeJob()
    buf = {"meta": {"plan": plan.model_dump(), "progress": []}}
    history = asyncio.run(chat._server_research(
        job, buf, "q", {"base_url": "http://local/v1", "model": "m", "engine": "duckduckgo"}, plan,
    ))
    assert len(buf["meta"]["sources"]) == 3
    assert len({source["url"] for source in buf["meta"]["sources"]}) == 3
    assert "[1]" in history[0]["content"]
    assert any(message == "progress" and data["phase"] == "sufficient" for message, data in job.events)
