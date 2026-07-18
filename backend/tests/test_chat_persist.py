"""永続チャットのテスト（DB保存・ジョブ継続・切断耐性）。

LLM は実在しない可能性があるため、chat_persist の生成ロジックは monkeypatch で置換し、
「サーバー側ジョブが assistant メッセージへ保存し、WS 接続と無関係に完結する」ことを検証する。
"""
import asyncio
import time

from tests.conftest import CSRF_HEADERS


def test_conversation_crud(admin_client):
    r = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS)
    assert r.status_code == 201
    conv_id = r.json()["id"]
    assert any(c["id"] == conv_id for c in admin_client.get("/api/v1/chat/conversations").json())

    renamed = admin_client.patch(
        f"/api/v1/chat/conversations/{conv_id}", json={"title": "調査メモ"}, headers=CSRF_HEADERS,
    )
    assert renamed.status_code == 200 and renamed.json()["title"] == "調査メモ"
    # 空の会話メッセージ一覧
    r = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
    assert r.status_code == 200 and r.json()["messages"] == []
    assert admin_client.delete(f"/api/v1/chat/conversations/{conv_id}", headers=CSRF_HEADERS).status_code == 204


def test_send_creates_messages_and_job_persists(admin_client, monkeypatch):
    """送信で user+assistant が作られ、ジョブがサーバー側で assistant へ書き込み完了する。
    （WS を一切開かなくても＝ブラウザを閉じても）回答が DB に保存される。"""
    import app.workflows.chat_persist as cp

    async def fake_job(job, assistant_id, conv_id, history, params):
        # LLM の代わりに固定応答をチャンクで書く
        from app.database import SessionLocal
        from app.models import ChatMessage

        text = "こんにちは。テスト応答です。"
        db = SessionLocal()
        try:
            m = db.get(ChatMessage, assistant_id)
            m.content = text
            m.status = "completed"
            db.commit()
        finally:
            db.close()
        return {"assistant_message_id": assistant_id, "chars": len(text)}

    monkeypatch.setattr(cp, "_run_chat_job", fake_job)

    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send",
                          json={"content": "やあ", "model": "test"}, headers=CSRF_HEADERS)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "generating" and body["job_id"]

    # ジョブ完了を待つ（WS は開かない = ブラウザを閉じた状態を模擬）
    aid = body["assistant_message_id"]
    for _ in range(50):
        time.sleep(0.05)
        msgs = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
        a = next((m for m in msgs if m["id"] == aid), None)
        if a and a["status"] == "completed":
            break
    assert a["status"] == "completed"
    assert a["content"] == "こんにちは。テスト応答です。"
    # user メッセージも保存されている
    assert any(m["role"] == "user" and m["content"] == "やあ" for m in msgs)
    # 会話タイトルは最初の発話から（この経路では fake なので title 更新は _maybe_title 未呼び出し。
    # 実経路では設定される）


def test_send_rejects_foreign_conversation(admin_client):
    r = admin_client.post("/api/v1/chat/conversations/nonexistent/send",
                          json={"content": "x"}, headers=CSRF_HEADERS)
    assert r.status_code == 404


def test_checkpoint_saves_partial_on_cancel(client, monkeypatch):
    """生成中にキャンセルされても、それまでの部分出力が DB に残る（切断で回答が消えない）。"""
    import app.workflows.chat_persist as cp
    from app.database import SessionLocal
    from app.jobs import service as jobs
    from app.models import ChatMessage, Conversation

    # 会話と assistant プレースホルダを直接作る
    db = SessionLocal()
    try:
        db.add(Conversation(id="cvx", owner_user_id=None))
        db.flush()
        db.add(ChatMessage(id="amx", conversation_id="cvx", role="assistant", status="generating"))
        db.commit()
    finally:
        db.close()

    async def slow_job(job, assistant_id, conv_id, history, params):
        # 部分出力を書いてから長く待つ（その間にキャンセル）
        from app.database import SessionLocal as SL

        db = SL()
        try:
            m = db.get(ChatMessage, assistant_id)
            m.content = "途中まで"
            db.commit()
        finally:
            db.close()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            db = SL()
            try:
                m = db.get(ChatMessage, assistant_id)
                m.status = "canceled"
                db.commit()
            finally:
                db.close()
            raise

    async def scenario():
        job = jobs.create("chat.completion", "t", lambda j: slow_job(j, "amx", "cvx", [], {"base_url": "", "model": "m", "mode": "chat"}))
        await asyncio.sleep(0.2)
        jobs.cancel(job.id)
        for _ in range(40):
            await asyncio.sleep(0.05)
            if job.status != "running":
                break
        return job.status

    status = asyncio.run(scenario())
    assert status == "canceled"
    db = SessionLocal()
    try:
        m = db.get(ChatMessage, "amx")
        assert m.content == "途中まで"  # 部分出力が残っている
        assert m.status == "canceled"
    finally:
        db.close()


def test_run_chat_job_emits_generation_stats(client, monkeypatch):
    """生成中に stats イベント（フェーズ/tok/s/コンテキスト）が流れ、最終値は実測usageで確定する。"""
    import app.workflows.chat_persist as cp
    from app.database import SessionLocal
    from app.jobs import service as jobs
    from app.models import ChatMessage, Conversation
    from app.models_mgmt.runtime_provider import RuntimeChunk

    db = SessionLocal()
    try:
        db.add(Conversation(id="cvs", owner_user_id=None))
        db.flush()
        db.add(ChatMessage(id="ams", conversation_id="cvs", role="assistant", status="generating"))
        db.commit()
    finally:
        db.close()

    class FakeProvider:
        async def stream_chat(self, request, request_id=None):
            yield RuntimeChunk("thinking", content="考")
            yield RuntimeChunk("content", content="答")
            yield RuntimeChunk("usage", usage={"prompt_tokens": 12, "completion_tokens": 2})

        async def cancel(self, request_id):
            return False

    monkeypatch.setattr(
        "app.models_mgmt.runtime_provider.provider_for_base_url", lambda base: FakeProvider(),
    )

    async def fake_ctx(base_url, model):
        return 8192

    monkeypatch.setattr(cp, "_context_max", fake_ctx)

    async def scenario():
        job = jobs.create("chat.completion", "t", lambda j: cp._run_chat_job(
            j, "ams", "cvs", [{"role": "user", "content": "hi"}],
            {"base_url": "http://127.0.0.1:9/v1", "model": "m", "mode": "chat",
             "thinking": "off", "max_output_tokens": 128},
        ))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if job.status not in ("queued", "running"):
                break
        return job

    job = asyncio.run(scenario())
    assert job.status == "succeeded", job.error
    stats = [e for e in job.events if e.get("message") == "stats"]
    assert stats, "statsイベントが流れていない"
    final = stats[-1]
    assert final["phase"] == "done"
    assert final["prompt_tokens"] == 12 and final["gen_tokens"] == 2  # usageの実測値で確定
    assert final["context_max"] == 8192
    assert final["tok_per_sec"] >= 0


def test_upload_attachment_image_and_document(admin_client, monkeypatch):
    """📎添付: 画像は保存されID返却、文書は会話コレクションへRAG登録される。"""
    import app.workflows.chat_persist as cp
    from tests.conftest import CSRF_HEADERS

    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()

    # 画像（1x1 PNG 相当のダミーバイト列でも保存経路は同じ）
    r = admin_client.post(
        f"/api/v1/chat/conversations/{conv['id']}/attachments",
        files={"file": ("photo.png", b"\x89PNG-fake-bytes", "image/png")},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    image = r.json()
    assert image["kind"] == "image" and image["id"].endswith(".png")
    assert (cp._attachment_dir(conv["id"]) / image["id"]).is_file()

    # 文書（埋め込みAPIはモック）
    async def fake_register(conv_id, text, source):
        assert "テスト本文" in text
        return {"added_chunks": 3}

    monkeypatch.setattr(cp, "register_chat_document", fake_register)
    r = admin_client.post(
        f"/api/v1/chat/conversations/{conv['id']}/attachments",
        files={"file": ("notes.md", ("テスト本文" * 30).encode(), "text/markdown")},
        headers=CSRF_HEADERS,
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["kind"] == "document" and doc["chunks"] == 3
    assert doc["collection"] == f"chat-{conv['id']}"


def test_ollama_native_messages_converts_image_content():
    """OpenAI互換のcontent配列がOllama native形式（content+images）へ変換される。"""
    from app.models_mgmt.runtime_provider import OllamaRuntimeProvider

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [
            {"type": "text", "text": "この画像は？"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        ]},
    ]
    converted = OllamaRuntimeProvider._native_messages(messages)
    assert converted[0] == {"role": "system", "content": "sys"}
    assert converted[1]["content"] == "この画像は？"
    assert converted[1]["images"] == ["QUJD"]


def test_rag_ingest_job_endpoint(admin_client, monkeypatch):
    """RAG取り込みはサーバー側ジョブとして開始される（未存在コレクションは404）。"""
    from tests.conftest import CSRF_HEADERS

    r = admin_client.post(
        "/api/v1/knowledge/collections/no-such-collection/ingest-jobs",
        json={"text": "テスト"}, headers=CSRF_HEADERS,
    )
    assert r.status_code == 404
