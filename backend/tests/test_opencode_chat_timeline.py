"""AI アシスタントの OpenCode: 思考・道具・本文を届いた順に記録する／作業フォルダを会話で続ける／箱ごとの /tmp。

目的: 2026-11-18 利用者の依頼。
  - 「思考が全部 1 つの窓に入っている。Bash などの操作の前後で分かれるはず」→ 届いた順の並び（timeline）を記録する
    （思考・本文は文字の位置、道具は ID）。画面はそれで思考を道具の前後に分けて出す
  - 「AI アシスタントを開き直すと作業フォルダの設定が外れる」→ 指定が空なら、その会話で最後に OpenCode が
    動いたプロジェクトで続ける
  - 「proto1〜3 が Project Lab に無い」→ 箱の /tmp は全部の箱で共有されていた。プロジェクトごとに別の、
    箱の外に残る場所へつなぐ
"""
import asyncio
import time
from pathlib import Path

from tests.conftest import CSRF_HEADERS


AGENTS: list[str] = []   # 偽の OpenCode が受け取ったエージェント


def _server(tmp_path):
    from app.integrations.opencode import chat_server

    return chat_server.Server(key="abc123", port=1, password="p", unit="u", config=str(tmp_path),
                              project=str(tmp_path), permission_file=str(tmp_path / "perm.json"),
                              started_at=0, last_used=0)


def _fake_opencode(monkeypatch, tmp_path, resolved: list):
    from app.integrations.opencode import chat_server
    from app.models_mgmt import local_llm

    server = _server(tmp_path)

    async def fake_resolve(name="", path=""):
        resolved.append((name, path))
        return tmp_path

    async def fake_ensure(project, owner):
        return server

    async def fake_exists(srv, sid):
        return False

    async def fake_create(srv, title):
        return "ses_new"

    async def fake_ready(url):
        return None

    async def fake_turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission, agent="build", on_status=None):
        AGENTS.append(agent)
        await on_reasoning("まず")
        await on_reasoning("見る。")
        await on_tool({"id": "call_1", "name": "shell", "status": "running", "input": {"command": "ls"}, "output": ""})
        await on_tool({"id": "call_1", "name": "shell", "status": "completed", "input": {}, "output": "a.py"})
        await on_reasoning("a.py を直す。")
        await on_tool({"id": "call_2", "name": "edit", "status": "completed", "input": {}, "output": ""})
        await on_text("直しました。")
        return {"status": "succeeded", "error": ""}

    monkeypatch.setattr(chat_server, "resolve_project", fake_resolve)
    monkeypatch.setattr(chat_server, "ensure", fake_ensure)
    monkeypatch.setattr(chat_server, "session_exists", fake_exists)
    monkeypatch.setattr(chat_server, "create_session", fake_create)
    monkeypatch.setattr(chat_server, "run_turn", fake_turn)
    monkeypatch.setattr(local_llm, "ensure_ready_by_base_url", fake_ready)


def _wait_done(admin_client, conv_id, aid):
    for _ in range(100):
        msgs = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
        found = next(m for m in msgs if m["id"] == aid)
        if found["status"] != "generating":
            return found
        time.sleep(0.05)
    raise AssertionError("終わらない")


def test_thinking_tools_and_text_are_recorded_in_arrival_order(admin_client, monkeypatch, tmp_path):
    resolved: list = []
    _fake_opencode(monkeypatch, tmp_path, resolved)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                          json={"content": "直して", "mode": "code", "model": "test", "code_project": "demo"})
    final = _wait_done(admin_client, conv_id, r.json()["assistant_message_id"])
    assert final["thinking"] == "まず見る。a.py を直す。" and final["content"] == "直しました。"
    # 思考は 2 つのまとまり（道具の前と後）。同じ道具の更新は 1 回だけ並ぶ
    assert final["meta"]["timeline"] == [
        {"k": "t", "from": 0, "to": 5}, {"k": "u", "id": "call_1"},
        {"k": "t", "from": 5, "to": 14}, {"k": "u", "id": "call_2"},
        {"k": "x", "from": 0, "to": 6},
    ]
    thinking = final["thinking"]
    assert thinking[0:5] == "まず見る。" and thinking[5:14] == "a.py を直す。"


def test_changing_the_project_starts_a_new_session_and_hands_over(admin_client, monkeypatch, tmp_path):
    """2026-11-18 本番: mokei を選んでも conv のセッションのまま、箱の中の空の conv で作業していた。"""
    import io

    from pypdf import PdfWriter

    from app.integrations.opencode import chat_server

    _fake_opencode(monkeypatch, tmp_path, [])
    first, second = tmp_path / "conv", tmp_path / "mokei"
    first.mkdir()
    second.mkdir()
    created: list[str] = []
    turns: list[tuple[str, str]] = []

    async def resolve(name="", path=""):
        return tmp_path / name

    async def exists(srv, sid):
        return True

    async def create(srv, title):
        created.append(f"ses_{len(created)}")
        return created[-1]

    async def turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission, agent="build", on_status=None):
        turns.append((sid, text))
        await on_text("前の回答です。")
        return {"status": "succeeded", "error": ""}

    monkeypatch.setattr(chat_server, "resolve_project", resolve)
    monkeypatch.setattr(chat_server, "session_exists", exists)
    monkeypatch.setattr(chat_server, "create_session", create)
    monkeypatch.setattr(chat_server, "run_turn", turn)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=100)
    pdf = io.BytesIO()
    writer.write(pdf)
    uploaded = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/attachments", headers=CSRF_HEADERS,
                                 files={"file": ("Plan15.pdf", pdf.getvalue(), "application/pdf")})
    assert uploaded.status_code == 201 and uploaded.json()["pages"] == 1 and uploaded.json()["note"]   # 画像だけでも受ける
    for project, text in (("conv", "作って"), ("conv", "続けて"), ("mokei", "こちらで"), ("conv", "戻って")):
        r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                              json={"content": text, "mode": "code", "model": "test", "code_project": project,
                                    "code_agent": "plan"})
        final = _wait_done(admin_client, conv_id, r.json()["assistant_message_id"])
        assert final["status"] == "completed", final["error"]
    assert [sid for sid, _ in turns] == ["ses_0", "ses_0", "ses_1", "ses_0"]     # フォルダごとにセッションを続ける
    assert "## 添付された PDF" in turns[0][1] and "## 添付された PDF" not in turns[1][1]
    moved = turns[2][1]
    assert "今回の作業フォルダは" in moved and "前の回答です。" in moved and "## 添付された PDF" in moved and "## プラン" in moved
    assert list((second / ".controldeck/attachments").iterdir())


def test_the_chosen_mode_is_kept_on_the_sent_message(admin_client, monkeypatch, tmp_path):
    """別の端末や開き直しでも、機能の固定を送った発言から戻せる。"""
    _fake_opencode(monkeypatch, tmp_path, [])
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                          json={"content": "直して", "mode": "code", "model": "test", "code_project": "demo",
                                "mode_choice": "code", "research_depth": "academic", "agent_choice": "plan"})
    _wait_done(admin_client, conv_id, r.json()["assistant_message_id"])
    messages = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
    meta = next(m for m in messages if m["role"] == "user")["meta"]
    assert meta["mode_choice"] == "code" and meta["research_depth"] == "academic" and meta["agent_choice"] == "plan"


def test_changes_are_recorded_and_the_latest_turn_can_be_reverted(admin_client, monkeypatch, tmp_path):
    """回答ごとの変更・差分・取り消し。取り消せるのは最新の回だけ。"""
    from app.integrations.opencode import chat_server

    _fake_opencode(monkeypatch, tmp_path, [])
    reverted: list[tuple[str, str]] = []
    count = {"n": 0}

    async def turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission, agent="build", on_status=None):
        count["n"] += 1
        await on_text("直しました")
        return {"status": "succeeded", "error": "", "message_id": f"msg_{count['n']}"}

    async def changes(srv, sid, message_id):
        return [{"file": "a.py", "patch": "@@ -1 +1 @@\n-a\n+b\n", "additions": 1, "deletions": 1, "status": "modified"}]

    async def revert(srv, sid, message_id):
        reverted.append((sid, message_id))

    monkeypatch.setattr(chat_server, "run_turn", turn)
    monkeypatch.setattr(chat_server, "turn_changes", changes)
    monkeypatch.setattr(chat_server, "revert_turn", revert)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    answers = []
    for text in ("一つ目", "二つ目"):
        r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                              json={"content": text, "mode": "code", "model": "test", "code_project": "demo"})
        answers.append(_wait_done(admin_client, conv_id, r.json()["assistant_message_id"]))
    assert answers[1]["meta"]["changes"] == [{"file": "a.py", "additions": 1, "deletions": 1, "status": "modified"}]
    base = f"/api/v1/chat/conversations/{conv_id}/opencode"
    detail = admin_client.get(f"{base}/changes", params={"message_id": answers[1]["id"]}).json()
    assert detail["files"][0]["patch"].startswith("@@")
    old = admin_client.post(f"{base}/revert", headers=CSRF_HEADERS, json={"message_id": answers[0]["id"]})
    assert old.status_code == 409 and reverted == []
    assert admin_client.post(f"{base}/revert", headers=CSRF_HEADERS, json={"message_id": answers[1]["id"]}).status_code == 200
    assert reverted == [("ses_new", "msg_2")]
    again = admin_client.post(f"{base}/revert", headers=CSRF_HEADERS, json={"message_id": answers[1]["id"]})
    assert again.status_code == 409


def test_a_queued_request_can_be_told_to_the_running_turn(admin_client, monkeypatch, tmp_path):
    """送信待ちを作業中の OpenCode へ伝えると、回答の並びに残り、送信待ちから消える。"""
    import asyncio

    from app.integrations.opencode import chat_server

    _fake_opencode(monkeypatch, tmp_path, [])
    told: list[str] = []
    release = {"go": False}

    async def turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission, agent="build", on_status=None):
        while not release["go"]:
            await asyncio.sleep(0.02)
        return {"status": "succeeded", "error": ""}

    async def active(srv, sid):
        return True

    async def steer(srv, sid, text):
        told.append(text)

    monkeypatch.setattr(chat_server, "run_turn", turn)
    monkeypatch.setattr(chat_server, "session_is_active", active)
    monkeypatch.setattr(chat_server, "steer_turn", steer)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    base = f"/api/v1/chat/conversations/{conv_id}"
    answer = admin_client.post(f"{base}/send", headers=CSRF_HEADERS,
                               json={"content": "作って", "mode": "code", "model": "test", "code_project": "demo"}).json()
    item = admin_client.post(f"{base}/queue", headers=CSRF_HEADERS,
                             json={"content": "色は青で", "mode": "code", "model": "test", "code_project": "demo"}).json()["item"]
    try:
        for _ in range(100):
            result = admin_client.post(f"{base}/queue/{item['id']}/steer", headers=CSRF_HEADERS)
            if result.status_code == 200:
                break
            time.sleep(0.05)
        assert result.status_code == 200 and told == ["色は青で"]
    finally:
        release["go"] = True
    final = _wait_done(admin_client, conv_id, answer["assistant_message_id"])
    assert {"k": "n", "text": "色は青で"} in final["meta"]["timeline"]
    assert admin_client.get(f"{base}/queue").json()["items"] == []


def test_timeline_offsets_use_the_browsers_character_positions():
    """絵文字の前後で本文の位置がずれない（画面は UTF-16 で切り出す）。"""
    from app.workflows.chat_persist import _utf16_len

    assert _utf16_len("直しました") == 5
    assert _utf16_len("🎉a") == 3


def test_an_empty_project_continues_in_the_conversations_last_project(admin_client, monkeypatch, tmp_path):
    from app.integrations.opencode import provider

    resolved: list = []
    _fake_opencode(monkeypatch, tmp_path, resolved)
    monkeypatch.setattr(provider, "codedev_root", lambda: tmp_path.parent)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    first = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                              json={"content": "作って", "mode": "code", "model": "test", "code_project": "demo"})
    _wait_done(admin_client, conv_id, first.json()["assistant_message_id"])
    # 画面を開き直して作業フォルダの選択が外れた（空）まま送った
    second = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                               json={"content": "続けて", "mode": "code", "model": "test"})
    assert second.status_code == 201, second.text
    _wait_done(admin_client, conv_id, second.json()["assistant_message_id"])
    assert resolved[0] == ("demo", "")
    # CodeDEV の直下なので名前で開く（フォルダの取り込みの道を通すと失敗・複製のおそれ。レビュー指摘）
    assert resolved[1] == (tmp_path.name, "")


def test_a_deleted_last_project_is_not_reopened(admin_client, monkeypatch, tmp_path):
    from app.integrations.opencode import provider

    resolved: list = []
    project = tmp_path / "gone"
    project.mkdir()
    _fake_opencode(monkeypatch, project, resolved)
    monkeypatch.setattr(provider, "codedev_root", lambda: tmp_path)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    first = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                              json={"content": "作って", "mode": "code", "model": "test", "code_project": "gone"})
    _wait_done(admin_client, conv_id, first.json()["assistant_message_id"])
    project.rmdir()                                   # Project Lab で消した
    second = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                               json={"content": "続けて", "mode": "code", "model": "test"})
    _wait_done(admin_client, conv_id, second.json()["assistant_message_id"])
    assert resolved[1] == ("", "")                    # 作り直さない（既定のプロジェクトの扱いへ）


def test_each_project_box_gets_its_own_tmp_that_outlives_the_box(monkeypatch, tmp_path):
    from app.integrations.opencode import provider
    from app.maintenance import sandbox

    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path)
    a = tmp_path / "CodeDEV" / "conv"
    b = tmp_path / "CodeDEV" / "train"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    tmp_a, tmp_b = provider.project_tmp_dir(a), provider.project_tmp_dir(b)
    assert tmp_a != tmp_b and tmp_a.is_dir() and tmp_b.is_dir()
    assert provider.project_tmp_dir(a) == tmp_a                    # 同じプロジェクトはいつも同じ場所（続きから使える）
    assert not tmp_a.is_relative_to(a)                             # プロジェクトの中には置かない
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(sandbox, "_rootfs", lambda: tmp_path / "rootfs")
    argv = sandbox.container_argv(["true"], workdir=a, writable=[a], binds=[(tmp_a, Path("/tmp"))])
    assert f"{tmp_a}:/tmp:rw" in argv


def test_other_projects_are_hidden_inside_a_project_box(monkeypatch, tmp_path):
    """箱の中で CodeDEV を空の場所で覆い、このプロジェクトだけをつなぐ。前に開いた別のプロジェクトの
    空のつなぎ口（Conv など）が見えて、OpenCode が書けない場所へ書こうとしたため（2026-11-18）。"""
    from app.integrations.opencode import provider
    from app.maintenance import sandbox

    root = tmp_path / "CodeDEV"
    project = root / "conv"
    project.mkdir(parents=True)
    monkeypatch.setattr(provider, "codedev_root", lambda: root)
    assert provider._sibling_projects(project) == [root.resolve()]
    assert provider._sibling_projects(tmp_path / "elsewhere") == []
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(sandbox, "_rootfs", lambda: tmp_path / "rootfs")
    argv = sandbox.container_argv(["true"], workdir=project, writable=[project], hide=[root])
    assert argv[argv.index("--tmpfs") + 1].startswith(f"{root}:")
    assert f"{project}:{project}:rw" in argv            # このプロジェクトは隠した上につながる


def test_deleting_a_project_removes_its_box_tmp(monkeypatch, tmp_path):
    from app.integrations.opencode import provider

    root = tmp_path / "CodeDEV"
    (root / "old").mkdir(parents=True)
    monkeypatch.setattr(provider, "codedev_root", lambda: root)
    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path)
    scratch = provider.project_tmp_dir(root / "old")
    (scratch / "proto.js").write_text("x")
    provider.delete_project("old")
    assert not scratch.exists()


def test_selftest_checks_the_project_box_isolation(monkeypatch, tmp_path):
    """箱の /tmp が専用で、CodeDEV が覆われているかを自己診断で確かめる（同じことが起きたら気付けるように）。"""
    from app.diagnostics import selftest
    from app.integrations.opencode import provider
    from app.maintenance import sandbox

    root = tmp_path / "CodeDEV"
    root.mkdir()
    monkeypatch.setattr(provider, "isolation_mode", lambda: "box")
    monkeypatch.setattr(provider, "codedev_root", lambda: root)
    monkeypatch.setattr(provider, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(selftest, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(sandbox, "_rootfs", lambda: tmp_path / "rootfs")
    ok, text = selftest._opencode_box()
    assert ok is True and "他のプロジェクトは見えない" in text
    monkeypatch.setattr(provider, "_sibling_projects", lambda project: [])   # 覆いが外れた
    ok, text = selftest._opencode_box()
    assert ok is False and "別のプロジェクト" in text


def test_plan_is_passed_to_opencode_and_recorded_on_the_answer(admin_client, monkeypatch, tmp_path):
    """プランで送ると、OpenCode に plan のエージェントで渡り、回答に印が残る（画面が「プラン」と出す）。"""
    resolved: list = []
    _fake_opencode(monkeypatch, tmp_path, resolved)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                          json={"content": "計画して", "mode": "code", "model": "test", "code_project": "demo",
                                "code_agent": "plan"})
    final = _wait_done(admin_client, conv_id, r.json()["assistant_message_id"])
    assert AGENTS[-1] == "plan" and final["meta"]["agent"] == "plan"
    bad = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                            json={"content": "x", "mode": "code", "model": "test", "code_project": "demo",
                                  "code_agent": "yolo"})
    assert bad.status_code == 422


def test_a_conversation_is_titled_when_it_starts_and_todos_keep_their_times(admin_client, monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    resolved: list = []
    _fake_opencode(monkeypatch, tmp_path, resolved)

    async def fake_turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission, agent="build", on_status=None):
        await on_status({"phase": "prefill", "detail": "読み込み"})
        await on_tool({"id": "t", "name": "todowrite", "status": "completed", "output": "",
                       "input": {"todos": [{"content": "設計", "status": "completed"}, {"content": "実装", "status": "in_progress"},
                                           {"content": "確認", "status": "pending"}]}})
        await on_text("できました。")
        return {"status": "succeeded", "error": ""}

    monkeypatch.setattr(chat_server, "run_turn", fake_turn)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send", headers=CSRF_HEADERS,
                          json={"content": "発電機を\\(L_d, L_q\\)のdq軸モデルで計算して。詳しく", "mode": "code", "model": "test", "code_project": "demo"})
    titles = admin_client.get("/api/v1/chat/conversations").json()
    rows = titles if isinstance(titles, list) else titles.get("conversations", titles.get("items", []))
    assert next(c for c in rows if c["id"] == conv_id)["title"] == "発電機をL_d, L_qのdq軸モデルで計算して"
    final = _wait_done(admin_client, conv_id, r.json()["assistant_message_id"])
    todos = final["meta"]["todos"]
    assert [t["status"] for t in todos] == ["completed", "in_progress", "pending"]
    assert todos[0]["completed_at"] and todos[1]["started_at"] and "completed_at" not in todos[1]
    assert not final["meta"]["tools"]                                # 道具の行には出さない



def test_chat_after_opencode_is_told_it_has_no_tools_and_tool_text_is_reported(admin_client, monkeypatch, tmp_path):
    """OpenCode の後のチャットには道具が無いと伝える。文字だけの呼び出しは自己診断で分かる（2026-11-18 本番）。"""
    from app.database import SessionLocal
    from app.diagnostics import selftest
    from app.models import ChatMessage
    from app.workflows import chat_persist

    seen = []

    async def fake_job(job, assistant_id, conv_id, history, params):
        seen.append(history)
        db = SessionLocal()
        row = db.get(ChatMessage, assistant_id)
        row.content, row.status = "<tool_call><function=exec>", "completed"
        db.commit()
        db.close()
        return {}

    monkeypatch.setattr(chat_persist, "_run_chat_job", fake_job)
    conv = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    db = SessionLocal()
    db.add(ChatMessage(id="codeanswer" + conv[:16], conversation_id=conv, role="assistant", content="済",
                       status="completed", meta_json='{"mode": "code"}'))
    db.commit()
    db.close()
    r = admin_client.post(f"/api/v1/chat/conversations/{conv}/send", headers=CSRF_HEADERS,
                          json={"content": "質問", "mode": "chat", "model": "test"})
    _wait_done(admin_client, conv, r.json()["assistant_message_id"])
    assert any("<tool_call>" in str(m["content"]) for m in seen[0] if m["role"] == "system")
    assert selftest._chat_tool_text()[0] == "warn"
