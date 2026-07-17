"""会話内文献ID、解決API、選択的コンテキスト注入のテスト。"""
import time

from tests.conftest import CSRF_HEADERS


def test_reference_registry_uses_short_base36_ids_and_deduplicates(admin_client):
    from app.database import SessionLocal
    from app.workflows.reference_registry import register_sources

    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    sources = [
        {"title": f"資料 {index}", "url": f"https://example.test/docs/{index}", "snippet": f"根拠 {index}"}
        for index in range(1, 37)
    ]
    with SessionLocal() as db:
        registered = register_sources(db, conv_id, sources)
        duplicate = register_sources(db, conv_id, [dict(sources[0], url="https://EXAMPLE.test/docs/1#section")])

    assert registered[0]["reference_id"] == "R1"
    assert registered[8]["reference_id"] == "R9"
    assert registered[9]["reference_id"] == "RA"
    assert registered[34]["reference_id"] == "RZ"
    assert registered[35]["reference_id"] == "R10"
    assert duplicate[0]["reference_id"] == "R1"

    catalog = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/references")
    assert catalog.status_code == 200
    assert len(catalog.json()["references"]) == 36
    assert "excerpt" not in catalog.json()["references"][0]

    detail = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/references/R1")
    assert detail.status_code == 200
    assert detail.json()["excerpt"] == "根拠 1"

    resolved = admin_client.post(
        f"/api/v1/chat/conversations/{conv_id}/references/resolve",
        json={"reference_ids": ["R10", "R1", "NOT_FOUND"]}, headers=CSRF_HEADERS,
    )
    assert [item["reference_id"] for item in resolved.json()["references"]] == ["R10", "R1"]

    assert admin_client.delete(
        f"/api/v1/chat/conversations/{conv_id}", headers=CSRF_HEADERS,
    ).status_code == 204
    assert admin_client.get(f"/api/v1/chat/conversations/{conv_id}/references/R1").status_code == 404


def test_send_expands_only_explicitly_requested_references(admin_client, monkeypatch):
    import app.workflows.chat_persist as chat
    from app.database import SessionLocal
    from app.workflows.reference_registry import register_sources

    captured: dict = {}

    async def fake_job(_job, assistant_id, _conv_id, history, _params):
        captured["history"] = history
        return {"assistant_message_id": assistant_id}

    monkeypatch.setattr(chat, "_run_chat_job", fake_job)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    with SessionLocal() as db:
        register_sources(db, conv_id, [
            {"title": "選択した論文", "url": "https://example.test/selected", "snippet": "選択文献だけの本文"},
            {"title": "選択していない論文", "url": "https://example.test/other", "snippet": "注入されてはいけない本文"},
        ])

    response = admin_client.post(
        f"/api/v1/chat/conversations/{conv_id}/send",
        json={"content": "R1 の結論を説明して", "model": "test"}, headers=CSRF_HEADERS,
    )
    assert response.status_code == 201
    for _ in range(50):
        if captured:
            break
        time.sleep(0.02)
    system_context = "\n".join(
        message["content"] for message in captured["history"] if message["role"] == "system"
    )
    assert "[R1] 選択した論文" in system_context
    assert "選択文献だけの本文" in system_context
    assert "注入されてはいけない本文" not in system_context


def test_reference_parser_accepts_compact_user_notation():
    from app.workflows.reference_registry import extract_reference_ids

    assert extract_reference_ids("[R1] と @ra、それからR10を比較。R1は重複") == ["R1", "RA", "R10"]
    assert extract_reference_ids("RAGという単語は文献IDではない") == []
