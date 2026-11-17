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
                       "sudo ls", "find . -delete", "some-unknown-tool --flag", "cat a | sh"]) {{
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
                   "npm install left-pad", "sudo ls", "find . -delete", "some-unknown-tool --flag", "cat a | sh"):
        assert out[danger] == "ask", danger
    assert out["edit"] == "allow" and out["webfetch"] is None and out["askMode"] is None
    assert out["hook"][:3] == ["allow", "deny", "ask"] and out["hook"][3].startswith("Auto")


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
    assert calls[0] == ("POST", f"/api/session/{sid}/prompt", {"text": "片付けて"})
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

    async def fake_turn(srv, sid, text, *, on_text, on_reasoning, on_tool, on_permission):
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
