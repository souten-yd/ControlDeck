import os
import subprocess
import time

from app.terminals.manager import HISTORY_BYTES, HISTORY_TRUNCATED, TerminalManager, _bounded_history, _target, tmux_available


def test_fallback_pty_lifecycle():
    """tmux 有無に関わらず、セッション作成→一覧→終了が動作すること。"""
    mgr = TerminalManager()
    session = mgr.create_session()
    try:
        sessions = mgr.list_sessions()
        assert any(s["id"] == session["id"] for s in sessions)
        if tmux_available():
            status = subprocess.run(
                ["tmux", "show-options", "-v", "-t", f"cdterm-{session['id']}", "status"],
                check=True, capture_output=True, text=True, timeout=10,
            )
            assert status.stdout.strip() == "off"
        else:
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


def test_terminal_session_id_and_history_bounds():
    assert _target("0123abcd") == "cdterm-0123abcd"
    try:
        _target("../../server")
    except KeyError:
        pass
    else:
        raise AssertionError("invalid tmux target was accepted")

    raw = b"old\n" + b"x" * HISTORY_BYTES + b"\nlast\n"
    bounded, truncated = _bounded_history(raw)
    assert truncated is True
    assert bounded.startswith(HISTORY_TRUNCATED)
    assert bounded.endswith(b"last\n")
    assert len(bounded) <= HISTORY_BYTES + len(HISTORY_TRUNCATED)


def test_tmux_replays_ten_thousand_lines():
    if not tmux_available():
        return
    mgr = TerminalManager()
    session = mgr.create_session()
    target = "cdterm-" + session["id"]
    conn = None
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "printf 'WRAP-%0120d-END\\n' 0", "Enter"],
            check=True, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "seq -f 'HIST-%05g' 1 10000", "Enter"],
            check=True, capture_output=True, timeout=10,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            captured = subprocess.run(
                ["tmux", "capture-pane", "-p", "-S", "-", "-t", target],
                capture_output=True, timeout=10,
            ).stdout
            if b"HIST-10000" in captured:
                break
            time.sleep(0.05)
        conn = mgr.open_connection(session["id"], rows=24, cols=80)
        assert b"HIST-00001" in conn.replay
        assert b"HIST-10000" in conn.replay
        assert conn.replay.count(b"HIST-00001") == 1
        assert b"WRAP-" + b"0" * 120 + b"-END" in conn.replay
    finally:
        if conn is not None:
            conn.close()
        mgr.kill_session(session["id"])
