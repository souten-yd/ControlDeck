"""秘密の会話（一方通行）の外部への調査・外部の LLM を、通信の直前・送信の前で止める。

目的: 会話ごとの「この会話は秘密」（解除できない）が付いた会話の中で、外部への調査（Web 検索・ページ取得・学術/特許/市場/GitHub・
Deep Research）が **通信の前に** ExternalResearchBlocked で止まること、外部のモデルを選んでいてもローカルで答えること、
OpenCode へは流さないこと、途中で押すと動いている生成が止まること。秘密でない会話・会話の外（特許の公報の取得・Workflow）は止めないこと。
根拠: 利用者の決定（2026-11-21）「AI に利用を制限させるのではなく、根本的につながらないようにする」「途中から秘密の話をしたくなって
押すことはよくある。一度押した会話の文脈には秘密が入っているので、解除して外部へ調査に出られてはいけない」。
httpx・ネットワークバックエンドは記録用に差し替え、実際の外部へは通信しない。"""
import asyncio
import json

import httpx
import pytest

from tests.conftest import CSRF_HEADERS


@pytest.fixture()
def sent(monkeypatch):
    """httpx.AsyncClient を作った回数・送ったリクエストを記録する（実際には送らない）。"""
    log = {"clients": 0, "requests": []}
    original = httpx.AsyncClient

    def handler(request):
        log["requests"].append(str(request.url))
        return httpx.Response(200, json={"results": [], "items": []}, request=request)

    def factory(*args, **kwargs):
        log["clients"] += 1
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return log


def test_scope_only_blocks_inside_and_resets():
    from app.security import offline

    offline.ensure_external_allowed("外")            # スコープの外（特許の公報の取得・Workflow など）は止めない
    assert offline.in_confidential_scope() is False
    with offline.confidential_scope():
        assert offline.in_confidential_scope() is True
        with pytest.raises(offline.ExternalResearchBlocked):
            offline.ensure_external_allowed("外")
    offline.ensure_external_allowed("外")            # 抜けたら戻る
    with offline.confidential_scope(False):          # 秘密でない会話は何も立てない
        offline.ensure_external_allowed("外")


def test_egress_blocked_before_resolving_or_connecting(monkeypatch):
    from app.security.offline import ExternalResearchBlocked, confidential_scope
    from app.web_collector import egress, fetch

    connected = []
    monkeypatch.setattr(egress.httpcore.AnyIOBackend, "connect_tcp",
                        lambda self, *a, **k: connected.append(a) or (_ for _ in ()).throw(AssertionError("接続した")))
    policy_override = egress.Policy(resolver_override={"example.test": ["93.184.216.34"]})

    async def scenario():
        with confidential_scope():
            with pytest.raises(ExternalResearchBlocked):
                await egress.resolve_public("example.test", 443, policy_override)
            with pytest.raises(ExternalResearchBlocked):
                await egress.GuardedBackend(policy_override).connect_tcp("example.test", 443)
            with pytest.raises(ExternalResearchBlocked):
                await egress.open_guarded("example.test", 443, policy_override)
            page = await fetch.collect("https://example.test/", policy=policy_override)
            assert page["status"] == "security_rejected" and "秘密の会話" in page["error"]
        # 会話の外（特許の公報の取得・Workflow の scrape など）は止まらない
        assert await egress.resolve_public("example.test", 443, policy_override) == ["93.184.216.34"]

    asyncio.run(scenario())
    assert connected == []


def test_node_web_search_blocked_before_any_http(sent, monkeypatch):
    from app.security.offline import ExternalResearchBlocked, confidential_scope
    from app.workflows import nodes, searxng

    started = []

    async def resolve_url(url):
        return "http://127.0.0.1:8888"

    async def ensure_running(base):
        started.append(base)

    monkeypatch.setattr(searxng, "resolve_url", resolve_url)
    monkeypatch.setattr(searxng, "ensure_running", ensure_running)

    async def scenario():
        with confidential_scope():
            for engine in ("duckduckgo", "searxng"):
                with pytest.raises(ExternalResearchBlocked):
                    await nodes.node_web_search({"query": "x", "engine": engine}, {})
        assert started == [] and sent["clients"] == 0   # SearXNG の起動も、検索も、していない
        out = await nodes.node_web_search({"query": "x", "engine": "searxng"}, {})   # 会話の外は今までどおり
        assert out["results"] == []

    asyncio.run(scenario())
    assert sent["requests"] == ["http://127.0.0.1:8888/search?q=x&format=json"]


def test_external_search_every_source_blocked_before_http(sent):
    from app.security.offline import ExternalResearchBlocked, confidential_scope
    from app.workflows import external_search as ext

    async def scenario():
        with confidential_scope():
            for source in list(ext.SOURCES) + ["patent"]:
                with pytest.raises(ExternalResearchBlocked):
                    await ext.search(source, "q", 3, api_key="k")
            fed = await ext.federated("q", 3)
            assert fed["results"] == [] and set(fed["errors"]) == set(ext.FEDERATED_SOURCES)
            assert all("秘密の会話" in message for message in fed["errors"].values())

    asyncio.run(scenario())
    assert sent["clients"] == 0 and sent["requests"] == []


def test_external_search_outside_scope_still_works(sent):
    """特許の公報の取得など、会話の外は止めない。"""
    from app.workflows import external_search as ext

    assert asyncio.run(ext.patent("q", 3, "k")) == [] and sent["clients"] == 1


def test_github_inspection_blocked(sent):
    from app.security.offline import confidential_scope
    from app.workflows import github_research

    async def scenario():
        with confidential_scope():
            return await github_research.inspect_repository("o", "r", "q")

    result = asyncio.run(scenario())
    assert result["sources"] == [] and "秘密の会話" in result["errors"][0]
    assert sent["clients"] == 0


def _deep_stubs(monkeypatch):
    from app.models_mgmt import runtime_policy as rp
    from app.workflows import chat_router as cr
    from app.workflows import deep_research, engine

    captured = {}

    async def prepare(base, model):
        return {}

    async def restore(state):
        return {"restore_reason": "x"}

    async def fake_run(query, **kwargs):
        captured.update(kwargs)
        return {"report": "本文", "sources": [], "research": {}}

    monkeypatch.setattr(rp, "prepare_deep_research_context", prepare)
    monkeypatch.setattr(rp, "restore_deep_research_context", restore)
    monkeypatch.setattr(engine, "_load_secrets", lambda: {})
    monkeypatch.setattr("app.models_mgmt.runtime_provider.resolve_target", lambda b, m: (b, m))
    monkeypatch.setattr(deep_research, "run_deep_research", fake_run)
    current = rp.get_policy()
    monkeypatch.setattr(rp, "get_policy", lambda: current.model_copy(update={
        "deep_research": current.deep_research.model_copy(update={"auto_from_context": False})}))
    return cr, captured


def test_deep_research_uses_only_local_sources_in_secret_conversation(monkeypatch):
    from app.security.offline import confidential_scope

    cr, captured = _deep_stubs(monkeypatch)
    with confidential_scope():
        body = cr.SearchBody(query="q", mode="deep", base_url="http://127.0.0.1:9/v1", model="m",
                             source_types=["web", "academic", "github", "rag", "patent"])
        result = asyncio.run(cr._deep_search(body))
        assert captured["requested_source_types"] == ["rag"]
        assert result["report"].startswith("> 外部への調査は止めています") and result["research"]["offline_research"] is True
        # 外部だけを選んでいても、LLM の計画（外部を含みうる）には任せず、ローカルだけを明示する
        body = cr.SearchBody(query="q", mode="deep", base_url="http://127.0.0.1:9/v1", model="m", source_types=["web"])
        asyncio.run(cr._deep_search(body))
        assert captured["requested_source_types"] == ["rag", "local_code"]


def test_deep_research_default_sources_in_normal_conversation(monkeypatch):
    cr, captured = _deep_stubs(monkeypatch)
    body = cr.SearchBody(query="q", mode="deep", base_url="http://127.0.0.1:9/v1", model="m")
    result = asyncio.run(cr._deep_search(body))
    assert captured["requested_source_types"] == ["web", "academic", "github", "direct"]
    assert not result["report"].startswith(">")


def _new_conversation(client, confidential=None):
    kwargs = {"json": {"confidential": confidential}} if confidential is not None else {}
    r = client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS, **kwargs)
    assert r.status_code == 201, r.text
    return r.json()


def test_confidential_is_one_way_audited_and_can_start_secret(admin_client):
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AuditLog, Conversation

    normal = _new_conversation(admin_client)
    assert normal["confidential"] is False
    url = f"/api/v1/chat/conversations/{normal['id']}/confidential"
    assert admin_client.post(url, json={"confidential": False}, headers=CSRF_HEADERS).status_code == 409   # 解除の口は無い
    r = admin_client.post(url, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["confidential"] is True
    assert admin_client.post(url, json={"confidential": False}, headers=CSRF_HEADERS).status_code == 409
    # PATCH に confidential を送っても外れない（欄が無いので無視され、変える項目なしの 422）
    r = admin_client.patch(f"/api/v1/chat/conversations/{normal['id']}", json={"confidential": False}, headers=CSRF_HEADERS)
    assert r.status_code == 422
    listed = {c["id"]: c for c in admin_client.get("/api/v1/chat/conversations").json()}
    assert listed[normal["id"]]["confidential"] is True
    # 会話に一言残る
    messages = admin_client.get(f"/api/v1/chat/conversations/{normal['id']}/messages").json()
    rows = messages if isinstance(messages, list) else messages.get("messages", [])
    assert any("秘密にしました" in m["content"] for m in rows)
    # どの経路でも外せない（ORM の更新も拒む）
    with SessionLocal() as db:
        conv = db.get(Conversation, normal["id"])
        conv.confidential_at = None
        with pytest.raises(ValueError):
            db.commit()
        db.rollback()
    # 最初から秘密で作れる
    started = _new_conversation(admin_client, confidential=True)
    assert started["confidential"] is True
    with SessionLocal() as db:
        found = db.execute(select(AuditLog).where(AuditLog.action == "chat.conversation.confidential")).scalars().all()
        by_resource = {row.resource_id: json.loads(row.metadata_json) for row in found}
        assert by_resource[normal["id"]]["at"] == "midway" and by_resource[started["id"]]["at"] == "create"
    # 自分の会話でなければ押せない
    assert admin_client.post("/api/v1/chat/conversations/none/confidential", headers=CSRF_HEADERS).status_code == 404


def test_fork_of_secret_conversation_stays_secret(admin_client):
    from app.database import SessionLocal
    from app.models import ChatMessage

    conv = _new_conversation(admin_client, confidential=True)["id"]
    with SessionLocal() as db:
        db.add(ChatMessage(id="of-fork-1", conversation_id=conv, role="user", content="秘密の話", status="completed"))
        db.commit()
    r = admin_client.post(f"/api/v1/chat/conversations/{conv}/fork", json={"message_id": "of-fork-1"}, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    assert r.json()["confidential"] is True


def test_search_endpoint_refuses_web_and_academic_for_secret_conversation(admin_client):
    secret_conv = _new_conversation(admin_client, confidential=True)
    for mode in ("web", "academic"):
        r = admin_client.post("/api/v1/chat/search", json={"query": "x", "mode": mode, "conversation_id": secret_conv["id"]},
                              headers=CSRF_HEADERS)
        assert r.status_code == 409 and "外部への調査は止めています" in r.json()["detail"]


def test_local_target_replaces_cloud_with_local_default_or_refuses(monkeypatch):
    from fastapi import HTTPException

    from app.models_mgmt import gateway
    from app.security import offline

    def resolve(base, model):
        if not gateway.is_gateway_url(base):
            return base, model                        # 外部の endpoint はそのまま返る
        if str(model).startswith("cloud:"):
            return base, model                        # ゲートウェイが外部モデルとして返す
        if model == gateway.AUTO_MODEL:
            return "http://127.0.0.1:8081/v1", "default-local"
        return "http://127.0.0.1:8082/v1", model

    monkeypatch.setattr("app.models_mgmt.runtime_provider.resolve_target", resolve)
    gw = gateway.base_url()
    assert offline.local_target(gw, "cloud:openrouter/some") == ("http://127.0.0.1:8081/v1", "default-local")
    assert offline.local_target("https://api.example.test/v1", "gpt") == ("http://127.0.0.1:8081/v1", "default-local")
    assert offline.local_target(gw, "mine") == ("http://127.0.0.1:8082/v1", "mine")   # ローカルはそのまま

    def none(base, model):
        if model == gateway.AUTO_MODEL:
            raise HTTPException(status_code=404, detail="転送先のモデルが登録されていません")
        return base, model

    monkeypatch.setattr("app.models_mgmt.runtime_provider.resolve_target", none)
    with pytest.raises(offline.LocalModelUnavailable, match="ローカルのモデルが使えないため"):
        offline.local_target(gw, "cloud:openrouter/some")     # 外部へは流さずエラー


def test_confidential_mark_only_when_request_supports_it():
    from app.models_mgmt.runtime_provider import RuntimeChatRequest
    from app.security import offline

    assert offline.confidential_request_kwargs() == {}                  # スコープの外は付けない
    with offline.confidential_scope():
        kwargs = offline.confidential_request_kwargs()
        assert kwargs == ({"confidential": True} if "confidential" in RuntimeChatRequest.__dataclass_fields__ else {})
        RuntimeChatRequest(base_url="http://127.0.0.1:9/v1", model="m", messages=[], **kwargs)   # 渡せる形


def _chat_setup(conv, msg, secret=False):
    from app.database import SessionLocal
    from app.models import ChatMessage, Conversation, utcnow

    with SessionLocal() as db:
        db.add(Conversation(id=conv, owner_user_id=None, confidential_at=utcnow() if secret else None))
        db.flush()
        db.add(ChatMessage(id=msg, conversation_id=conv, role="assistant", status="generating"))
        db.commit()


def _run_chat(cp, conv, msg, params):
    from app.jobs import service as jobs

    async def scenario():
        job = jobs.create("chat.completion", "t", lambda j: cp._run_chat_job(
            j, msg, conv, [{"role": "user", "content": "明日の横浜の天気は？"}],
            {"base_url": "http://127.0.0.1:9/v1", "model": "m", "thinking": "off", "max_output_tokens": 128, **params}))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if job.status not in ("queued", "running"):
                break
        return job

    return asyncio.run(scenario())


def _chat_provider(monkeypatch, cp, requests):
    from app.models_mgmt.runtime_provider import RuntimeChunk
    from app.workflows import rag

    class Provider:
        tools_verified = True

        async def stream_chat(self, request, request_id=None):
            requests.append(request)
            yield RuntimeChunk("content", content="答え")

        async def cancel(self, request_id):
            return False

    async def fake_ctx(base_url, model):
        return 32768

    monkeypatch.setattr("app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: Provider())
    monkeypatch.setattr(cp, "_context_max", fake_ctx)
    monkeypatch.setattr(rag, "collection_exists", lambda name: False)


@pytest.mark.parametrize("is_secret", [True, False])
def test_chat_auto_mode_web_search_tool_only_in_normal_conversation(client, monkeypatch, is_secret):
    import app.workflows.chat_persist as cp
    from app.workflows import chat_tools

    conv, msg = f"of-cv-{is_secret}", f"of-am-{is_secret}"
    _chat_setup(conv, msg, secret=is_secret)
    requests = []
    _chat_provider(monkeypatch, cp, requests)
    job = _run_chat(cp, conv, msg, {"mode": "chat", "mode_choice": "auto"})
    assert job.status == "succeeded", job.error
    names = [t["function"]["name"] for t in (requests[0].tools or [])]
    assert (chat_tools.WEB_SEARCH in names) is (not is_secret)
    notices = [e for e in job.events if "秘密の会話" in json.dumps(e, ensure_ascii=False)]
    assert bool(notices) is is_secret


def test_chat_job_in_secret_conversation_runs_inside_scope(client, monkeypatch):
    """会話の実行の入口がスコープを立てる（中の外部への通信は通信の前に止まる）。抜けたら戻る。"""
    import app.workflows.chat_persist as cp
    from app.security import offline

    conv, msg = "of-cv-scope", "of-am-scope"
    _chat_setup(conv, msg, secret=True)
    requests, seen = [], []
    _chat_provider(monkeypatch, cp, requests)
    original = cp._run_chat_job_impl

    async def spy(*a, **k):
        seen.append(offline.in_confidential_scope())
        return await original(*a, **k)

    monkeypatch.setattr(cp, "_run_chat_job_impl", spy)
    assert _run_chat(cp, conv, msg, {"mode": "chat"}).status == "succeeded"
    assert seen == [True] and offline.in_confidential_scope() is False


def test_chat_web_mode_becomes_plain_chat_in_secret_conversation(client, monkeypatch):
    """明示の「Web」「学術」でも、秘密の会話は検索をせず普通の会話にして、止めていることを示す。"""
    import app.workflows.chat_persist as cp

    conv, msg = "of-cv-web", "of-am-web"
    _chat_setup(conv, msg, secret=True)
    requests = []
    _chat_provider(monkeypatch, cp, requests)

    async def forbidden(*a, **k):
        raise AssertionError("検索した")

    monkeypatch.setattr(cp, "_server_search", forbidden)
    job = _run_chat(cp, conv, msg, {"mode": "web", "mode_choice": "web"})
    assert job.status == "succeeded", job.error
    assert any("秘密の会話" in json.dumps(e, ensure_ascii=False) for e in job.events)


def test_send_in_secret_conversation_uses_local_model_and_refuses_opencode(admin_client, monkeypatch):
    import app.workflows.chat_persist as cp
    from app.security import offline

    secret_conv = _new_conversation(admin_client, confidential=True)["id"]
    started = []

    async def fake_start_job(db, conv_id, body, owner, history, user_msg_id, assistant_id, archives):
        started.append((body.base_url, body.model))
        return {"job_id": "x"}

    monkeypatch.setattr(cp, "_start_job", fake_start_job)
    monkeypatch.setattr(offline, "local_target", lambda b, m: ("http://127.0.0.1:8081/v1", "local-default"))
    url = f"/api/v1/chat/conversations/{secret_conv}/send"
    r = admin_client.post(url, json={"content": "こんにちは", "mode": "chat", "base_url": "http://127.0.0.1:1/api/v1",
                                     "model": "cloud:openrouter/x"}, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    assert started == [("http://127.0.0.1:8081/v1", "local-default")]     # 外部のモデルを選んでも、ローカルへ送られる
    r = admin_client.post(url, json={"content": "直して", "mode": "code"}, headers=CSRF_HEADERS)
    assert r.status_code == 409 and "次の版で対応します" in r.json()["detail"]

    def none(b, m):
        raise offline.LocalModelUnavailable(offline.NO_LOCAL_MODEL)

    monkeypatch.setattr(offline, "local_target", none)       # ローカルのモデルが無ければ、外部へ流さずエラー
    r = admin_client.post(url, json={"content": "こんにちは", "mode": "chat", "model": "cloud:x"}, headers=CSRF_HEADERS)
    assert r.status_code == 409 and "ローカルのモデルが使えないため答えられません" in r.json()["detail"]
    assert len(started) == 1


def test_pressing_midway_cancels_running_generation(admin_client, monkeypatch):
    from app.database import SessionLocal
    from app.jobs import service as jobs
    from app.models import ChatMessage

    conv = _new_conversation(admin_client)["id"]
    with SessionLocal() as db:
        db.add(ChatMessage(id="of-mid-1", conversation_id=conv, role="assistant", status="generating", job_id="job-of-1"))
        db.commit()
    canceled = []

    async def fake_cancel(job_id):
        canceled.append(job_id)
        return True

    monkeypatch.setattr(jobs, "cancel_and_wait", fake_cancel)
    r = admin_client.post(f"/api/v1/chat/conversations/{conv}/confidential", headers=CSRF_HEADERS)
    assert r.status_code == 200 and canceled == ["job-of-1"]      # 動いている生成（OpenCode の回も同じ Job）を止める
    # 2 回目（すでに秘密）は何も止めず、何も変えない
    assert admin_client.post(f"/api/v1/chat/conversations/{conv}/confidential", headers=CSRF_HEADERS).status_code == 200
    assert canceled == ["job-of-1"]
