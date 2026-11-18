"""AI アシスタントの OpenCode（opencode serve の API）: 権限プラグイン・設定・1 回分の対話・許可の返答。"""
import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import CSRF_HEADERS

PLUGIN = Path(__file__).resolve().parents[1] / "app" / "integrations" / "opencode" / "plugins" / "controldeck-permission"


# ---- 権限プラグイン（node で直接呼ぶ） ----

@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")
def test_permission_plugin_auto_allows_safe_work_and_asks_for_danger(tmp_path):
    mode_file = tmp_path / "mode.json"
    mode_file.write_text(json.dumps({"mode": "auto"}))
    script = f"""
      import plugin, {{ decide, judgeShell }} from {json.dumps((PLUGIN / "index.mjs").as_uri())};
      const out = {{}};
      for (const c of ["ls -la", "git status && git diff", "npm run build", "python -m pytest -q", "rg foo | head",
                       "rm -rf build", "git push origin main", "curl https://x", "echo x > a.txt", "npm install left-pad",
                       "sudo ls", "find . -delete", "some-unknown-tool --flag", "cat a | sh",
                       "env bash -c id", "env sh -c 'git push --force'", "echo `rm -rf ~`", "echo $(/bin/rm -rf x)",
                       "cat <(bash -c id)", "ls\\nnode -e 'x'", "echo x | env sh", "find . -fprint /etc/x",
                       "sort -o important.py a", "git diff --output=src/main.py", "git switch --discard-changes main",
                       "git branch -f main HEAD~5", "node -e 'x'", "python3 -c 'x'", "xargs rm < list",
                       "git -c core.fsmonitor=x status"]) {{
        out[c] = judgeShell(c).effect;
      }}
      out.edit = decide("auto", "edit", ["a.py"]).effect;
      out.webfetch = decide("auto", "webfetch", ["https://x"]);
      out.askMode = decide("ask", "shell", ["ls"]);
      // フックそのもの: ask のものだけを判断し、allow / deny で決まったものは触らない
      const hooks = {{}};
      await plugin.setup({{ permission: {{ hook: async (name, fn) => {{ hooks[name] = fn; }} }} }});
      const e1 = {{ action: "shell", resources: ["ls"], effect: "ask" }}; hooks.evaluate(e1);
      const e2 = {{ action: "shell", resources: ["systemctl stop x"], effect: "deny" }}; hooks.evaluate(e2);
      const e3 = {{ action: "shell", resources: ["rm x"], effect: "ask" }}; hooks.evaluate(e3);
      out.hook = [e1.effect, e2.effect, e3.effect, e1.message];
      console.log(JSON.stringify(out));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                            env={"CONTROLDECK_PERMISSION_FILE": str(mode_file), "PATH": "/usr/bin:/bin"},
                            timeout=30, check=True)
    out = json.loads(result.stdout)
    for safe in ("ls -la", "git status && git diff", "npm run build", "python -m pytest -q", "rg foo | head"):
        assert out[safe] == "allow", safe
    for danger in ("rm -rf build", "git push origin main", "curl https://x", "echo x > a.txt",
                   "npm install left-pad", "sudo ls", "find . -delete", "some-unknown-tool --flag", "cat a | sh",
                   # レビューで見つかった、判定を素通りしていた形
                   "env bash -c id", "env sh -c 'git push --force'", "echo `rm -rf ~`", "echo $(/bin/rm -rf x)",
                   "cat <(bash -c id)", "ls\nnode -e 'x'", "echo x | env sh", "find . -fprint /etc/x",
                   "sort -o important.py a", "git diff --output=src/main.py", "git switch --discard-changes main",
                   "git branch -f main HEAD~5", "node -e 'x'", "python3 -c 'x'", "xargs rm < list",
                   "git -c core.fsmonitor=x status"):
        assert out[danger] == "ask", danger
    # Auto は箱の中なら確認しない（利用者 2026-11-18）。禁止（deny）は緩めない
    assert out["edit"] == "allow" and out["webfetch"]["effect"] == "allow" and out["askMode"] is None
    assert out["hook"][:3] == ["allow", "deny", "allow"] and out["hook"][3].startswith("Auto")


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")
def test_permission_plugin_asks_for_every_command_outside_the_box(tmp_path):
    """箱の外（host）では境界が無いので、Auto でもコマンドは確認（編集は自動のまま）。"""
    mode_file = tmp_path / "mode.json"
    mode_file.write_text(json.dumps({"mode": "auto"}))
    script = f"""
      import {{ decide }} from {json.dumps((PLUGIN / "index.mjs").as_uri())};
      console.log(JSON.stringify([decide("auto", "shell", ["ls"]).effect, decide("auto", "edit", ["a"]).effect]));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                            env={"CONTROLDECK_PERMISSION_FILE": str(mode_file), "CONTROLDECK_ISOLATION": "host",
                                 "PATH": "/usr/bin:/bin"}, timeout=30, check=True)
    assert json.loads(result.stdout) == ["ask", "allow"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")
def test_permission_plugin_falls_back_to_ask_when_the_mode_file_is_unreadable(tmp_path):
    script = f"""
      import {{ currentMode }} from {json.dumps((PLUGIN / "index.mjs").as_uri())};
      console.log(currentMode());
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                            env={"CONTROLDECK_PERMISSION_FILE": str(tmp_path / "missing.json"), "PATH": "/usr/bin:/bin"},
                            timeout=30, check=True)
    assert result.stdout.strip() == "ask"


# ---- 設定 ----

def test_chat_server_config_asks_before_the_deny_list_and_loads_the_plugin():
    from app.integrations.opencode import provider

    v2 = {"permissions": [{"action": "shell", "resource": "systemctl *", "effect": "deny"}],
          "plugins": ["file:///x/superpowers"]}
    out = provider._apply_chat_server(v2)
    actions = [r["action"] for r in out["permissions"][:len(provider.CHAT_SERVER_ASK_ACTIONS)]]
    assert actions == list(provider.CHAT_SERVER_ASK_ACTIONS)
    assert out["permissions"][-1]["effect"] == "deny"           # 後に一致した規則が勝つ: 禁止は緩まない
    assert out["plugins"][0] == provider.PERMISSION_PLUGIN.as_uri() and "file:///x/superpowers" in out["plugins"]
    assert (provider.PERMISSION_PLUGIN / "package.json").is_file()


def test_container_argv_passes_secrets_by_env_file_not_argv(tmp_path):
    from app.maintenance import sandbox

    env_file = tmp_path / "serve.env"
    env_file.write_text("OPENCODE_PASSWORD=secret-value\n")
    argv = sandbox.container_argv(["true"], workdir=tmp_path, writable=[tmp_path], env_file=env_file)
    assert "--env-file" in argv and str(env_file) in argv
    assert not any("secret-value" in part for part in argv)


def test_permission_mode_setting_is_validated(monkeypatch, tmp_path):
    from app.integrations.opencode import provider

    with pytest.raises(ValueError):
        provider.save_workflow({"chat_permission_mode": "yolo"})
    assert provider.DEFAULT_SETTINGS["chat_permission_mode"] == "auto"


# ---- 1 回分の対話 ----

def _server(tmp_path):
    from app.integrations.opencode import chat_server

    return chat_server.Server(key="abc123", port=1, password="p", unit="u", config=str(tmp_path),
                              project=str(tmp_path), permission_file=str(tmp_path / "perm.json"),
                              started_at=0, last_used=0)


def _fake_events(monkeypatch, sequence, calls):
    from app.integrations.opencode import chat_server

    async def fake_events(server):
        yield {"type": "server.connected", "data": {}}
        while not calls:  # prompt が送られるまで待つ
            await asyncio.sleep(0.01)
        for event in sequence:
            if event == "WAIT":
                await asyncio.sleep(10)
                continue
            yield event

    async def fake_call(server, method, path, body=None):
        calls.append((method, path, body))
        return {}

    monkeypatch.setattr(chat_server, "events", fake_events)
    monkeypatch.setattr(chat_server, "_call", fake_call)
    monkeypatch.setattr(chat_server, "touch", lambda server: None)


def test_run_turn_streams_text_tools_and_permissions(monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    sid = "ses_1"
    sequence = [
        {"type": "session.reasoning.delta", "data": {"sessionID": sid, "delta": "考える"}},
        {"type": "session.tool.input.started", "data": {"sessionID": sid, "id": "call_1", "name": "shell"}},
        {"type": "session.tool.called", "data": {"sessionID": sid, "id": "call_1", "input": {"command": "rm -rf build"}}},
        {"type": "permission.asked", "data": {"id": "per_1", "sessionID": sid, "action": "shell",
                                               "resources": ["rm -rf build"], "save": ["rm *"],
                                               "source": {"id": "call_1"}}},
        {"type": "permission.replied", "data": {"sessionID": sid, "requestID": "per_1", "reply": "once"}},
        {"type": "session.text.delta", "data": {"sessionID": "ses_child", "delta": "子の本文"}},  # 別セッションは出さない
        {"type": "session.tool.success", "data": {"sessionID": sid, "id": "call_1",
                                                   "content": [{"type": "text", "text": "(no output)"}]}},
        {"type": "session.text.delta", "data": {"sessionID": sid, "delta": "## 結果\n"}},
        {"type": "session.text.delta", "data": {"sessionID": sid, "delta": "完了"}},
        {"type": "session.execution.succeeded", "data": {"sessionID": sid}},
    ]
    calls: list = []
    _fake_events(monkeypatch, sequence, calls)
    text, thinking, tools, perms = [], [], [], []

    async def collect(target, item):
        target.append(json.loads(json.dumps(item)))

    result = asyncio.run(chat_server.run_turn(
        _server(tmp_path), sid, "片付けて",
        on_text=lambda d: collect(text, d), on_reasoning=lambda d: collect(thinking, d),
        on_tool=lambda t: collect(tools, t), on_permission=lambda p: collect(perms, p)))
    assert result == {"status": "succeeded", "error": ""}
    # 毎回エージェントをそろえてから送る（既定はビルド）
    assert calls[0] == ("POST", f"/api/session/{sid}/agent", {"agent": "build"})
    assert calls[1] == ("GET", f"/api/session/{sid}", None)          # 切り替わったかを読み直す
    assert calls[2] == ("POST", f"/api/session/{sid}/prompt", {"text": "片付けて"})
    assert "".join(text) == "## 結果\n完了" and thinking == ["考える"]
    assert tools[-1]["status"] == "completed" and tools[-1]["input"] == {"command": "rm -rf build"}
    assert perms[0]["status"] == "pending" and perms[0]["save"] == ["rm *"] and perms[1]["status"] == "once"


def test_run_turn_reports_failure_and_interrupts_when_cancelled(monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    calls: list = []
    _fake_events(monkeypatch, [{"type": "session.execution.failed",
                                "data": {"sessionID": "ses_1", "error": {"message": "model error"}}}], calls)
    noop = lambda *_: asyncio.sleep(0)  # noqa: E731
    result = asyncio.run(chat_server.run_turn(_server(tmp_path), "ses_1", "x", on_text=noop, on_reasoning=noop,
                                              on_tool=noop, on_permission=noop))
    assert result == {"status": "failed", "error": "model error"}

    calls.clear()
    _fake_events(monkeypatch, ["WAIT"], calls)

    async def cancel_midway():
        task = asyncio.create_task(chat_server.run_turn(_server(tmp_path), "ses_1", "x", on_text=noop,
                                                        on_reasoning=noop, on_tool=noop, on_permission=noop))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_midway())
    assert ("POST", "/api/session/ses_1/interrupt", {}) in calls


def test_mode_file_and_idle_reaping(monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    monkeypatch.setattr(chat_server, "_state_dir", lambda: tmp_path)
    server = _server(tmp_path)
    chat_server.set_mode(server, "ask")
    assert chat_server.get_mode(server) == "ask"
    with pytest.raises(chat_server.ChatServerError):
        chat_server.set_mode(server, "everything")
    stopped_units = []
    monkeypatch.setattr(chat_server, "_stop_unit", lambda unit: stopped_units.append(unit))
    server.config = str(tmp_path / "config-old")
    server.last_used = time.time() - chat_server.IDLE_SECONDS - 5
    chat_server._save(server)
    fresh = _server(tmp_path)
    fresh.key, fresh.unit, fresh.last_used, fresh.config = "def456", "u2", time.time(), str(tmp_path / "c2")
    chat_server._save(fresh)
    assert chat_server.reap_idle() == ["abc123"] and stopped_units == ["u"]
    assert chat_server.load("def456") is not None and chat_server.load("abc123") is None


# ---- チャット API ----

def test_code_mode_records_tools_and_permissions_and_answers_them(admin_client, monkeypatch, tmp_path):
    import app.workflows.chat_persist as cp
    from app.integrations.opencode import chat_server
    from app.models_mgmt import local_llm

    server = _server(tmp_path)
    replies = []

    async def fake_resolve(name="", path=""):
        return tmp_path

    async def fake_ensure(project, owner):
        return server

    async def fake_exists(srv, sid):
        return False

    async def fake_create(srv, title):
        return "ses_new"

    async def fake_ready(url):
        return None

    hold = asyncio.Event()

    async def fake_turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission, agent="build", on_status=None):
        await on_tool({"id": "call_1", "name": "shell", "status": "running", "input": {"command": "rm -rf build"}, "output": ""})
        await on_permission({"id": "per_1", "action": "shell", "resources": ["rm -rf build"], "save": [],
                             "tool": "call_1", "status": "pending"})
        for _ in range(100):
            if replies:
                break
            await asyncio.sleep(0.05)
        await on_permission({"id": "per_1", "status": replies[0] if replies else "expired"})
        await on_tool({"id": "call_1", "name": "shell", "status": "completed", "input": {}, "output": "(no output)"})
        await on_text("片付けました。")
        return {"status": "succeeded", "error": ""}

    async def fake_reply(srv, sid, req, decision):
        assert (sid, req) == ("ses_new", "per_1")
        replies.append(decision)

    monkeypatch.setattr(chat_server, "resolve_project", fake_resolve)
    monkeypatch.setattr(chat_server, "ensure", fake_ensure)
    monkeypatch.setattr(chat_server, "session_exists", fake_exists)
    monkeypatch.setattr(chat_server, "create_session", fake_create)
    monkeypatch.setattr(chat_server, "run_turn", fake_turn)
    monkeypatch.setattr(chat_server, "reply_permission", fake_reply)
    monkeypatch.setattr(chat_server, "load", lambda key: server)
    monkeypatch.setattr(local_llm, "ensure_ready_by_base_url", fake_ready)
    chat_server.set_mode(server, "auto")

    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send",
                          json={"content": "build を片付けて", "mode": "code", "model": "test", "code_project": "demo"},
                          headers=CSRF_HEADERS)
    assert r.status_code == 201, r.text
    aid = r.json()["assistant_message_id"]

    def assistant():
        msgs = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
        return next(m for m in msgs if m["id"] == aid)

    for _ in range(100):
        if any(p.get("status") == "pending" for p in assistant()["meta"].get("permissions", [])):
            break
        time.sleep(0.05)
    meta = assistant()["meta"]
    assert meta["opencode_session"] == "ses_new" and meta["permission_mode"] == "auto"
    assert meta["permissions"][0]["status"] == "pending" and meta["tools"][0]["status"] == "running"

    # 答えられない許可・他人の形は断る
    bad = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/opencode/permission",
                            json={"message_id": aid, "request_id": "per_x", "decision": "once"}, headers=CSRF_HEADERS)
    assert bad.status_code == 409
    wrong = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/opencode/permission",
                              json={"message_id": aid, "request_id": "per_1", "decision": "yes"}, headers=CSRF_HEADERS)
    assert wrong.status_code == 422
    ok = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/opencode/permission",
                           json={"message_id": aid, "request_id": "per_1", "decision": "once"}, headers=CSRF_HEADERS)
    assert ok.status_code == 204, ok.text

    for _ in range(100):
        if assistant()["status"] == "completed":
            break
        time.sleep(0.05)
    final = assistant()
    assert final["status"] == "completed" and final["content"] == "片付けました。"
    assert final["meta"]["permissions"][0]["status"] == "once" and final["meta"]["tools"][0]["status"] == "completed"
    assert replies == ["once"]


def test_permission_mode_api_saves_and_applies_to_running_servers(admin_client, monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    monkeypatch.setattr(chat_server, "_state_dir", lambda: tmp_path)
    mine = _server(tmp_path)
    mine.owner = 1
    chat_server._save(mine)
    other = _server(tmp_path)
    other.key, other.owner, other.permission_file = "zzz999", 999, str(tmp_path / "other.json")
    chat_server._save(other)
    chat_server.set_mode(mine, "auto")
    chat_server.set_mode(other, "auto")

    assert admin_client.get("/api/v1/chat/opencode/permission-mode").json()["mode"] in ("auto", "ask")
    bad = admin_client.put("/api/v1/chat/opencode/permission-mode", json={"mode": "yolo"}, headers=CSRF_HEADERS)
    assert bad.status_code == 422
    admin_id = admin_client.get("/api/v1/auth/me").json()["id"]
    mine.owner = admin_id
    chat_server._save(mine)
    r = admin_client.put("/api/v1/chat/opencode/permission-mode", json={"mode": "ask"}, headers=CSRF_HEADERS)
    assert r.status_code == 200 and r.json()["mode"] == "ask"
    assert admin_client.get("/api/v1/chat/opencode/permission-mode").json()["mode"] == "ask"
    # 設定は全体で 1 つ: 動いているすべてのサーバーに効く（他の利用者のものも。画面の表示と食い違わない）
    assert chat_server.get_mode(chat_server.load(mine.key)) == "ask"
    assert chat_server.get_mode(chat_server.load(other.key)) == "ask"
    admin_client.put("/api/v1/chat/opencode/permission-mode", json={"mode": "auto"}, headers=CSRF_HEADERS)


def test_run_turn_passes_child_permissions_and_survives_a_reconnect(monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    sid = "ses_1"
    sequence = [
        {"type": "session.created", "data": {"id": "ses_child", "parentID": sid}},
        {"type": "permission.asked", "data": {"id": "per_c", "sessionID": "ses_child", "action": "shell",
                                               "resources": ["rm x"], "save": [], "source": {}}},
        {"type": "permission.asked", "data": {"id": "per_other", "sessionID": "ses_unrelated", "action": "shell",
                                               "resources": ["rm y"], "save": [], "source": {}}},
        {"type": "session.execution.succeeded", "data": {"sessionID": sid}},
    ]
    calls: list = []
    _fake_events(monkeypatch, sequence, calls)
    perms = []

    async def on_permission(item):
        perms.append(dict(item))

    noop = lambda *_: asyncio.sleep(0)  # noqa: E731
    result = asyncio.run(chat_server.run_turn(_server(tmp_path), sid, "x", on_text=noop, on_reasoning=noop,
                                              on_tool=noop, on_permission=on_permission))
    assert result["status"] == "succeeded"
    assert [p["id"] for p in perms] == ["per_c"]                      # 関係ないセッションの許可は出さない
    assert perms[0]["sessionID"] == "ses_child" and perms[0]["child"] is True


def test_running_servers_are_not_reaped(monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    monkeypatch.setattr(chat_server, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(chat_server, "_stop_unit", lambda unit: None)
    server = _server(tmp_path)
    server.last_used = time.time() - chat_server.IDLE_SECONDS - 5
    chat_server._save(server)
    chat_server._active[server.key] = 1
    try:
        assert chat_server.reap_idle() == []          # 発言を処理中のサーバーは止めない
    finally:
        chat_server._active.pop(server.key, None)
    assert chat_server.reap_idle() == [server.key]


def test_code_mode_rejects_a_second_message_while_working_and_never_stays_generating(admin_client, monkeypatch, tmp_path):
    import app.workflows.chat_persist as cp
    from app.integrations.opencode import chat_server
    from app.models_mgmt import local_llm

    async def boom(name="", path=""):
        raise KeyError("unexpected")

    async def fake_ready(url):
        return None

    monkeypatch.setattr(chat_server, "resolve_project", boom)
    monkeypatch.setattr(local_llm, "ensure_ready_by_base_url", fake_ready)
    conv_id = admin_client.post("/api/v1/chat/conversations", headers=CSRF_HEADERS).json()["id"]
    r = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send",
                          json={"content": "x", "mode": "code", "model": "t", "code_project": "demo"}, headers=CSRF_HEADERS)
    aid = r.json()["assistant_message_id"]
    for _ in range(100):
        msgs = admin_client.get(f"/api/v1/chat/conversations/{conv_id}/messages").json()["messages"]
        status = next(m for m in msgs if m["id"] == aid)["status"]
        if status != "generating":
            break
        time.sleep(0.05)
    assert status == "failed"                         # 想定外の例外でも「生成中」のまま残らない

    from app.database import SessionLocal
    from app.models import ChatMessage
    with SessionLocal() as db:                        # 作業中の発言があれば、次は 409
        db.get(ChatMessage, aid).status = "generating"
        db.commit()
    second = admin_client.post(f"/api/v1/chat/conversations/{conv_id}/send",
                               json={"content": "y", "mode": "code", "model": "t", "code_project": "demo"},
                               headers=CSRF_HEADERS)
    assert second.status_code == 409


def test_japanese_project_directory_is_sent_url_encoded(tmp_path):
    """日本語のプロジェクト名でも、ヘッダーを送れる（ASCII だけ）。OpenCode は decodeURIComponent で戻す。
    根拠: 2026-11-17 プロジェクト「コンバータシム」で OpenCode が UnicodeEncodeError になり、回答を作れなかった。"""
    from urllib.parse import unquote

    from app.integrations.opencode import chat_server

    project = "/data1tb/CodeDEV/コンバータシム"
    server = chat_server.Server(key="abc123", port=1, password="p", unit="u", config=str(tmp_path),
                                project=project, permission_file=str(tmp_path / "perm.json"),
                                started_at=0.0, last_used=0.0)
    value = server.headers()["x-opencode-directory"]
    value.encode("ascii")
    assert unquote(value) == project
    assert chat_server.Server(key="a", port=1, password="p", unit="u", config="c", project="/data1tb/CodeDEV/my-app",
                              permission_file="p", started_at=0.0, last_used=0.0).headers()["x-opencode-directory"] \
        == "/data1tb/CodeDEV/my-app"


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い")
def test_plan_agent_changes_nothing_even_in_auto_mode(tmp_path):
    """プラン（plan エージェント）では何も変えない。2026-11-18 利用者「プランの際はビルドモードではないことが明確に」。
    レビュー指摘（同日）: 規則で allow に決まったもの（ビルドで「いつも許可」）・子のエージェント・書ける読むだけ風の
    コマンド（fd -x・uniq IN OUT・rg --pre）も止める。確認モード・箱の外では読むだけのコマンドも確認する。
    エージェント名が無くても、ControlDeck が書いたプランのセッション一覧で判断する。"""
    results = {}
    for mode in ("auto", "ask"):
        mode_file = tmp_path / f"{mode}.json"
        mode_file.write_text(json.dumps({"mode": mode, "plan_sessions": ["ses_plan"]}))
        script = f"""
          import plugin from {json.dumps((PLUGIN / "index.mjs").as_uri())};
          const hooks = {{}};
          await plugin.setup({{ permission: {{ hook: async (name, fn) => {{ hooks[name] = fn; }} }} }});
          const out = {{}};
          const cases = [["edit", ["a.py"], "ask"], ["write", ["b.py"], "allow"], ["shell", ["ls -la"], "ask"],
                         ["shell", ["ls -la x 2>/dev/null | head -5; echo done"], "ask"], ["shell", ["node --version 2>&1"], "ask"],
                         ["shell", ["git -C /p status --short 2>&1 | head"], "ask"], ["shell", ["ls > out.txt"], "ask"], ["shell", ["ls >&out.txt"], "ask"], ["shell", ["echo x 1<>f.txt"], "ask"],
                         ["shell", ["cat a & npm run build"], "ask"], ["shell", ["git remote -v add x http://e"], "ask"],
                         ["shell", ["cat a 2>/dev/nullx"], "ask"], ["shell", ["node x.js"], "ask"],
                         ["shell", ["git status && git diff"], "ask"], ["shell", ["rg foo | head"], "ask"], ["shell", ["grep -n -C 3 foo a.py"], "ask"],
                         ["shell", ["npm run build"], "ask"], ["shell", ["git commit -m x"], "ask"], ["shell", ["mkdir x"], "ask"],
                         ["shell", ["python -m pytest -q"], "ask"], ["shell", ["rm -rf build"], "allow"],
                         ["shell", ["fd . -x touch {{}}"], "ask"], ["shell", ["uniq a.txt src/app.py"], "ask"],
                         ["shell", ["rg --pre ./x.sh foo"], "ask"], ["subagent", ["developer"], "allow"],
                         ["execute", ["sculpt"], "allow"], ["webfetch", ["https://x"], "ask"], ["shell", ["systemctl stop x"], "deny"]];
          for (const [action, resources, effect] of cases) {{
            const event = {{ action, resources, effect, agent: "plan", sessionID: "ses_x" }};
            hooks.evaluate(event);
            out[action + ":" + resources[0]] = event.effect;
          }}
          // エージェント名が無くても、プランのセッション一覧で止める
          const bySession = {{ action: "edit", resources: ["a.py"], effect: "allow", sessionID: "ses_plan" }};
          hooks.evaluate(bySession);
          out.bySession = bySession.effect;
          const build = {{ action: "edit", resources: ["a.py"], effect: "ask", agent: "build", sessionID: "ses_b" }};
          hooks.evaluate(build);
          out.build = build.effect;
          console.log(JSON.stringify(out));
        """
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                                env={"CONTROLDECK_PERMISSION_FILE": str(mode_file), "PATH": "/usr/bin:/bin"},
                                timeout=30, check=True)
        results[mode] = json.loads(result.stdout)
    for mode, out in results.items():
        assert out["edit:a.py"] == "deny" and out["write:b.py"] == "deny", mode       # allow で決まった書き込みも止める
        for change in ("npm run build", "git commit -m x", "mkdir x", "python -m pytest -q", "rm -rf build",
                       "fd . -x touch {}", "uniq a.txt src/app.py", "rg --pre ./x.sh foo",
                       "ls > out.txt", "cat a 2>/dev/nullx", "node x.js", "ls >&out.txt", "echo x 1<>f.txt",
                       "cat a & npm run build", "git remote -v add x http://e"):
            assert out[f"shell:{change}"] == "deny", (mode, change)
        assert out["subagent:developer"] == "deny", mode
        assert out["execute:sculpt"] == "ask", mode
        assert out["shell:systemctl stop x"] == "deny", mode                            # 禁止は緩めない
        assert out["bySession"] == "deny", mode
    # 調べて計画するので、Auto の箱の中では Web を読める（確認モードは確認する）
    assert results["auto"]["webfetch:https://x"] == "allow" and results["ask"]["webfetch:https://x"] == "ask"
    # 2026-11-18 本番: 出力を捨てるだけの形が「変更」扱いで止まっていた
    for read in ("ls -la", "rg foo | head", "grep -n -C 3 foo a.py", "ls -la x 2>/dev/null | head -5; echo done",
                 "node --version 2>&1", "git -C /p status --short 2>&1 | head", "git status && git diff"):
        assert results["auto"][f"shell:{read}"] == "allow", read
        assert results["ask"][f"shell:{read}"] == "ask", read                          # 確認モードでは緩めない
    assert results["auto"]["build"] == "allow" and results["ask"]["build"] == "ask"


def test_run_turn_switches_to_the_plan_agent(monkeypatch, tmp_path):
    from app.integrations.opencode import chat_server

    sid = "ses_p"
    calls: list = []
    _fake_events(monkeypatch, [{"type": "session.execution.succeeded", "data": {"sessionID": sid}}], calls)

    async def noop(_):
        return None

    result = asyncio.run(chat_server.run_turn(_server(tmp_path), sid, "計画して", on_text=noop, on_reasoning=noop,
                                              on_tool=noop, on_permission=noop, agent="plan"))
    assert result["status"] == "succeeded"
    assert calls[0] == ("POST", f"/api/session/{sid}/agent", {"agent": "plan"})
    # 権限プラグインへ、このセッションがプランだと知らせる（エージェント名が無くても止められるように）
    assert json.loads(Path(_server(tmp_path).permission_file).read_text())["plan_sessions"] == [sid]


def test_switch_agent_is_checked_by_reading_the_session_back(monkeypatch, tmp_path):
    """v2 は知らない名前でも 204 を返す（実測）。読み直して切り替わっていなければ止める（レビュー指摘）。"""
    from app.integrations.opencode import chat_server

    async def fake_call(server, method, path, body=None):
        return {"agent": "build"} if method == "GET" else {}

    monkeypatch.setattr(chat_server, "_call", fake_call)
    import pytest as _pytest
    with _pytest.raises(chat_server.ChatServerError):
        asyncio.run(chat_server.switch_agent(_server(tmp_path), "ses_1", "plan"))
    asyncio.run(chat_server.switch_agent(_server(tmp_path), "ses_1", "build"))
    with _pytest.raises(chat_server.ChatServerError):
        asyncio.run(chat_server.switch_agent(_server(tmp_path), "ses_1", "yolo"))


def test_run_turn_waits_for_a_background_subagent_and_keeps_the_follow_up(monkeypatch, tmp_path):
    """裏のレビュー: 親のターンが一度終わっても聞き続け、子が終わった後の親の続き（結果）を受け取る（2026-11-18 本番）。"""
    from app.integrations.opencode import chat_server

    sid, child = "ses_parent", "ses_child1"
    sequence = [
        {"type": "session.execution.started", "data": {"sessionID": sid}},
        {"type": "session.created", "data": {"id": child, "parentID": sid}},
        {"type": "session.tool.input.started", "data": {"sessionID": sid, "id": "c1", "name": "subagent"}},
        {"type": "session.tool.success", "data": {"sessionID": sid, "id": "c1", "content": [
            {"type": "text", "text": f"The subagent is working in the background (sessionID: {child}). You will receive"}]}},
        {"type": "session.text.delta", "data": {"sessionID": sid, "delta": "レビューを依頼しました。"}},
        {"type": "session.execution.succeeded", "data": {"sessionID": sid}},
        {"type": "session.execution.succeeded", "data": {"sessionID": child}},
        {"type": "session.execution.started", "data": {"sessionID": sid}},
        {"type": "session.text.delta", "data": {"sessionID": sid, "delta": "レビュー結果: 問題なし"}},
        {"type": "session.execution.succeeded", "data": {"sessionID": sid}},
    ]
    calls: list = []
    _fake_events(monkeypatch, sequence, calls)
    text, tools, states = [], [], []

    async def keep(target, item):
        target.append(json.loads(json.dumps(item)) if isinstance(item, dict) else item)

    async def noop(_):
        return None

    result = asyncio.run(chat_server.run_turn(_server(tmp_path), sid, "レビューして", on_text=lambda d: keep(text, d),
                                              on_reasoning=noop, on_tool=lambda t: keep(tools, t), on_permission=noop,
                                              on_status=lambda s: keep(states, s)))
    assert result["status"] == "succeeded"
    assert "".join(text) == "レビューを依頼しました。\n\nレビュー結果: 問題なし"
    assert tools[-1]["status"] == "completed" and tools[-1]["ended_at"] >= tools[-1]["started_at"]
    assert "background" in [s["phase"] for s in states] or any("裏のエージェント" in s["detail"] for s in states)


def _background_case(sequence_tail, monkeypatch, tmp_path, active=None):
    from app.integrations.opencode import chat_server

    sid, child = "ses_parent", "ses_child1"
    head = [{"type": "session.execution.started", "data": {"sessionID": sid}},
            {"type": "session.tool.input.started", "data": {"sessionID": sid, "id": "c1", "name": "subagent"}},
            {"type": "session.tool.success", "data": {"sessionID": sid, "id": "c1", "content": [
                {"type": "text", "text": f"working in the background (sessionID: {child})."}]}}]
    calls: list = []
    _fake_events(monkeypatch, head + sequence_tail(sid, child), calls)
    monkeypatch.setattr(chat_server, "FOLLOW_UP_SECONDS", 0.2)
    monkeypatch.setattr(chat_server, "BACKGROUND_CHECK_SECONDS", 0.0)
    text, tools = [], []

    async def keep(target, item):
        target.append(json.loads(json.dumps(item)) if isinstance(item, dict) else item)

    async def noop(_):
        return None

    result = asyncio.run(asyncio.wait_for(chat_server.run_turn(
        _server(tmp_path), sid, "x", on_text=lambda d: keep(text, d), on_reasoning=noop,
        on_tool=lambda t: keep(tools, t), on_permission=noop), 5))
    return result, "".join(text), tools, calls


def test_the_follow_up_window_starts_when_the_parent_turn_ends(monkeypatch, tmp_path):
    """子が先に終わり親がその後も動いていても、親の終わりから続きを待つ（レビュー指摘）。"""
    def tail(sid, child):
        return [{"type": "session.execution.succeeded", "data": {"sessionID": child}},
                {"type": "session.text.delta", "data": {"sessionID": sid, "delta": "途中"}},
                {"type": "session.execution.succeeded", "data": {"sessionID": sid}},
                {"type": "session.execution.started", "data": {"sessionID": sid}},
                {"type": "session.text.delta", "data": {"sessionID": sid, "delta": "結果"}},
                {"type": "session.execution.succeeded", "data": {"sessionID": sid}}]
    result, text, _, _ = _background_case(tail, monkeypatch, tmp_path)
    assert result["status"] == "succeeded" and text == "途中\n\n結果"


def test_a_failed_parent_stops_background_children(monkeypatch, tmp_path):
    def tail(sid, child):
        return [{"type": "session.execution.failed", "data": {"sessionID": sid, "error": {"message": "x"}}}]
    result, _, tools, calls = _background_case(tail, monkeypatch, tmp_path)
    assert result["status"] == "failed" and tools[-1]["status"] == "failed"
    assert ("POST", "/api/session/ses_child1/interrupt", {}) in calls
