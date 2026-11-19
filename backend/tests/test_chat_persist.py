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


def test_sent_image_is_kept_on_the_message_served_and_removed_with_the_conversation(admin_client, monkeypatch):
    """送った画像は発言の meta に残り、本人だけが取得でき、会話を消すと一緒に消える。"""
    import app.workflows.chat_persist as cp

    async def fake_job(job, assistant_id, conv_id, history, params):
        # 画像は最後の user 発言へ content 配列として渡る（ここでは受け取ったことだけ確かめる）
        assert params["attachments"]
        return {"assistant_message_id": assistant_id}

    monkeypatch.setattr(cp, "_run_chat_job", fake_job)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    png = b"\x89PNG\r\n\x1a\nfake"
    image_id = admin_client.post(
        f"/api/v1/chat/conversations/{conv_id}/attachments",
        files={"file": ("貼り付け.png", png, "image/png")}, headers=CSRF_HEADERS,
    ).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send",
                          json={"content": "これは何？", "model": "test", "attachments": [image_id, "../evil"]},
                          headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    msgs = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
    user = next(m for m in msgs if m["role"] == "user")
    assert user["meta"]["attachments"] == [image_id]          # 形の正しい ID だけ残す

    got = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/attachments/{image_id}")
    assert got.status_code == 200 and got.content == png and got.headers["content-type"] == "image/png"
    assert admin_client.get(f"/api/v1/chat/conversations/{conv_id}/attachments/..%2Fx.png").status_code == 404
    assert admin_client.get(f"/api/v1/chat/conversations/{conv_id}/attachments/0000000000000000.png").status_code == 404
    assert admin_client.get(f"/api/v1/chat/conversations/nope/attachments/{image_id}").status_code == 404

    folder = cp._attachment_root(conv_id)
    assert folder.is_dir()
    assert admin_client.delete(f"/api/v1/chat/conversations/{conv_id}", headers=CSRF_HEADERS).status_code == 204
    assert not folder.exists()


def test_unsupported_image_type_is_rejected_clearly(admin_client):
    """HEIC など対応していない画像は、文書として読もうとせず理由を返す。"""
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/attachments",
                          files={"file": ("IMG_0001.HEIC", b"heic-bytes", "image/heic")}, headers=CSRF_HEADERS)
    assert r.status_code == 415 and "PNG" in r.json()["detail"]


def test_document_upload_says_when_the_embedding_model_is_unreachable(admin_client, monkeypatch):
    """文書は読めたのに埋め込みモデルへ届かないときは、「読めない」ではなく接続できないと伝える。"""
    import httpx

    import app.workflows.chat_persist as cp

    async def unreachable(conv_id, text, source):
        raise httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(cp, "register_chat_document", unreachable)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/attachments",
                          files={"file": ("note.md", ("本文" * 60).encode(), "text/markdown")}, headers=CSRF_HEADERS)
    assert r.status_code == 503 and "埋め込みモデル" in r.json()["detail"]


def _seed_conversation(admin_client):
    """user → assistant（OpenCode・画像つき）→ user → assistant の会話を DB に直接作る。"""
    import json as _json
    from datetime import timedelta

    import app.workflows.chat_persist as cp
    from app.database import SessionLocal
    from app.models import ChatMessage, utcnow

    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    image_id = admin_client.post(
        f"/api/v1/chat/conversations/{conv_id}/attachments",
        files={"file": ("a.png", b"\x89PNG\r\n\x1a\nx", "image/png")}, headers=CSRF_HEADERS,
    ).json()["id"]
    base = utcnow()
    rows = [
        ("u1", "user", "最初の質問", {"attachments": [image_id]}),
        ("a1", "assistant", "最初の回答", {"mode": "code", "opencode_session": "ses_x", "server_key": "abc"}),
        ("u2", "user", "次の質問", {}),
        ("a2", "assistant", "次の回答", {}),
    ]
    ids = {}
    with SessionLocal() as db:
        for i, (key, role, content, meta) in enumerate(rows):
            mid = cp._new_id()
            ids[key] = mid
            db.add(ChatMessage(id=mid, conversation_id=conv_id, role=role, content=content, status="completed",
                               meta_json=_json.dumps(meta), created_at=base + timedelta(seconds=i)))
        db.commit()
    return conv_id, ids, image_id


def test_fork_copies_up_to_the_message_and_keeps_the_original(admin_client):
    import app.workflows.chat_persist as cp

    conv_id, ids, image_id = _seed_conversation(admin_client)
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/fork", json={"message_id": ids["a1"]}, headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    fork_id = r.json()["id"]
    msgs = admin_client.get(f"/api/v1/chat/conversations/{fork_id}/messages").json()["messages"]
    assert [m["content"] for m in msgs] == ["最初の質問", "最初の回答"]
    assert "opencode_session" not in msgs[1]["meta"] and msgs[1]["meta"]["mode"] == "code"   # 別のセッションにする
    assert msgs[0]["meta"]["attachments"] == [image_id]
    assert (cp._attachment_root(fork_id) / image_id).is_file()                                # 画像も写す
    original = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
    assert len(original) == 4                                                                   # 元は残る

    # 編集して送り直す: その発言の手前まで
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/fork",
                          json={"message_id": ids["u2"], "include": False}, headers=CSRF_HEADERS)
    edit_msgs = admin_client.get(f"/api/v1/chat/conversations/{r.json()['id']}/messages").json()["messages"]
    assert [m["content"] for m in edit_msgs] == ["最初の質問", "最初の回答"]

    assert admin_client.post(f"/api/v1/chat/conversations/{conv_id}/fork", json={"message_id": "nope"},
                             headers=CSRF_HEADERS).status_code == 404
    assert admin_client.post("/api/v1/chat/conversations/nope/fork", json={"message_id": ids["u1"]},
                             headers=CSRF_HEADERS).status_code == 404


def test_delete_message_removes_its_reply_and_refuses_while_generating(admin_client, monkeypatch):
    from types import SimpleNamespace

    import app.workflows.chat_persist as cp
    from app.database import SessionLocal
    from app.models import ChatMessage

    monkeypatch.setattr(cp.jobs, "get", lambda job_id: SimpleNamespace(status="running") if job_id == "live" else None)

    conv_id, ids, _ = _seed_conversation(admin_client)
    r = admin_client.delete(f"/api/v1/chat/conversations/{conv_id}/messages/{ids['u1']}", headers=CSRF_HEADERS)
    assert r.status_code == 204
    left = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
    assert [m["content"] for m in left] == ["次の質問", "次の回答"]                               # 回答も消える

    with SessionLocal() as db:
        m = db.get(ChatMessage, ids["a2"])
        m.status, m.job_id = "generating", "live"
        db.commit()
    assert admin_client.delete(f"/api/v1/chat/conversations/{conv_id}/messages/{ids['u2']}",
                               headers=CSRF_HEADERS).status_code == 409
    assert admin_client.post(f"/api/v1/chat/conversations/{conv_id}/fork", json={"message_id": ids["a2"]},
                             headers=CSRF_HEADERS).status_code == 409
    # 回答だけを消す
    with SessionLocal() as db:
        db.get(ChatMessage, ids["a2"]).status = "completed"
        db.commit()
    assert admin_client.delete(f"/api/v1/chat/conversations/{conv_id}/messages/{ids['a2']}",
                               headers=CSRF_HEADERS).status_code == 204
    left = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
    assert [m["content"] for m in left] == ["次の質問"]


def test_fork_remaps_compaction_carries_edit_images_and_copies_documents(admin_client):
    import json as _json

    import app.workflows.chat_persist as cp
    from app.database import SessionLocal
    from app.models import ChatMessage
    from app.workflows import rag

    conv_id, ids, image_id = _seed_conversation(admin_client)
    with SessionLocal() as db:
        a2 = db.get(ChatMessage, ids["a2"])
        a2.meta_json = _json.dumps({"compaction": {"summary": "要約", "covers_until": ids["a1"]},
                                    "permissions": [{"id": "p1", "status": "pending"}]})
        db.commit()
    rag.set_config(cp.conversation_collection(conv_id), {})
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/fork", json={"message_id": ids["a2"]}, headers=CSRF_HEADERS)
    body = r.json()
    assert body["documents_copied"] is True and rag.collection_exists(cp.conversation_collection(body["id"]))
    assert body["opencode_context_reset"] is True
    msgs = admin_client.get(f"/api/v1/chat/conversations/{body['id']}/messages").json()["messages"]
    assert msgs[3]["meta"]["compaction"]["covers_until"] == msgs[1]["id"]          # 分岐先の ID に付け替え
    assert msgs[3]["meta"]["permissions"][0]["status"] == "expired"

    # 編集して送り直す: 書き直す発言の画像を分岐先へ写して返す
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/fork",
                          json={"message_id": ids["u1"], "include": False}, headers=CSRF_HEADERS)
    assert r.json()["attachments"] == [image_id]
    assert (cp._attachment_root(r.json()["id"]) / image_id).is_file()


def test_stale_generating_message_can_be_deleted(admin_client):
    """再起動でジョブが消え「generating」のまま残った発言も消せる・分岐できる。"""
    import app.workflows.chat_persist as cp
    from app.database import SessionLocal
    from app.models import ChatMessage

    conv_id, ids, image_id = _seed_conversation(admin_client)
    with SessionLocal() as db:
        m = db.get(ChatMessage, ids["a1"])
        m.status, m.job_id = "generating", "gone-job"
        db.commit()
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/fork", json={"message_id": ids["a1"]}, headers=CSRF_HEADERS)
    assert r.status_code == 201
    forked = admin_client.get(f"/api/v1/chat/conversations/{r.json()['id']}/messages").json()["messages"]
    assert forked[1]["status"] == "interrupted"
    assert admin_client.delete(f"/api/v1/chat/conversations/{conv_id}/messages/{ids['u1']}",
                               headers=CSRF_HEADERS).status_code == 204
    assert not (cp._attachment_root(conv_id) / image_id).exists()                     # 画像も消える


def test_archive_hides_from_list_and_restores(admin_client):
    """会話のアーカイブ（2026-11-17 の依頼: 履歴を左右のスワイプでアーカイブか削除）。消さずに一覧から外し、戻せる。"""
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.patch(f"/api/v1/chat/conversations/{conv_id}", json={"archived": True}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["archived"] is True
    assert conv_id not in [c["id"] for c in admin_client.get("/api/v1/chat/conversations").json()]
    archived = admin_client.get("/api/v1/chat/conversations?archived=true").json()
    assert [c["id"] for c in archived] == [conv_id] and archived[0]["archived"] is True
    assert admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").status_code == 200   # 中身は残る
    admin_client.patch(f"/api/v1/chat/conversations/{conv_id}", json={"archived": False}, headers=CSRF_HEADERS)
    assert conv_id in [c["id"] for c in admin_client.get("/api/v1/chat/conversations").json()]
    assert admin_client.patch(f"/api/v1/chat/conversations/{conv_id}", json={}, headers=CSRF_HEADERS).status_code == 422


def test_japanese_questions_get_a_final_language_instruction():
    """日本語の質問には、生成の直前に「日本語で」を足す（2026-11-17: DS-XS が検索資料に引かれて中国語で答えた）。"""
    from app.workflows.chat_persist import language_guard

    history = [{"role": "system", "content": "資料: 特朗普政府…"}, {"role": "user", "content": "トランプ大統領の近況を調査して"}]
    guarded = language_guard(history, "トランプ大統領の近況を調査して")
    assert guarded[0] == history[0] and len(guarded) == 2              # 途中に system を足さない（テンプレートが嫌う）
    assert guarded[-1]["role"] == "user" and guarded[-1]["content"].startswith("トランプ大統領の近況を調査して")
    assert "日本語" in guarded[-1]["content"] and history[-1]["content"] == "トランプ大統領の近況を調査して"  # 元は変えない
    assert language_guard(history, "What is new with Trump?") == history     # かなの無い質問には足さない


def test_voice_conversation_gets_spoken_rules_and_the_chosen_style(admin_client):
    """会話モードの発言には、話し言葉の決まり（絵文字・箇条書きなし、短く、相手の言語で）と選んだ回答傾向を最後に足す。
    文字の会話には足さない。会話モードは思考を切って待たせない（2026-11-19 利用者「会話モードに即した回答に。回答傾向を選べるように」）。"""
    from app.workflows.chat_persist import SendBody
    from app.workflows.voice_style import STYLES, voice_guard

    history = [{"role": "system", "content": "s"}, {"role": "user", "content": "明日の天気は？"}]
    assert voice_guard(history, "") == history
    spoken = voice_guard(history, "teacher")[-1]["content"]
    assert spoken.startswith("明日の天気は？") and "絵文字" in spoken and "箇条書き" in spoken and "英語で話されたら英語で" in spoken
    assert STYLES["teacher"][1] in spoken and history[-1]["content"] == "明日の天気は？"
    assert STYLES["friendly"][1] in voice_guard(history, "unknown")[-1]["content"]   # 知らない傾向は既定
    image = [{"role": "user", "content": [{"type": "text", "text": "これは何？"}, {"type": "image_url", "image_url": {"url": "x"}}]}]
    assert "絵文字" in voice_guard(image, "polite")[-1]["content"][0]["text"]
    assert SendBody(content="x", voice_style="friendly").voice_style == "friendly"
