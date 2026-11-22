"""作業場の会話の「保存と中断・再開」の chat 側（2026-11-22）。

目的: 作業場（開発用の箱）の OpenCode の回が終わるたび（成功・失敗・止めた、どれでも）自動保存が呼ばれること。
使用量の上限・残高切れ・レート制限で失敗したら、モデルの順番（chain）の次へセッションを切り替えて同じ依頼を
再送し、会話に 1 行「〜が使えなくなったので〜で続けます」が出ること。中断・止めた回を「続きから」で再開
できること（同じセッションへ・前回の理由と ToDo を本文へ渡す）。
根拠: 2026-11-22 利用者「作業場のコードはどういうタイミングで実際に保存されるのか。途中で停電や再起動した
場合はどうなるのか。中断して再開できるのか。他にも考えられる割り込みのシチュエーションを想定した上で、
問題がないように実装してほしい」。OpenCode・会話サーバー・autosave は偽物（記録だけ）。
"""
import json
import time

from tests.conftest import CSRF_HEADERS

CLEAN = {"committed": False, "count": 0, "skipped": "clean", "free_gib": 99.0, "error": ""}


def _server(tmp_path, workspace="ws1"):
    from app.integrations.opencode import chat_server

    return chat_server.Server(key="wspersist01", port=1, password="p", unit="u", config=str(tmp_path),
                              project=str(tmp_path), permission_file=str(tmp_path / "perm.json"),
                              started_at=0, last_used=0, workspace=workspace)


def _fake(monkeypatch, tmp_path, turn, *, autosave_results=None, chain=None, switch=None):
    """作業場の会話サーバーと OpenCode・自動保存を偽物へ替え、呼び出しを記録して返す。"""
    from app.integrations.opencode import chat_server
    from app.maintenance import sandbox as _sandbox, workspaces
    from app.models_mgmt import local_llm

    server = _server(tmp_path)
    calls: dict = {}

    async def fake_resolve(name="", path=""):
        return tmp_path

    async def fake_ensure(project, owner):
        return server

    async def fake_exists(srv, sid):
        return True

    async def fake_create(srv, title):
        return "ses_new"

    async def fake_ready(url):
        return None

    async def fake_turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission,
                        agent="build", on_status=None):
        calls.setdefault("turns", []).append((sid, text))
        return await turn(on_text)

    monkeypatch.setattr(chat_server, "resolve_project", fake_resolve)
    monkeypatch.setattr(chat_server, "ensure", fake_ensure)
    monkeypatch.setattr(chat_server, "session_exists", fake_exists)
    monkeypatch.setattr(chat_server, "create_session", fake_create)
    monkeypatch.setattr(chat_server, "run_turn", fake_turn)
    monkeypatch.setattr(chat_server, "get_mode", lambda srv: "auto")
    monkeypatch.setattr(local_llm, "ensure_ready_by_base_url", fake_ready)

    autosaves = list(autosave_results or [])

    def fake_autosave(box, conv_id=""):
        calls.setdefault("autosave", []).append(conv_id)
        return autosaves.pop(0) if autosaves else CLEAN

    monkeypatch.setattr(workspaces, "autosave", fake_autosave)
    monkeypatch.setattr(_sandbox, "load", lambda name: None)
    monkeypatch.setattr(_sandbox, "model_chain", lambda b: list(chain or []))
    if chain is not None:
        monkeypatch.setattr(_sandbox, "model_chain", lambda b: chain)

        async def fake_switch(srv, sid, model):
            if switch is not None:
                await switch(srv, sid, model)
            calls.setdefault("switched", []).append(model)

        monkeypatch.setattr(chat_server, "switch_model", fake_switch)
    return server, calls


async def _ok(on_text):
    await on_text("直しました。")
    return {"status": "succeeded", "error": ""}


def _new_conversation(admin_client):
    return admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]


def _send(admin_client, conv_id, content="直して"):
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                          json={"content": content, "mode": "code", "model": "test"})
    assert r.status_code == 201, r.text
    return r.json()["assistant_message_id"]


def _wait_done(admin_client, conv_id, aid):
    for _ in range(200):
        msgs = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
        found = next(m for m in msgs if m["id"] == aid)
        if found["status"] != "generating":
            return found
        time.sleep(0.05)
    raise AssertionError("終わらない")


# ---- ターンの終わりの自動保存 ----

def test_workspace_turn_autosaves_on_success(admin_client, monkeypatch, tmp_path):
    server, calls = _fake(monkeypatch, tmp_path, _ok,
                          autosave_results=[{"committed": True, "count": 1, "skipped": "", "free_gib": 90.0, "error": ""}])
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert final["status"] == "completed" and final["content"] == "直しました。"
    assert len(calls["autosave"]) == 1, "成功した回の終わりに 1 回"


def test_workspace_turn_autosaves_even_when_interrupted(admin_client, monkeypatch, tmp_path):
    async def turn(on_text):
        await on_text("途中まで")
        return {"status": "interrupted", "error": ""}

    server, calls = _fake(monkeypatch, tmp_path, turn, autosave_results=[CLEAN])
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert final["status"] == "canceled"
    assert len(calls["autosave"]) == 1, "止めた回でも保存する"


def test_workspace_turn_autosaves_when_the_turn_crashes(admin_client, monkeypatch, tmp_path):
    async def turn(on_text):
        raise RuntimeError("OpenCode が落ちました")

    server, calls = _fake(monkeypatch, tmp_path, turn, autosave_results=[CLEAN])
    conv_id = _new_conversation(admin_client)
    aid = _send(admin_client, conv_id)
    final = _wait_done(admin_client, conv_id, aid)
    assert final["status"] == "failed"
    assert len(calls["autosave"]) == 1, "例外でも保存する"


def test_non_workspace_turns_do_not_autosave(admin_client, monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    server, calls = _fake(monkeypatch, tmp_path, _ok)
    server.workspace = ""        # 普通のプロジェクトの箱
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert final["status"] == "completed"
    assert "autosave" not in calls, "作業場でなければ呼ばない"


def test_workspace_turn_reports_disk_shortage_in_the_reply(admin_client, monkeypatch, tmp_path):
    server, calls = _fake(monkeypatch, tmp_path, _ok, autosave_results=[
        {"committed": False, "count": 0, "skipped": "disk", "free_gib": 5.0, "error": ""}])
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert "ディスクの空きが少ないので保存できません" in final["content"]
    assert "5 GiB" in final["content"], "残りの空きを添える"


# ---- モデルの切り替わり ----

def test_rate_limit_failure_switches_to_the_next_model_and_resends(admin_client, monkeypatch, tmp_path):
    state = {"n": 0}

    async def turn(on_text):
        state["n"] += 1
        if state["n"] == 1:
            return {"status": "failed", "error": "rate limit exceeded: 429"}
        await on_text("続けました。")
        return {"status": "succeeded", "error": ""}

    server, calls = _fake(monkeypatch, tmp_path, turn,
                         chain=["opencode-go/glm-5.3", "opencode/hy4-flash-free"])
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert final["status"] == "completed" and "続けました。" in final["content"]
    assert calls["switched"] == ["opencode/hy4-flash-free"], "順番の次のモデルへ切り替える"
    assert len(calls["turns"]) == 2, "同じ依頼をもう一度"
    assert "glm-5.3 が使えなくなったので hy4-flash-free で続けます" in final["content"], "会話に 1 行"
    assert "429" in final["content"], "理由も添える"
    assert final["meta"]["model_index"] == 1, "次のターンは切り替え後から"


def test_model_switch_resumes_from_the_last_index_of_previous_turns(admin_client, monkeypatch, tmp_path):
    """前の回で chain[2] まで進めていれば、この回で上限が起きたら chain[3] へ（前の分をやり直さない）。"""
    from app.database import SessionLocal
    from app.models import ChatMessage

    state = {"n": 0}

    async def turn(on_text):
        state["n"] += 1
        if state["n"] == 1:
            return {"status": "failed", "error": "quota exceeded"}
        await on_text("続けました。")
        return {"status": "succeeded", "error": ""}

    server, calls = _fake(monkeypatch, tmp_path, turn,
                          chain=["m/a", "m/b", "m/c", "m/d"])
    conv_id = _new_conversation(admin_client)
    with SessionLocal() as db:
        db.add(ChatMessage(id="mprev000000001", conversation_id=conv_id, role="assistant", content="前",
                           status="completed", meta_json=json.dumps(
                               {"mode": "code", "opencode_session": "ses_prev", "model_index": 2})))
        db.commit()
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert final["status"] == "completed"
    assert calls["switched"] == ["m/d"], "前の回の 2 の次（3）へ"
    assert "c が使えなくなったので d で続けます" in final["content"]


def test_rate_limit_exhausts_the_chain_and_keeps_the_failure(admin_client, monkeypatch, tmp_path):
    async def turn(on_text):
        return {"status": "failed", "error": "usage limit reached"}

    server, calls = _fake(monkeypatch, tmp_path, turn, chain=["m/a"])
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert final["status"] == "failed"
    assert "switched" not in calls and len(calls["turns"]) == 1, "次が無ければ切り替えない"


def test_other_failures_do_not_switch_models(admin_client, monkeypatch, tmp_path):
    async def turn(on_text):
        return {"status": "failed", "error": "tool approval denied"}

    server, calls = _fake(monkeypatch, tmp_path, turn, chain=["m/a", "m/b"])
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert final["status"] == "failed"
    assert "switched" not in calls and len(calls["turns"]) == 1, "上限以外の失敗は切り替えない"


def test_failed_switch_keeps_the_original_failure(admin_client, monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    async def turn(on_text):
        return {"status": "failed", "error": "rate limit exceeded"}

    async def broken(srv, sid, model):
        raise chat_server.ChatServerError("切り替えられません")

    server, calls = _fake(monkeypatch, tmp_path, turn, chain=["m/a", "m/b"], switch=broken)
    conv_id = _new_conversation(admin_client)
    final = _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert final["status"] == "failed"
    assert len(calls["turns"]) == 1, "切り替えられなければ元の失敗のまま"


# ---- 続きから ----

def test_resume_sends_reason_and_todos_to_the_same_session(admin_client, monkeypatch, tmp_path):
    from app.database import SessionLocal
    from app.models import ChatMessage

    server, calls = _fake(monkeypatch, tmp_path, _ok, autosave_results=[CLEAN])
    conv_id = _new_conversation(admin_client)
    with SessionLocal() as db:
        db.add(ChatMessage(id="m_intr000000001", conversation_id=conv_id, role="assistant", content="途中まで",
                           status="canceled",
                           meta_json=json.dumps({"mode": "code", "project_path": str(tmp_path),
                                                 "opencode_session": "ses_prev",
                                                 "todos": [{"status": "in_progress", "content": "残りを直す"},
                                                           {"status": "completed", "content": "まず測る"}]})))
        db.commit()
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/opencode/resume", headers=CSRF_HEADERS,
                          json={"message_id": "m_intr000000001"})
    assert r.status_code == 201, r.text
    final = _wait_done(admin_client, conv_id, r.json()["assistant_message_id"])
    assert final["meta"]["opencode_session"] == "ses_prev", "セッションが残っていれば同じセッションで続ける"
    sent = calls["turns"][-1][1]
    assert "前回は中断されました（利用者が止めました）" in sent
    assert "git log・git status と ToDo を確かめて、残りを続けてください" in sent
    assert "## 前回の ToDo" in sent and "残りを直す" in sent and "まず測る" in sent
    assert final["status"] == "completed"
    assert calls["autosave"], "続きからも通常のターンとして自動保存する"
    with SessionLocal() as db:
        row = db.get(ChatMessage, "m_intr000000001")
        assert json.loads(row.meta_json or "{}").get("resumed") is True, \
            "再開し終えた回へ印を付ける（画面が二重に出さない）"


def test_resume_of_a_failed_turn_carries_the_error(admin_client, monkeypatch, tmp_path):
    from app.database import SessionLocal
    from app.models import ChatMessage

    server, calls = _fake(monkeypatch, tmp_path, _ok, autosave_results=[CLEAN])
    conv_id = _new_conversation(admin_client)
    with SessionLocal() as db:
        db.add(ChatMessage(id="m_fail000000001", conversation_id=conv_id, role="assistant", content="",
                           status="failed", error="モデルが落ちました",
                           meta_json=json.dumps({"mode": "code", "project_path": str(tmp_path),
                                                 "opencode_session": "ses_prev"})))
        db.commit()
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/opencode/resume", headers=CSRF_HEADERS,
                          json={"message_id": "m_fail000000001"})
    assert r.status_code == 201, r.text
    final = _wait_done(admin_client, conv_id, r.json()["assistant_message_id"])
    assert "前回は中断されました（モデルが落ちました）" in calls["turns"][-1][1]


def test_resume_refuses_when_there_is_no_unsettled_opencode_turn(admin_client, monkeypatch, tmp_path):
    from app.database import SessionLocal
    from app.models import ChatMessage

    server, calls = _fake(monkeypatch, tmp_path, _ok)
    conv_id = _new_conversation(admin_client)
    with SessionLocal() as db:
        db.add(ChatMessage(id="m_done000000001", conversation_id=conv_id, role="assistant", content="済み",
                           status="completed", meta_json=json.dumps({"mode": "code", "project_path": str(tmp_path),
                                                                    "opencode_session": "ses_prev"})))
        db.add(ChatMessage(id="m_chat000000001", conversation_id=conv_id, role="assistant", content="",
                           status="failed", meta_json=json.dumps({"mode": "chat"})))
        db.commit()
    base = f"/api/v1/chat/conversations/{conv_id}/opencode/resume"
    r = admin_client.post(base, headers=CSRF_HEADERS, json={"message_id": "m_done000000001"})
    assert r.status_code == 409 and "中断・停止・失敗していません" in r.json()["detail"]
    r = admin_client.post(base, headers=CSRF_HEADERS, json={"message_id": "m_chat000000001"})
    assert r.status_code == 409 and "OpenCode の回ではありません" in r.json()["detail"]
    assert admin_client.post(base, headers=CSRF_HEADERS, json={}).status_code == 409
    assert admin_client.post(base, headers=CSRF_HEADERS,
                            json={"message_id": "m_nobody0000001"}).status_code == 404


def test_resume_picks_the_latest_unsettled_opencode_turn_without_an_id(admin_client, monkeypatch, tmp_path):
    from app.database import SessionLocal
    from app.models import ChatMessage

    server, calls = _fake(monkeypatch, tmp_path, _ok, autosave_results=[CLEAN])
    conv_id = _new_conversation(admin_client)
    with SessionLocal() as db:
        db.add(ChatMessage(id="m_old0000000001", conversation_id=conv_id, role="assistant", content="",
                           status="interrupted", meta_json=json.dumps({"mode": "code", "project_path": str(tmp_path),
                                                                     "opencode_session": "ses_old"})))
        db.flush()
        time.sleep(0.01)
        db.add(ChatMessage(id="m_new0000000001", conversation_id=conv_id, role="assistant", content="",
                           status="interrupted", meta_json=json.dumps({"mode": "code", "project_path": str(tmp_path),
                                                                     "opencode_session": "ses_new"})))
        db.commit()
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/opencode/resume", headers=CSRF_HEADERS, json={})
    assert r.status_code == 201, r.text
    final = _wait_done(admin_client, conv_id, r.json()["assistant_message_id"])
    assert final["meta"]["opencode_session"] == "ses_new", "ID を省いたら最新の中断の回"


# ---- Claude による直し（2026-11-22 レビュー）----

def test_model_index_is_not_carried_into_a_new_session(admin_client, monkeypatch, tmp_path):
    """セッションを作り直した（前のが無い）ときのモデルは既定へ戻っている。前の回の chain の位置を引きずらない
    （引きずると、最初の上限で 2 つ先のモデルへ飛ぶ）。"""
    from app.database import SessionLocal
    from app.integrations.opencode import chat_server
    from app.models import ChatMessage

    state = {"n": 0}

    async def turn(on_text):
        state["n"] += 1
        if state["n"] == 1:
            return {"status": "failed", "error": "quota exceeded"}
        return {"status": "succeeded", "error": ""}

    server, calls = _fake(monkeypatch, tmp_path, turn, chain=["m/a", "m/b", "m/c", "m/d"])

    async def gone(srv, sid):
        return False

    monkeypatch.setattr(chat_server, "session_exists", gone)
    conv_id = _new_conversation(admin_client)
    with SessionLocal() as db:
        db.add(ChatMessage(id="mprev000000002", conversation_id=conv_id, role="assistant", content="前",
                           status="completed", meta_json=json.dumps(
                               {"mode": "code", "opencode_session": "ses_old", "model_index": 2})))
        db.commit()
    _wait_done(admin_client, conv_id, _send(admin_client, conv_id))
    assert calls["switched"] == ["m/b"], "新しいセッションは chain の先頭から数える"


def test_resume_cannot_be_done_twice_for_the_same_turn(admin_client, monkeypatch, tmp_path):
    from app.database import SessionLocal
    from app.models import ChatMessage

    server, calls = _fake(monkeypatch, tmp_path, _ok, autosave_results=[CLEAN])
    conv_id = _new_conversation(admin_client)
    with SessionLocal() as db:
        db.add(ChatMessage(id="m_twice00000001", conversation_id=conv_id, role="assistant", content="途中",
                           status="interrupted",
                           meta_json=json.dumps({"mode": "code", "project_path": str(tmp_path),
                                                 "opencode_session": "ses_prev"})))
        db.commit()
    url = f"/api/v1/chat/conversations/{conv_id}/opencode/resume"
    first = admin_client.post(url, headers=CSRF_HEADERS, json={"message_id": "m_twice00000001"})
    assert first.status_code == 201, first.text
    _wait_done(admin_client, conv_id, first.json()["assistant_message_id"])
    again = admin_client.post(url, headers=CSRF_HEADERS, json={"message_id": "m_twice00000001"})
    assert again.status_code == 409 and "すでに" in again.json()["detail"]
    assert len(calls["turns"]) == 1, "二重に送らない"
