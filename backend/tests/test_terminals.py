import os
import time

from app.terminals.manager import TerminalManager, tmux_available


def test_fallback_pty_lifecycle():
    """tmux 有無に関わらず、セッション作成→一覧→終了が動作すること。"""
    mgr = TerminalManager()
    session = mgr.create_session()
    try:
        sessions = mgr.list_sessions()
        assert any(s["id"] == session["id"] for s in sessions)
        if not tmux_available():
            # フォールバック時は PTY へ書き込み→読み出しできる
            conn = mgr.open_connection(session["id"], rows=24, cols=80)
            conn.write(b"echo cd-test-$((6*7))\n")
            deadline = time.time() + 5
            out = b""
            while time.time() < deadline and b"cd-test-42" not in out:
                try:
                    out += os.read(conn.master_fd, 65536)
                except BlockingIOError:
                    time.sleep(0.05)
            assert b"cd-test-42" in out
    finally:
        mgr.kill_session(session["id"])
    assert all(s["id"] != session["id"] for s in mgr.list_sessions())


def test_terminal_api_requires_permission(client):
    client.cookies.clear()
    assert client.get("/api/v1/terminals").status_code == 401


def test_terminal_api_admin(admin_client):
    from tests.conftest import CSRF_HEADERS

    r = admin_client.get("/api/v1/terminals")
    assert r.status_code == 200
    body = r.json()
    assert "tmux" in body and "sessions" in body

    r = admin_client.post("/api/v1/terminals", headers=CSRF_HEADERS)
    assert r.status_code == 201
    sid = r.json()["id"]
    r = admin_client.delete(f"/api/v1/terminals/{sid}", headers=CSRF_HEADERS)
    assert r.status_code == 200
