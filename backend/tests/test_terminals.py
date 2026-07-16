import asyncio
import json
import os
import signal
import subprocess
import time

from app.terminals.manager import (
    HISTORY_BYTES,
    HISTORY_TRUNCATED,
    TerminalConnection,
    TerminalManager,
    _bounded_history,
    _normalize_terminal_size,
    _target,
    tmux_available,
)


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


def test_terminal_size_is_bounded_and_duplicate_resize_is_suppressed(monkeypatch):
    assert _normalize_terminal_size(1, 1) == (3, 10)
    assert _normalize_terminal_size(999, 9999) == (500, 1000)
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        "app.terminals.manager._set_winsize",
        lambda fd, rows, cols: calls.append((fd, rows, cols)),
    )
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr("app.terminals.manager.os.getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(
        "app.terminals.manager.os.killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
    )
    conn = TerminalConnection(master_fd=123, pid=456, owns_process=False, rows=24, cols=80)
    assert conn.resize(24, 80) == (24, 80)
    assert conn.resize(1, 1) == (3, 10)
    assert conn.resize(1, 1) == (3, 10)
    assert calls == [(123, 3, 10)]
    assert signals == []

    tmux_conn = TerminalConnection(master_fd=124, pid=500, owns_process=True, rows=24, cols=80)
    assert tmux_conn.resize(30, 100) == (30, 100)
    assert signals == [(501, signal.SIGWINCH)]


def test_terminal_websocket_resize_ack_tracks_generations(admin_client, monkeypatch):
    class FakeConnection:
        initial = b""
        replay = b""

        def __init__(self):
            self.resize_calls: list[tuple[int, int]] = []
            self.fail = False

        async def read_loop(self, _on_data):
            await asyncio.Future()

        def resize(self, rows: int, cols: int) -> tuple[int, int]:
            if self.fail:
                raise OSError("test resize failure")
            self.resize_calls.append((rows, cols))
            return _normalize_terminal_size(rows, cols)

        def size_diagnostics(self) -> dict[str, object]:
            return {"ptyRows": 19, "ptyCols": 44}

        def close(self) -> None:
            pass

    conn = FakeConnection()
    monkeypatch.setattr("app.terminals.router.manager.open_connection", lambda *_args, **_kwargs: conn)
    with admin_client.websocket_connect("/api/v1/terminals/0123abcd/connect?rows=24&cols=80") as websocket:
        assert websocket.receive_json() == {"type": "history_reset"}
        assert websocket.receive_json() == {"type": "history_end"}
        websocket.send_text(json.dumps({
            "type": "resize",
            "rows": 19,
            "cols": 44,
            "resizeGeneration": 12,
            "connectionGeneration": 3,
            "debug": True,
        }))
        ack = websocket.receive_json()
        assert ack["type"] == "resize_ack"
        assert ack["success"] is True
        assert ack["rows"] == 19 and ack["cols"] == 44
        assert ack["resizeGeneration"] == 12
        assert ack["connectionGeneration"] == 3
        assert ack["diagnostics"]["ptyRows"] == 19
        assert conn.resize_calls == [(19, 44)]

        websocket.send_text(json.dumps({
            "type": "size_probe",
            "resizeGeneration": 12,
            "connectionGeneration": 3,
        }))
        probe = websocket.receive_json()
        assert probe["type"] == "size_probe_result"
        assert probe["resizeGeneration"] == 12
        assert probe["connectionGeneration"] == 3
        assert probe["diagnostics"] == {"ptyRows": 19, "ptyCols": 44}

        conn.fail = True
        websocket.send_text(json.dumps({
            "type": "resize",
            "rows": 20,
            "cols": 45,
            "resizeGeneration": 13,
            "connectionGeneration": 3,
        }))
        failed = websocket.receive_json()
        assert failed["type"] == "resize_ack"
        assert failed["success"] is False
        assert failed["resizeGeneration"] == 13


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
