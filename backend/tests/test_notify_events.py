"""通知の入口（種類の有効・無効・お休みモード）と、各検出の所からの呼び出し。

目的:
- 種類を切った人には送らない。既定（chat_done は無効）どおりに動く。
- お休みモード（既定 22:00〜07:00）の間は送らない。日またぎ・境界（22:00 は休み、07:00 は送る）・無効・開始=終了。
- 緊急の監視の警報（urgent）は、お休み中でも「緊急は送る」を有効にしたときだけ送る（既定は無効）。
- 設定の API は本人の分だけ読み書きできる（他人の行に触れない）。不正な種類・時刻は 422。
- 会話の回の終わり（コード開発・質問待ち・失敗・調査・長い会話）と、起動時の中断・待ちの通知が入口へ届く。
- 秘密の会話は、題も要約も載せない。要約にパス・鍵・URL を載せない。
- 実際の Web Push は外へ送らない（webpush.send_in_background / send_to を差し替える）。
根拠: 2026-11-23 利用者の依頼（お休みモード・通知の種類の拡充）。Push は Apple・Google の push service を通るため、秘密を載せない。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.alerts import chat_events, events, webpush
from app.database import SessionLocal
from app.models import ChatMessage, Conversation, User
from tests.conftest import CSRF_HEADERS


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "_path", lambda: tmp_path / "prefs.json")
    events._last_sent.clear()


@pytest.fixture()
def sent(monkeypatch):
    out: list[dict] = []
    monkeypatch.setattr(webpush, "send_in_background", lambda **kw: out.append(kw))
    monkeypatch.setattr(webpush, "send_to", lambda **kw: out.append(kw) or {"sent": 1, "removed": 0, "failed": 0})
    return out


def at(hour: int, minute: int = 0):
    return lambda: datetime(2026, 11, 23, hour, minute)


def test_disabled_kind_is_not_sent_and_defaults_apply(sent):
    events.notify_event("code_done", title="t", user_ids=[7])
    assert len(sent) == 1 and sent[0]["user_ids"] == [7]
    events.save_prefs(7, kinds={"code_done": False})
    events.notify_event("code_done", title="t", user_ids=[7])
    events.notify_event("code_done", title="t", user_ids=[8])      # 別の人の設定は影響しない
    assert [item["user_ids"] for item in sent] == [[7], [8]]
    events.notify_event("chat_done", title="t", user_ids=[7])      # 既定が無効の種類
    assert len(sent) == 2
    with pytest.raises(ValueError):
        events.notify_event("nope", title="t", user_ids=[7])


@pytest.mark.parametrize(("hour", "minute", "quiet"), [
    (21, 59, False), (22, 0, True), (23, 30, True), (0, 0, True), (6, 59, True), (7, 0, False), (12, 0, False)])
def test_quiet_hours_cross_midnight_and_boundaries(sent, monkeypatch, hour, minute, quiet):
    monkeypatch.setattr(events, "local_now", at(hour, minute))
    events.notify_event("code_done", title="t", user_ids=[7])
    assert (len(sent) == 0) is quiet


def test_quiet_hours_custom_same_day_range_and_off(sent, monkeypatch):
    events.save_prefs(7, quiet={"start": "12:00", "end": "13:00"})
    monkeypatch.setattr(events, "local_now", at(12, 30))
    events.notify_event("code_done", title="t", user_ids=[7])
    assert not sent
    assert events.quiet_state(events.get_prefs(7)["quiet"], at(12, 30)()) == {"active": True, "until": "13:00"}
    events.save_prefs(7, quiet={"enabled": False})
    events.notify_event("code_done", title="t", user_ids=[7])
    assert len(sent) == 1


def test_urgent_alert_passes_quiet_hours_only_when_allowed(sent, monkeypatch):
    monkeypatch.setattr(events, "local_now", at(23))
    events.notify_event("alert", title="t", user_ids=[7], urgent=True)          # 既定はお休み中は緊急も送らない
    assert len(sent) == 0
    events.save_prefs(7, quiet={"urgent_alerts": True})
    events.notify_event("alert", title="t", user_ids=[7], urgent=True)
    events.notify_event("alert", title="t", user_ids=[7])                       # 緊急でなければ止まる
    assert len(sent) == 1
    events.save_prefs(7, kinds={"alert": False}, quiet={"urgent_alerts": True})  # 種類を切れば緊急でも送らない
    events.notify_event("alert", title="t", user_ids=[7], urgent=True)
    assert len(sent) == 1


def test_cooldown_sends_same_tag_once(sent):
    for _ in range(3):
        events.notify_event("model_error", title="t", user_ids=[1], tag="m", cooldown=600)
    assert len(sent) == 1


def test_settings_api_is_own_only_and_validates(admin_client):
    admin_id = None
    with SessionLocal() as db:
        admin_id = db.query(User).filter(User.username == "admin").one().id
    view = admin_client.get("/api/v1/notification-settings").json()
    assert view["quiet"] == {"enabled": True, "start": "22:00", "end": "07:00", "urgent_alerts": False}
    assert {k["id"]: k["enabled"] for k in view["kinds"]}["chat_done"] is False
    put = admin_client.put("/api/v1/notification-settings", headers=CSRF_HEADERS,
                           json={"kinds": {"code_done": False}, "quiet": {"start": "23:00"}})
    assert put.status_code == 200 and put.json()["quiet"]["start"] == "23:00"
    assert events.get_prefs(admin_id)["kinds"]["code_done"] is False
    assert events.get_prefs(admin_id + 1000)["kinds"]["code_done"] is True        # 他人の分は変わらない
    for bad in ({"kinds": {"nope": True}}, {"quiet": {"start": "25:00"}}, {"quiet": {"start": "07:00", "end": "07:00"}}):
        assert admin_client.put("/api/v1/notification-settings", headers=CSRF_HEADERS, json=bad).status_code == 422


def _conversation(title="ログイン画面の直し", *, secret=False, mode="code", agent="build", content="login.py を直しました", status="completed"):
    from app.models import utcnow

    with SessionLocal() as db:
        uid = db.query(User).filter(User.username == "admin").one().id
        conv = Conversation(id=f"c{datetime.now().timestamp():.6f}"[:32].replace(".", ""), title=title, owner_user_id=uid,
                            confidential_at=utcnow() if secret else None)
        db.add(conv)
        db.flush()
        msg = ChatMessage(id=conv.id + "m", conversation_id=conv.id, role="assistant", content=content, status=status,
                          meta_json=json.dumps({"mode": mode, "agent": agent}), job_id=conv.id + "j")
        db.add(msg)
        db.commit()
        return conv.id, msg.id, uid


def test_turn_end_calls_entry_with_kind_and_conversation_url(sent):
    conv, msg, uid = _conversation()
    chat_events.announce_turn(conv, msg, "code", failed=False, seconds=5)
    assert sent[0]["user_ids"] == [uid] and sent[0]["url"] == f"/assistant?conversation={conv}"
    assert sent[0]["title"] == "ログイン画面の直し" and "コード開発が完了しました" in sent[0]["body"]
    chat_events.announce_turn(conv, msg, "code", failed=True, seconds=5)
    assert "失敗" in sent[1]["body"]
    plan_conv, plan_msg, _ = _conversation(agent="plan", content="## 決めてもらいたいこと\n1. どちら？")
    chat_events.announce_turn(plan_conv, plan_msg, "code", failed=False, seconds=5)
    assert "決めてもらいたいこと" in sent[2]["body"]
    research, research_msg, _ = _conversation(mode="web")
    chat_events.announce_turn(research, research_msg, "web", failed=False, seconds=5)
    assert "調査が完了" in sent[3]["body"]


def test_plain_chat_is_silent_by_default_and_only_when_long(sent):
    conv, msg, uid = _conversation(mode="chat")
    chat_events.announce_turn(conv, msg, "chat", failed=False, seconds=200)       # 既定は無効
    events.save_prefs(uid, kinds={"chat_done": True})
    chat_events.announce_turn(conv, msg, "chat", failed=False, seconds=10)        # 短い
    assert not sent
    chat_events.announce_turn(conv, msg, "chat", failed=False, seconds=61)
    assert len(sent) == 1


def test_canceled_turn_is_not_announced(sent):
    conv, msg, _ = _conversation(status="canceled")
    chat_events.announce_turn(conv, msg, "code", failed=False, seconds=5)
    assert not sent


def test_secret_conversation_and_summary_carry_no_content(sent):
    conv, msg, _ = _conversation(title="特許 請求項の草案", secret=True, content="特許の内容を詳しく書きます")
    chat_events.announce_turn(conv, msg, "code", failed=False, seconds=5)
    blob = json.dumps(sent[0], ensure_ascii=False)
    assert "特許" not in blob and "請求項" not in blob and "草案" not in blob
    line = chat_events.one_line(f"# /home/u/secret/app.py を直した {'sk' + '-abcdef1234567890abcdef'} と https://x.example/a")
    assert "/home" not in line and "sk-abc" not in line and "x.example" not in line


def test_waiting_and_startup_interruption_notify(sent):
    conv, msg, uid = _conversation()
    chat_events.announce_waiting(conv, "code_permission")
    chat_events.announce_waiting(conv, "code_permission")       # 30 秒の間は 1 回
    assert len(sent) == 1 and "許可" in sent[0]["body"]
    chat_events.announce_interrupted([conv + "j", "nothing"])
    assert len(sent) == 2 and "中断" in sent[1]["body"]
    chat, chat_msg, _ = _conversation(mode="chat")
    chat_events.announce_interrupted([chat + "j"])               # OpenCode 以外の回は code_interrupted にしない
    assert len(sent) == 2


def test_other_detection_points_go_through_entry(sent, monkeypatch):
    import asyncio

    from app.alerts.notify import send_notification
    from app.maintenance import auto_update

    calls: list[tuple] = []
    monkeypatch.setattr(events, "notify_event", lambda kind, **kw: calls.append((kind, kw.get("urgent"))) or {"users": 0})
    asyncio.run(send_notification("push", "", "t", "m", kind="patent_watch"))
    asyncio.run(send_notification("push", "", "t", "m", kind="alert", urgent=True))
    assert calls == [("patent_watch", False), ("alert", True)]
    from app.models_mgmt import gateway

    gateway._announce_load_failed("m1")
    assert calls[-1][0] == "model_error"
    assert hasattr(auto_update, "notify")
