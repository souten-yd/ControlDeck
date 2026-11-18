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

    async def fake_turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission):
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


def test_an_empty_project_continues_in_the_conversations_last_project(admin_client, monkeypatch, tmp_path):
    resolved: list = []
    _fake_opencode(monkeypatch, tmp_path, resolved)
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
    assert resolved[1] == ("", str(tmp_path))       # 前回のプロジェクトで続けた


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
