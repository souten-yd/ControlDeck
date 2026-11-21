"""見張りモードの画面側: live.js の差し込み（鍵付き入口の HTML だけ）と、WebSocket `__cd/live` の受け口。

目的:
- 鍵付き入口の応答が text/html のときだけ、`</head>`（無ければ `</body>`、無ければ末尾）の前へ script を 1 本入れる。
  接頭辞が正しい・content-length が合う・圧縮された HTML は圧縮なし（identity）で頼み直す・8 MiB 超と
  HTML 以外は触らない。`__cd/live.js` は上流へ送らず ControlDeck が答える。
- `__cd/live` は run の鍵で認証する。見張り役の状態を `status` で、restart の再起動を `reload` で、AI の知らせ
  （notes ファイル）を `note` で画面へ送る。native・見張り無しなら status を送らない。
- 画面から来た記録は `.controldeck/feedback/<run_id>.jsonl` へ追記する。1 件 16KB・1 秒 50 件・1 run 20MB まで。
根拠: 2026-11-21 の設計。記録の入口は誰でも（鍵があれば）叩けるので、ディスクと CPU を食い尽くされない上限が要る。
Vite の HMR など、アプリ自身の WebSocket・script を壊さないため、HTML 以外は今のまま流す。
"""
from __future__ import annotations

import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from starlette.websockets import WebSocketDisconnect

from app.project_lab import live_channel, live_paths, runs, service, webview
from tests.test_project_view_websocket import _make_run, _Upstream, _url, live_runs  # noqa: F401


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append({"path": self.path, "encoding": self.headers.get("accept-encoding", "")})
        content_type, body, compress = type(self).routes.get(self.path.split("?")[0], ("text/plain", b"", False))
        headers = {}
        if compress and "gzip" in self.headers.get("accept-encoding", ""):
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def http_upstream():
    _Handler.routes, _Handler.seen = {}, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def _script(run_id: int) -> str:
    return f'<script src="{_url(run_id)}__cd/live.js" defer></script>'


def test_html_gets_exactly_one_script_before_head_close(client, live_runs, http_upstream):  # noqa: F811
    page = "<html><head><title>日本語</title></head><body>hi</body></html>"
    _Handler.routes["/"] = ("text/html; charset=utf-8", page.encode(), False)
    run_id = _make_run(http_upstream)
    response = client.get(_url(run_id))
    tag = _script(run_id)
    assert response.text == page.replace("</head>", tag + "</head>")
    assert response.text.count("__cd/live.js") == 1
    assert response.headers["content-length"] == str(len(response.content))
    assert response.headers["access-control-allow-origin"] == "*"


def test_script_goes_before_body_close_or_at_the_end(client, live_runs, http_upstream):  # noqa: F811
    _Handler.routes["/a"] = ("text/html", b"<body>x</BODY>", False)
    _Handler.routes["/b"] = ("text/html", b"<p>fragment</p>", False)
    run_id = _make_run(http_upstream)
    tag = _script(run_id)
    assert client.get(_url(run_id, "a")).text == f"<body>x{tag}</BODY>"
    assert client.get(_url(run_id, "b")).text == f"<p>fragment</p>{tag}"


def test_non_html_and_oversized_html_pass_through_untouched(client, live_runs, http_upstream, monkeypatch):  # noqa: F811
    _Handler.routes["/app.js"] = ("text/javascript", b"</head> export const a = 1;", False)
    _Handler.routes["/data.json"] = ("application/json", b'{"html":"</head>"}', False)
    big = b"<html><head></head><body>" + b"x" * 5000 + b"</body></html>"
    _Handler.routes["/big"] = ("text/html", big, False)
    run_id = _make_run(http_upstream)
    assert client.get(_url(run_id, "app.js")).content == b"</head> export const a = 1;"
    assert client.get(_url(run_id, "data.json")).content == b'{"html":"</head>"}'
    monkeypatch.setattr(webview, "MAX_INJECT_BYTES", 4096)        # 8 MiB の代わりに小さくして確かめる
    over = client.get(_url(run_id, "big"))
    assert over.content == big and "__cd/live.js" not in over.text


def test_html_just_under_the_limit_is_still_injected(client, live_runs, http_upstream, monkeypatch):  # noqa: F811
    body = b"<html><head></head>" + b"x" * 1000 + b"</html>"
    _Handler.routes["/p"] = ("text/html", body, False)
    run_id = _make_run(http_upstream)
    monkeypatch.setattr(webview, "MAX_INJECT_BYTES", len(body))
    assert "__cd/live.js" in client.get(_url(run_id, "p")).text


def test_8mib_is_the_real_limit():
    assert webview.MAX_INJECT_BYTES == 8 * 1024 * 1024


def test_compressed_html_is_requested_again_without_compression(client, live_runs, http_upstream):  # noqa: F811
    page = b"<html><head></head><body>compressed</body></html>"
    _Handler.routes["/z"] = ("text/html", page, True)
    run_id = _make_run(http_upstream)
    response = client.get(_url(run_id, "z"), headers={"accept-encoding": "gzip"})
    assert "__cd/live.js" in response.text and "compressed" in response.text
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(response.content))
    encodings = [item["encoding"] for item in _Handler.seen]
    assert "gzip" in encodings[0] and encodings[1] == "identity"      # 1 回目は圧縮可、頼み直しは identity
    _Handler.seen.clear()
    _Handler.routes["/app.js"] = ("text/javascript", b"x" * 100, True)
    client.get(_url(run_id, "app.js"), headers={"accept-encoding": "gzip"})
    assert len(_Handler.seen) == 1                                    # HTML 以外は頼み直さない


def test_live_js_is_answered_by_control_deck_even_while_upstream_is_down(client, lab, http_upstream, monkeypatch):
    run_id = _make_run(http_upstream)
    monkeypatch.setattr(runs, "web_preview_ready", lambda row: False)    # ビルド中・再起動中
    response = client.get(_url(run_id, "__cd/live.js"))
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/javascript")
    assert b"__cd/live" in response.content and response.headers["cache-control"] == "no-store"
    assert _Handler.seen == []                                           # 上流へは送らない
    assert client.get(f"/project-view/r/{run_id}/{'0' * 32}/__cd/live.js").status_code == 404


# ---- WebSocket ----

@pytest.fixture()
def lab(tmp_path, monkeypatch, live_runs):  # noqa: F811
    root = tmp_path / "CodeDEV"
    (root / "demo" / ".controldeck").mkdir(parents=True)
    monkeypatch.setattr(service, "project_root", lambda: root.resolve())
    monkeypatch.setattr(live_paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(webview, "LIVE_POLL_SECONDS", 0.02)
    live_channel._windows.clear()
    return root.resolve() / "demo"


def _feedback(project, run_id) -> list[dict]:
    path = live_paths.feedback_file(project, run_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_live_websocket_rejects_a_wrong_key_and_a_finished_run(client, lab):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        with pytest.raises(WebSocketDisconnect) as wrong:
            with client.websocket_connect(f"/project-view/r/{run_id}/{'0' * 32}/__cd/live"):
                pass
        assert wrong.value.code == 4404
        finished = _make_run(upstream.port, status="SUCCEEDED")
        with pytest.raises(WebSocketDisconnect) as ended:
            with client.websocket_connect(_url(finished, "__cd/live")):
                pass
        assert ended.value.code == 4404
    assert upstream.requests == []        # 上流の WebSocket へは中継しない


def test_records_are_appended_with_time_and_source(client, lab):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            ws.send_text(json.dumps({"kind": "click", "target": {"selector": "#go"}, "ts": "forged", "source": "ai"}))
            ws.send_text(json.dumps({"kind": "input", "length": 4}))
            assert _wait(lambda: len(_feedback(lab, run_id)) == 2)
    first, second = _feedback(lab, run_id)
    assert first["kind"] == "click" and first["target"] == {"selector": "#go"}
    assert first["source"] == "page" and first["ts"] != "forged" and second["length"] == 4
    assert live_paths.feedback_file(lab, run_id).stat().st_mode & 0o777 == 0o600
    assert upstream.requests == []


def test_a_record_over_16kb_is_dropped_and_invalid_json_is_refused(client, lab):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            ws.send_text(json.dumps({"kind": "note", "text": "x" * (16 * 1024)}))
            assert json.loads(ws.receive_text()) == {"type": "dropped", "reason": "too_large"}
            ws.send_text("not json")
            assert json.loads(ws.receive_text())["reason"] == "invalid"
            ws.send_text("[1,2]")
            assert json.loads(ws.receive_text())["reason"] == "invalid"
            small = json.dumps({"kind": "note", "text": "x" * (16 * 1024 - 100)})
            assert len(small.encode()) <= 16 * 1024
            ws.send_text(small)
            assert _wait(lambda: len(_feedback(lab, run_id)) == 1)


def test_records_are_limited_to_50_per_second(client, lab, monkeypatch):
    monkeypatch.setattr(live_channel.time, "monotonic", lambda: 1000.0)   # 同じ 1 秒の中
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            for index in range(60):
                ws.send_text(json.dumps({"kind": "click", "n": index}))
            reasons = [json.loads(ws.receive_text())["reason"] for _ in range(10)]
            assert reasons == ["rate"] * 10
            assert _wait(lambda: len(_feedback(lab, run_id)) == 50)
        monkeypatch.setattr(live_channel.time, "monotonic", lambda: 1001.5)  # 1 秒たてば、また受ける
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            ws.send_text(json.dumps({"kind": "click", "n": 100}))
            assert _wait(lambda: len(_feedback(lab, run_id)) == 51)


def test_a_run_stops_accepting_records_at_the_total_limit(client, lab, monkeypatch):
    monkeypatch.setattr(live_channel, "MAX_FEEDBACK_BYTES", 1300)
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            for _ in range(5):
                ws.send_text(json.dumps({"kind": "note", "text": "y" * 400}))
            replies = [json.loads(ws.receive_text()) for _ in range(3)]
            assert {item["reason"] for item in replies} == {"quota"}
            assert _wait(lambda: len(_feedback(lab, run_id)) == 2)
    assert live_paths.feedback_file(lab, run_id).stat().st_size <= 1300


def _append(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write((payload if isinstance(payload, str) else json.dumps(payload)) + "\n")


def _state(phase, **extra):
    return {"ts": "t", "phase": phase, "changed": [], "exit_code": None, "error_tail": "", **extra}


def test_status_reload_and_note_reach_the_page_in_restart_mode(client, lab):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        live_paths.write_spec(run_id, {"mode": "restart"})
        state, notes = live_paths.state_file(lab, run_id), live_paths.notes_file(lab, run_id)
        _append(state, _state("running"))
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            first = json.loads(ws.receive_text())
            assert first["type"] == "status" and first["phase"] == "running"    # つないだ時の今の状態
            _append(state, _state("building", changed=["app.py"]))
            building = json.loads(ws.receive_text())
            assert building["phase"] == "building" and building["changed"] == ["app.py"]
            _append(state, _state("failed", exit_code=1, error_tail="boom api_token=secret123"))
            failed = json.loads(ws.receive_text())
            assert failed["phase"] == "failed" and "secret123" not in failed["error_tail"]
            _append(state, _state("running"))
            assert json.loads(ws.receive_text())["type"] == "status"
            assert json.loads(ws.receive_text()) == {"type": "reload"}          # 起き直したら読み込み直す
            _append(notes, "ボタンの色を直しました")
            assert json.loads(ws.receive_text()) == {"type": "note", "text": "ボタンの色を直しました"}


def test_rerun_mode_sends_status_but_never_reload(client, lab):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        live_paths.write_spec(run_id, {"mode": "rerun"})
        state, notes = live_paths.state_file(lab, run_id), live_paths.notes_file(lab, run_id)
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            _append(state, _state("running"))
            assert json.loads(ws.receive_text())["phase"] == "running"
            _append(state, _state("exited", exit_code=0))
            assert json.loads(ws.receive_text())["phase"] == "exited"
            _append(notes, "done")
            assert json.loads(ws.receive_text())["type"] == "note"             # reload が挟まらない


def test_native_or_unwatched_runs_get_no_status(client, lab):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)                                       # spec なし = native・見張り無し
        state, notes = live_paths.state_file(lab, run_id), live_paths.notes_file(lab, run_id)
        _append(state, _state("running"))
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            _append(state, _state("building"))
            _append(notes, "hello")
            assert json.loads(ws.receive_text()) == {"type": "note", "text": "hello"}   # 最初に届くのは note だけ
            ws.send_text(json.dumps({"kind": "click"}))                         # 記録は native でも使える
            assert _wait(lambda: len(_feedback(lab, run_id)) == 1)


def test_old_notes_are_not_replayed_on_connect(client, lab):
    with _Upstream() as upstream:
        run_id = _make_run(upstream.port)
        notes = live_paths.notes_file(lab, run_id)
        _append(notes, "old news")
        with client.websocket_connect(_url(run_id, "__cd/live")) as ws:
            _append(notes, "fresh")
            assert json.loads(ws.receive_text())["text"] == "fresh"
