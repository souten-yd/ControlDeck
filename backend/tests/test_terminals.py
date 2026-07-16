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
from app.terminals.stream import OutputJournal, TerminalClientStream, TerminalStreamRegistry


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


def test_terminal_write_retries_partial_and_interrupted_writes(monkeypatch):
    calls: list[bytes] = []
    results: list[object] = [InterruptedError(), 2, 1, 3]

    def fake_write(_fd: int, data: memoryview) -> int:
        calls.append(bytes(data))
        result = results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return int(result)

    monkeypatch.setattr("app.terminals.manager.os.write", fake_write)
    conn = TerminalConnection(master_fd=123, pid=456, owns_process=False)
    assert conn.write(b"abcdef") == 6
    assert calls == [b"abcdef", b"abcdef", b"cdef", b"def"]


def test_terminal_write_rejects_zero_progress(monkeypatch):
    monkeypatch.setattr("app.terminals.manager.os.write", lambda *_args: 0)
    conn = TerminalConnection(master_fd=123, pid=456, owns_process=False)
    try:
        conn.write(b"data")
    except OSError as exc:
        assert "no progress" in str(exc)
    else:
        raise AssertionError("zero-byte PTY write was accepted")


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
    url = ("/api/v1/terminals/0123abcd/connect?rows=24&cols=80"
           "&clientInstanceId=backendtestclient0001&connectionGeneration=3&attachMode=initial&lastSequence=0")
    with admin_client.websocket_connect(url) as websocket:
        assert websocket.receive_json() == {"type": "history_reset", "connectionGeneration": 3}
        assert websocket.receive_json() == {"type": "history_end", "connectionGeneration": 3, "sequence": 0}
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


def test_output_journal_bounds_and_sequence_lookup():
    journal = OutputJournal(max_bytes=6, max_chunks=3)
    first = journal.append(b"aa")
    second = journal.append(b"bb")
    third = journal.append(b"cc")
    assert [entry.sequence for entry in journal.after(0) or []] == [first.sequence, second.sequence, third.sequence]
    fourth = journal.append(b"dd")
    assert journal.byte_count == 6
    assert journal.chunk_count == 3
    assert journal.oldest_sequence == second.sequence
    assert journal.after(0) is None
    assert [entry.data for entry in journal.after(second.sequence) or []] == [b"cc", b"dd"]
    assert journal.after(fourth.sequence + 1) is None


def test_terminal_websocket_resume_sends_only_sequence_delta(admin_client, monkeypatch):
    class FakeConnection:
        initial = b""
        replay = b"FULL-HISTORY"

        def write(self, _data: bytes) -> None:
            pass

        def resize(self, rows: int, cols: int) -> tuple[int, int]:
            return rows, cols

        def close(self) -> None:
            pass

    class FakeStream:
        connection = FakeConnection()
        connection_generation = 2
        journal = OutputJournal()

    stream = FakeStream()
    stream.journal.append(b"already-drawn")
    delta = stream.journal.append(b"only-delta")

    class FakeRegistry:
        def acquire(self, *_args):
            stream.connection_generation = 2
            return stream, False, asyncio.Queue()

        def release(self, *_args) -> None:
            pass

    monkeypatch.setattr("app.terminals.router.streams", FakeRegistry())
    url = ("/api/v1/terminals/0123abcd/connect?rows=24&cols=80"
           "&clientInstanceId=resumetestclient0001&connectionGeneration=2&attachMode=resume&lastSequence=1")
    with admin_client.websocket_connect(url) as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "resume_ready"
        assert ready["throughSequence"] == delta.sequence
        output = websocket.receive_json()
        assert output == {"type": "output", "sequence": delta.sequence, "connectionGeneration": 2}
        assert websocket.receive_bytes() == b"only-delta"
        assert websocket.receive_json() == {
            "type": "resume_end", "connectionGeneration": 2, "sequence": delta.sequence,
        }


def test_terminal_websocket_resume_outside_journal_falls_back_once(admin_client, monkeypatch):
    class FakeConnection:
        initial = b""
        replay = b""

        def capture_replay(self) -> bytes:
            return b"BOUNDED-SNAPSHOT"

        def write(self, _data: bytes) -> None:
            pass

        def resize(self, rows: int, cols: int) -> tuple[int, int]:
            return rows, cols

        def close(self) -> None:
            pass

    class FakeStream:
        connection = FakeConnection()
        connection_generation = 5
        journal = OutputJournal(max_bytes=4, max_chunks=2)

    stream = FakeStream()
    stream.journal.append(b"aa")
    stream.journal.append(b"bb")
    stream.journal.append(b"cc")

    class FakeRegistry:
        def acquire(self, *_args):
            return stream, False, asyncio.Queue()

        def release(self, *_args) -> None:
            pass

    monkeypatch.setattr("app.terminals.router.streams", FakeRegistry())
    url = ("/api/v1/terminals/0123abcd/connect?rows=24&cols=80"
           "&clientInstanceId=fallbacktestclient01&connectionGeneration=5&attachMode=resume&lastSequence=0")
    with admin_client.websocket_connect(url) as websocket:
        assert websocket.receive_json()["type"] == "resume_reset_required"
        assert websocket.receive_json() == {"type": "history_reset", "connectionGeneration": 5}
        assert websocket.receive_bytes() == b"BOUNDED-SNAPSHOT"
        end = websocket.receive_json()
        assert end["type"] == "history_end"
        assert end["connectionGeneration"] == 5
        assert end["sequence"] == stream.journal.latest_sequence


def test_terminal_websocket_paste_ack_is_exact_and_deduplicated(admin_client, monkeypatch):
    class FakeConnection:
        initial = b""
        replay = b""

        def __init__(self):
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> int:
            self.writes.append(data)
            return len(data)

        def close(self) -> None:
            pass

    conn = FakeConnection()
    stream = TerminalClientStream("0123abcd", "pastebackendclient01", conn)  # type: ignore[arg-type]

    class FakeRegistry:
        def acquire(self, _sid, _client, generation, _rows, _cols):
            return stream, False, stream.attach(generation)

        def release(self, active, queue) -> None:
            active.detach(queue)

    monkeypatch.setattr("app.terminals.router.streams", FakeRegistry())
    base = ("/api/v1/terminals/0123abcd/connect?rows=24&cols=80"
            "&clientInstanceId=pastebackendclient01&attachMode=initial&lastSequence=0")
    payload = b"START-" + b"x" * (320 * 1024) + b"-END"
    chunks = [payload[offset:offset + 8192] for offset in range(0, len(payload), 8192)]
    with admin_client.websocket_connect(base + "&connectionGeneration=1") as websocket:
        assert websocket.receive_json()["type"] == "history_reset"
        assert websocket.receive_json()["type"] == "history_end"
        for index, chunk in enumerate(chunks):
            websocket.send_text(json.dumps({
                "type": "input", "inputSequence": index + 1, "pasteId": 1,
                "chunkIndex": index, "final": index == len(chunks) - 1,
                "byteLength": len(chunk), "connectionGeneration": 1,
            }))
            websocket.send_bytes(chunk)
            ack = websocket.receive_json()
            assert ack["type"] == "input_ack"
            assert ack["writtenBytes"] == len(chunk)
    assert b"".join(conn.writes) == payload

    with admin_client.websocket_connect(
        base.replace("attachMode=initial", "attachMode=resume") + "&connectionGeneration=2"
    ) as websocket:
        assert websocket.receive_json()["type"] == "resume_ready"
        assert websocket.receive_json()["type"] == "resume_end"
        index = len(chunks) - 1
        chunk = chunks[index]
        websocket.send_text(json.dumps({
            "type": "input", "inputSequence": index + 1, "pasteId": 1,
            "chunkIndex": index, "final": True, "byteLength": len(chunk),
            "connectionGeneration": 2,
        }))
        websocket.send_bytes(chunk)
        assert websocket.receive_json()["type"] == "input_ack"
    assert b"".join(conn.writes) == payload


def test_terminal_input_sequence_and_cleanup():
    class FakeConnection:
        initial = b""
        replay = b""
        def close(self) -> None:
            pass

    stream = TerminalClientStream("0123abcd", "inputsequencetest01", FakeConnection())  # type: ignore[arg-type]
    assert stream.validate_new_input_sequence(1) is True
    stream.record_input_ack(1, 7, 0, 8192)
    assert stream.validate_new_input_sequence(3) is False
    assert stream.input_ack(1) is not None
    stream.close()
    assert stream.input_ack(1) is None


def test_terminal_stream_rejects_stale_generation_and_cleans_session():
    class FakeConnection:
        initial = b""
        replay = b""

        def __init__(self):
            self.closed = 0

        async def read_loop(self, _on_data):
            await asyncio.Future()

        def close(self) -> None:
            self.closed += 1

    class FakeManager:
        def __init__(self):
            self.connection = FakeConnection()

        def open_connection(self, *_args):
            return self.connection

    async def scenario() -> None:
        fake_manager = FakeManager()
        registry = TerminalStreamRegistry(fake_manager)  # type: ignore[arg-type]
        stream, created, queue = registry.acquire("0123abcd", "streamtestclient0001", 1, 24, 80)
        assert created is True
        registry.release(stream, queue)
        try:
            registry.acquire("0123abcd", "streamtestclient0001", 1, 24, 80)
        except ValueError:
            pass
        else:
            raise AssertionError("stale connection generation was accepted")
        resumed, created_again, new_queue = registry.acquire("0123abcd", "streamtestclient0001", 2, 24, 80)
        assert resumed is stream and created_again is False
        registry.release(stream, queue)  # 旧世代finallyは新世代のcleanupを予約しない
        assert stream.subscriber is new_queue
        assert stream.cleanup_handle is None
        stream.journal.append(b"pending")
        registry.close_session("0123abcd")
        await asyncio.sleep(0)
        assert registry.stream_count() == 0
        assert stream.journal.byte_count == 0
        assert fake_manager.connection.closed == 1

    asyncio.run(scenario())


def test_terminal_stream_journals_disconnected_output_in_order():
    class FakeConnection:
        initial = b""
        replay = b""

        def __init__(self):
            self.output: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def read_loop(self, on_data):
            while True:
                data = await self.output.get()
                if data is None:
                    return
                await on_data(data)

        def close(self) -> None:
            pass

    class FakeManager:
        def __init__(self):
            self.connection = FakeConnection()

        def open_connection(self, *_args):
            return self.connection

    async def scenario() -> None:
        fake_manager = FakeManager()
        registry = TerminalStreamRegistry(fake_manager)  # type: ignore[arg-type]
        stream, _, queue = registry.acquire("0123abcd", "journaltestclient001", 1, 24, 80)
        registry.release(stream, queue)
        await fake_manager.connection.output.put(b"during-1")
        await fake_manager.connection.output.put(b"during-2")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        entries = stream.journal.after(0)
        assert entries is not None
        assert [entry.data for entry in entries] == [b"during-1", b"during-2"]
        resumed, created, _ = registry.acquire("0123abcd", "journaltestclient001", 2, 24, 80)
        assert resumed is stream and created is False
        registry.close_session("0123abcd")

    asyncio.run(scenario())


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
