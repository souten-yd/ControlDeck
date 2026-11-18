"""AI アシスタント: 生成中に送った要望（送信待ち）をサーバーが持ち、回答が終わったら送る。

目的: 2026-11-18 利用者「実行中に要望を送信したい。送信した要望はサーバー側で保存しておき、適切なタイミングで
LLM に送信してほしい。複数の要望の送信や削除、編集、取り消しなどを可能に」。
  - 生成中に貯めた要望は、回答が正常に終わったら自動で送る。同じ送り方の続きは 1 つの発言にまとめる
  - 止めた・失敗した回答の後は送らずに保留し、「今すぐ送る」で送る
  - 編集・削除（元に戻す）・すべて取り消す。送った後は編集できない
"""
import threading
import time

from tests.conftest import CSRF_HEADERS


def _fake_runner(monkeypatch, gate: threading.Event, final_status: list[str]):
    """生成の代わり。gate が開くまで「生成中」のまま待ち、開いたら final_status の先頭の状態で終える。"""
    import asyncio

    from app.database import SessionLocal
    from app.models import ChatMessage
    from app.workflows import chat_persist

    async def fake(job, assistant_id, conv_id, history, params):
        await asyncio.to_thread(gate.wait, 10)
        db = SessionLocal()
        try:
            row = db.get(ChatMessage, assistant_id)
            row.content = "答え: " + history[-1]["content"]
            row.status = final_status.pop(0) if final_status else "completed"
            db.commit()
        finally:
            db.close()
        return {}

    monkeypatch.setattr(chat_persist, "_run_chat_job", fake)


def _messages(client, conv_id):
    return client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]


def _wait(predicate, what: str):
    for _ in range(200):
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(what)


def _send(client, conv_id, text, mode="chat"):
    return client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                       json={"content": text, "mode": mode, "model": "test"})


def _queue(client, conv_id, text, mode="chat"):
    r = client.post(f"/api/v1/chat/conversations/{conv_id}/queue", headers=CSRF_HEADERS,
                    json={"content": text, "mode": mode, "model": "test"})
    assert r.status_code == 201, r.text
    return r.json()


def test_requests_sent_during_a_run_are_sent_together_when_it_finishes(admin_client, monkeypatch):
    gate = threading.Event()
    _fake_runner(monkeypatch, gate, [])
    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    assert _send(admin_client, conv, "最初").status_code == 201
    first = _queue(admin_client, conv, "インダクタンスの最小値は 1 µH")
    second = _queue(admin_client, conv, "電圧の最大値も")
    assert first["dispatched"] is None and second["dispatched"] is None     # 生成中は送らない
    state = admin_client.get(f"/api/v1/chat/conversations/{conv}/queue").json()
    assert [i["content"] for i in state["items"]] == ["インダクタンスの最小値は 1 µH", "電圧の最大値も"]
    assert state["generating_message_id"] and state["held"] is False
    gate.set()                                                              # 回答が終わる
    _wait(lambda: len(_messages(admin_client, conv)) == 4, "送信待ちが送られない")
    _wait(lambda: _messages(admin_client, conv)[-1]["status"] == "completed", "2 つ目の回答が終わらない")
    rows = _messages(admin_client, conv)
    assert rows[2]["role"] == "user" and rows[2]["content"] == "インダクタンスの最小値は 1 µH\n\n電圧の最大値も"
    assert admin_client.get(f"/api/v1/chat/conversations/{conv}/queue").json()["items"] == []
    # 送った後は編集・削除できない
    r = admin_client.patch(f"/api/v1/chat/conversations/{conv}/queue/{first['item']['id']}", headers=CSRF_HEADERS,
                           json={"content": "x"})
    assert r.status_code == 409


def test_a_different_way_of_sending_waits_for_the_next_turn(admin_client, monkeypatch):
    gate = threading.Event()
    _fake_runner(monkeypatch, gate, [])
    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    _send(admin_client, conv, "最初")
    _queue(admin_client, conv, "チャットで")
    _queue(admin_client, conv, "Web で調べて", mode="web")
    gate.set()
    _wait(lambda: len(_messages(admin_client, conv)) == 6, "2 件が順に送られない")
    _wait(lambda: _messages(admin_client, conv)[-1]["status"] == "completed", "最後の回答が終わらない")
    users = [m["content"] for m in _messages(admin_client, conv) if m["role"] == "user"]
    assert users == ["最初", "チャットで", "Web で調べて"]


def test_after_a_stopped_run_the_queue_is_held_until_sent_now(admin_client, monkeypatch):
    gate = threading.Event()
    _fake_runner(monkeypatch, gate, ["canceled"])
    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    _send(admin_client, conv, "最初")
    _queue(admin_client, conv, "続き")
    gate.set()
    _wait(lambda: _messages(admin_client, conv)[-1]["status"] == "canceled", "止まらない")
    time.sleep(0.8)                                                          # 自動では送らない
    state = admin_client.get(f"/api/v1/chat/conversations/{conv}/queue").json()
    assert len(_messages(admin_client, conv)) == 2 and state["held"] is True
    r = admin_client.post(f"/api/v1/chat/conversations/{conv}/queue/dispatch", headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    _wait(lambda: len(_messages(admin_client, conv)) == 4, "今すぐ送るで送られない")


def test_edit_delete_restore_and_clear(admin_client, monkeypatch):
    gate = threading.Event()
    _fake_runner(monkeypatch, gate, [])
    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    _send(admin_client, conv, "最初")
    a = _queue(admin_client, conv, "一つ目")["item"]["id"]
    b = _queue(admin_client, conv, "二つ目")["item"]["id"]
    base = f"/api/v1/chat/conversations/{conv}/queue"
    assert admin_client.patch(f"{base}/{a}", headers=CSRF_HEADERS, json={"content": "一つ目（直した）"}).status_code == 200
    assert admin_client.delete(f"{base}/{b}", headers=CSRF_HEADERS).status_code == 200
    assert [i["content"] for i in admin_client.get(base).json()["items"]] == ["一つ目（直した）"]
    assert admin_client.post(f"{base}/{b}/restore", headers=CSRF_HEADERS).status_code == 200   # 元に戻す（順番も元どおり）
    assert [i["id"] for i in admin_client.get(base).json()["items"]] == [a, b]
    cleared = admin_client.delete(base, headers=CSRF_HEADERS).json()["ids"]
    assert cleared == [a, b] and admin_client.get(base).json()["items"] == []
    gate.set()
    _wait(lambda: _messages(admin_client, conv)[-1]["status"] == "completed", "終わらない")
    time.sleep(0.8)
    assert len(_messages(admin_client, conv)) == 2                         # 取り消したものは送らない


def test_a_request_queued_when_nothing_runs_is_sent_at_once(admin_client, monkeypatch):
    gate = threading.Event()
    gate.set()
    _fake_runner(monkeypatch, gate, [])
    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = _queue(admin_client, conv, "すぐ送って")
    assert r["dispatched"] and r["dispatched"]["user_message_id"]


def test_the_asr_reads_only_the_spoken_length():
    """whisper は 30 秒に詰め物して読む。短い区切りは読む範囲を絞って速くする（6 秒で 14〜17 秒 → 約 3 秒。実測）。"""
    from app.workflows.asr import speed_args

    short = speed_args(44 + 32000 * 6)
    assert short[:2] == ["-t", short[1]] and int(short[1]) <= 8
    assert "-ac" in short and 400 <= int(short[short.index("-ac") + 1]) < 1500
    assert "-ac" not in speed_args(44 + 32000 * 30)                       # 長いものは絞らない


def test_selftest_warns_when_a_finished_conversation_still_has_waiting_requests(admin_client):
    """送り損ね（回答は正常に終わったのに、送信待ちが 2 分以上残る）に自己診断で気付けるように。"""
    from datetime import timedelta

    from app.database import SessionLocal
    from app.diagnostics import selftest
    from app.models import ChatMessage, ChatQueuedRequest, utcnow

    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    db = SessionLocal()
    try:
        db.add(ChatMessage(id="qa" + conv[:20], conversation_id=conv, role="assistant", content="済", status="completed"))
        db.add(ChatQueuedRequest(id="qq" + conv[:20], conversation_id=conv, content="残った", body_json="{}",
                                 created_at=utcnow() - timedelta(minutes=5)))
        db.commit()
    finally:
        db.close()
    status, text = selftest._chat_queue()
    assert status == "warn" and "1 会話" in text
    admin_client.delete(f"/api/v1/chat/conversations/{conv}/queue", headers=CSRF_HEADERS)
    assert selftest._chat_queue()[0] is True


def test_review_fixes_hold_restore_and_a_failing_group_does_not_block_the_rest(admin_client, monkeypatch):
    """レビュー指摘（2026-11-18）:
    - 画面が前の発言を送っている途中（自動判定）に貯めた要望は、すぐには送らない（hold。順番が入れ替わっていた）
    - 送れないまとまりは「送れなかった」にして、後ろの要望は送る（先頭が詰まって永久に残っていた）
    - 回答が終わった後に戻した要望は、そのまま送る（保留のまま残っていた）"""
    from app.workflows import chat_persist

    gate = threading.Event()
    gate.set()
    _fake_runner(monkeypatch, gate, [])
    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    base = f"/api/v1/chat/conversations/{conv}/queue"
    held = admin_client.post(base, headers=CSRF_HEADERS, json={"content": "後で", "mode": "chat", "model": "test", "hold": True}).json()
    assert held["dispatched"] is None
    admin_client.delete(base, headers=CSRF_HEADERS)

    real = chat_persist._start_send

    async def picky(db, conv_row, body, owner):
        if body.content.startswith("だめ"):
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="送れない内容")
        return await real(db, conv_row, body, owner)

    monkeypatch.setattr(chat_persist, "_start_send", picky)
    bad = admin_client.post(base, headers=CSRF_HEADERS, json={"content": "だめな要望", "mode": "chat", "model": "test", "hold": True}).json()
    admin_client.post(base, headers=CSRF_HEADERS, json={"content": "よい要望", "mode": "web", "model": "test", "hold": True})
    assert admin_client.post(f"{base}/dispatch", headers=CSRF_HEADERS).status_code == 200
    _wait(lambda: any(m["content"] == "よい要望" for m in _messages(admin_client, conv)), "後ろの要望が送られない")
    items = admin_client.get(base).json()["items"]
    assert [(i["id"], i["status"], i["error"]) for i in items] == [(bad["item"]["id"], "failed", "送れない内容")]
    _wait(lambda: _messages(admin_client, conv)[-1]["status"] == "completed", "終わらない")

    # 回答が終わった後に戻した要望は、そのまま送る
    later = admin_client.post(base, headers=CSRF_HEADERS, json={"content": "戻す要望", "mode": "chat", "model": "test", "hold": True}).json()
    admin_client.delete(f"{base}/{later['item']['id']}", headers=CSRF_HEADERS)
    assert admin_client.post(f"{base}/{later['item']['id']}/restore", headers=CSRF_HEADERS).status_code == 200
    _wait(lambda: any(m["content"] == "戻す要望" for m in _messages(admin_client, conv)), "戻した要望が送られない")


def test_a_failure_after_writing_the_message_does_not_leave_it_generating(admin_client, monkeypatch):
    """発言を書いた後でジョブを作れなかったら「生成中」のまま残さない（残ると送信待ちも OpenCode も止まる）。"""
    from app.jobs import service as jobs

    def broken(*args, **kwargs):
        raise ValueError("idempotency")

    monkeypatch.setattr(jobs, "create", broken)
    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv}/queue", headers=CSRF_HEADERS,
                          json={"content": "送る", "mode": "chat", "model": "test"})
    assert r.status_code == 201 and r.json()["dispatched"] is None
    rows = _messages(admin_client, conv)
    assert rows[-1]["role"] == "assistant" and rows[-1]["status"] == "failed"
    state = admin_client.get(f"/api/v1/chat/conversations/{conv}/queue").json()
    assert state["generating_message_id"] is None and state["items"][0]["status"] == "failed"


def test_groups_respect_the_attachment_budget():
    """まとめると添付が 8 個を超えるなら、そこで分ける（黙って落としていた）。"""
    import json as _json

    from app.models import ChatQueuedRequest
    from app.workflows.chat_persist import _groups

    def item(i, n):
        return ChatQueuedRequest(id=f"g{i}", conversation_id="c", content=f"要望{i}",
                                 body_json=_json.dumps({"mode": "chat", "model": "m", "attachments": [f"{i}{k}" for k in range(n)]}))

    groups = _groups([item(1, 4), item(2, 4), item(3, 4)])
    assert [[i.id for i in g] for g in groups] == [["g1", "g2"], ["g3"]]
