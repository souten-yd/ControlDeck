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
