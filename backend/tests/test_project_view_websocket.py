"""Project Lab の preview の鍵の入口と、WebSocket（HMR）の中継。

目的:
- preview は sandbox の iframe（生まれが null）で開くので Cookie を送れない。URL の run ごとの鍵で通し、
  鍵が違う・終わった run は通さない。ControlDeck の Cookie・Origin は上流へ渡さない。
- WebSocket は上流と双方向に中継し、サブプロトコル（Vite の vite-hmr）・文字・binary・close code を通す。
- command に {base} を渡した run は、接頭辞を付けたまま上流へ送る（Vite の --base で HMR が同じ接頭辞へ来る）。
根拠: 2026-11-21 実測。前の preview では、sandbox の中の Vite は module script が CORS で 1 本も読めず、
HMR はサイトの根元 ws://host/ へ繋ぎに来て 403 だった。
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.websockets import WebSocketDisconnect

from app.database import SessionLocal
from app.models import ProjectRun
from app.project_lab import runs


class _Upstream:
    """小さな上流（WebSocket の echo）。受けた握手の頭と path を控える。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.port = 0
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Future | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        from websockets.asyncio.server import serve

        async def handler(connection):
            self.requests.append({
                "path": connection.request.path,
                "headers": {k.lower(): v for k, v in connection.request.headers.raw_items()},
                "subprotocol": connection.subprotocol,
            })
            async for message in connection:
                if message == "close-me":
                    await connection.close(code=4001, reason="bye")
                    return
                await connection.send(message)

        async def main():
            self._stop = asyncio.get_running_loop().create_future()
            async with serve(handler, "127.0.0.1", 0, subprotocols=["vite-hmr"]) as server:
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                await self._stop

        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(main())

    def __enter__(self):
        self._thread.start()
        assert self._ready.wait(5)
        return self

    def __exit__(self, *exc):
        assert self._loop and self._stop
        self._loop.call_soon_threadsafe(self._stop.set_result, None)
        self._thread.join(5)


def _make_run(port: int, *, status: str = "RUNNING", command: list[str] | None = None) -> int:
    with SessionLocal() as db:
        row = ProjectRun(project_id="demo", project_name="Demo", profile_id="dev", profile_type="web",
                         status=status, web_port=port, command_json=json.dumps(command or ["node", "server.js"]))
        db.add(row)
        db.commit()
        row.unit_name = f"control-deck-project-run-{row.id}"
        db.commit()
        return row.id


def _url(run_id: int, path: str = "") -> str:
    with SessionLocal() as db:
        return runs.preview_base(db.get(ProjectRun, run_id)) + path


@pytest.fixture()
def live_runs(monkeypatch):
    """systemd を使わずに、DB の status のまま動いている扱いにする。"""
    monkeypatch.setattr(runs, "refresh_run", lambda db, row: row)
    monkeypatch.setattr(runs, "web_preview_ready", lambda row: row.status in runs.RUNNING_STATES)


def test_websocket_relays_messages_subprotocol_and_close(client, live_runs):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        client.cookies.set("cd_session", "secret-session")
        with client.websocket_connect(_url(run_id, "?token=hmr"), subprotocols=["vite-hmr"],
                                      headers={"origin": "null"}) as ws:
            assert ws.accepted_subprotocol == "vite-hmr"
            ws.send_text("hello")
            assert ws.receive_text() == "hello"
            ws.send_bytes(b"\x00\x01binary")
            assert ws.receive_bytes() == b"\x00\x01binary"
            ws.send_text("close-me")
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
            assert closed.value.code == 4001
        client.cookies.clear()
    seen = upstream.requests[0]
    assert seen["path"] == "/?token=hmr"              # 接頭辞を外し、query はそのまま
    assert seen["subprotocol"] == "vite-hmr"
    assert "cookie" not in seen["headers"]             # ControlDeck の Cookie は渡さない
    assert "origin" not in seen["headers"]
    assert seen["headers"]["host"] == f"127.0.0.1:{upstream.port}"   # Vite の Host 検査を通る


def test_websocket_rejects_wrong_token_and_finished_run(client, live_runs):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        with pytest.raises(WebSocketDisconnect) as wrong:
            with client.websocket_connect(f"/project-view/r/{run_id}/{'0' * 32}/"):
                pass
        assert wrong.value.code == 4404
        finished = _make_run(upstream.port, status="SUCCEEDED")
        with pytest.raises(WebSocketDisconnect) as ended:
            with client.websocket_connect(_url(finished)):
                pass
        assert ended.value.code == 4404
    assert upstream.requests == []                     # どちらも上流へは繋がない


def test_websocket_keeps_prefix_when_command_has_base(client, live_runs):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port, command=["node", "vite.js", "--base", "{base}"])
        with client.websocket_connect(_url(run_id), subprotocols=["vite-hmr"]) as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "ping"
    assert upstream.requests[0]["path"] == _url(run_id)


class _HttpUpstream(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
        body = b"export const ok = 1;"
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript")
        self.send_header("Set-Cookie", "upstream=1")
        self.send_header("Access-Control-Allow-Origin", "http://localhost:5173")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_token_http_route_needs_no_cookie_and_allows_null_origin(client, live_runs):
    _HttpUpstream.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HttpUpstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run_id = _make_run(server.server_address[1])
        client.cookies.set("cd_session", "secret-session")
        response = client.get(_url(run_id, "src/main.js"), headers={"origin": "null"})
        client.cookies.clear()
        assert response.status_code == 200 and response.text == "export const ok = 1;"
        # 生まれが null の module script が読めるように。上流の CORS 頭は置き換える
        assert response.headers["access-control-allow-origin"] == "*"
        assert "set-cookie" not in response.headers
        assert _HttpUpstream.seen[0]["path"] == "/src/main.js"
        assert "cookie" not in _HttpUpstream.seen[0]["headers"]
        assert client.get(f"/project-view/r/{run_id}/{'0' * 32}/src/main.js").status_code == 404
        # Cookie の入口（前からある）は CORS を開けない
        with SessionLocal() as db:
            assert runs.preview_token(db.get(ProjectRun, run_id)) not in str(db.get(ProjectRun, run_id).command_json)
    finally:
        server.shutdown()


def test_launch_fills_base_but_does_not_record_the_token(tmp_path, monkeypatch):
    """{base} は run の番号が決まってから埋める。記録（command_json）には鍵を残さない。"""
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class Result:
            returncode = 0
            stdout = stderr = ""
        return Result()

    monkeypatch.setattr(runs, "_systemd_tools", lambda: ("systemd-run", "systemctl", "journalctl"))
    monkeypatch.setattr(runs.subprocess, "run", fake_run)
    monkeypatch.setattr(runs, "_artifact_snapshot", lambda project, manifest: {})
    monkeypatch.setattr(runs, "refresh_run", lambda db, row: row)
    with SessionLocal() as db:
        for row in db.query(ProjectRun).filter(ProjectRun.status.in_(runs.RUNNING_STATES)).all():
            row.status = "SUCCEEDED"
        db.commit()
        row = runs._launch(
            db, project=tmp_path, project_id="base-demo", project_name="Base", profile_id="dev", profile_type="web",
            command=["node", "vite.js", "--base", "{base}"], executable="/usr/bin/node", cwd=tmp_path,
            environment={}, timeout_seconds=60, web_port=40123, created_by=None, manifest=None,
        )
        base = runs.preview_base(row)
        assert captured["argv"][-2:] == ["--base", base]
        assert f"--setenv=CONTROL_DECK_PREVIEW_BASE={base}" in captured["argv"]
        assert "{base}" in row.command_json and runs.preview_token(row) not in row.command_json
        assert runs.preview_keeps_base(row)
        row.status = "SUCCEEDED"
        db.commit()
