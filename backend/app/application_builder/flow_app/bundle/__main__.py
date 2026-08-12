"""配布アプリのエントリポイント。引数なしならローカルGUI、--input ならCLIで実行する。

追加ランタイムは不要（python3 のみ）。ネットワークへ待ち受けるのは 127.0.0.1 既定。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from flowapp.runner import FlowError, run_flow

MAX_REQUEST_BYTES = 4 * 1024 * 1024


def _resource(name: str) -> str:
    return resources.files("flowapp").joinpath(name).read_text(encoding="utf-8")


def load_flow() -> dict:
    return json.loads(_resource("flow.json"))


def _load_env_file(path: str) -> None:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _coerce(value: str, field: dict) -> object:
    kind = str(field.get("type") or "string")
    if kind in {"integer", "number"}:
        try:
            return int(value) if kind == "integer" else float(value)
        except ValueError:
            return value
    if kind == "boolean":
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if kind in {"array", "object"}:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _normalize_input(raw: dict, fields: list[dict]) -> dict:
    by_name = {str(field.get("name")): field for field in fields}
    result: dict[str, object] = {}
    for key, value in (raw or {}).items():
        field = by_name.get(str(key))
        result[str(key)] = _coerce(value, field) if field is not None and isinstance(value, str) else value
    return result


class Handler(BaseHTTPRequestHandler):
    flow: dict = {}
    server_version = "FlowApp"

    def log_message(self, *_args) -> None:  # 既定のstderrログは出さない
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0]
        if path in {"/", "/index.html"}:
            meta = {
                "name": self.flow.get("name"), "description": self.flow.get("description"),
                "version": self.flow.get("version"), "inputs": self.flow.get("inputs", []),
                "outputs": self.flow.get("outputs", []), "generatedAt": self.flow.get("generatedAt"),
            }
            page = _resource("ui.html").replace(
                "/*__FLOW_APP__*/null", json.dumps(meta, ensure_ascii=False),
            )
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/meta":
            self._json(200, {"name": self.flow.get("name"), "inputs": self.flow.get("inputs", [])})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.split("?", 1)[0] != "/api/run":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"error": "Content-Length が不正です"})
            return
        if length > MAX_REQUEST_BYTES:
            self._json(413, {"error": "入力が大きすぎます"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "JSONを解析できません"})
            return
        data = _normalize_input(payload if isinstance(payload, dict) else {}, self.flow.get("inputs", []))
        try:
            result = run_flow(self.flow["definition"], data)
        except FlowError as exc:
            self._json(200, {"status": "FAILED", "error": str(exc), "outputs": {}, "nodes": []})
            return
        except Exception as exc:  # noqa: BLE001 - GUIへ理由を返して落とさない
            self._json(200, {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}", "outputs": {}, "nodes": []})
            return
        self._json(200, result)


def serve(flow: dict, host: str, port: int, open_browser: bool) -> int:
    Handler.flow = flow
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"{flow.get('name')} を起動しました: {url}")
    print("終了する場合は Ctrl+C を押してください。")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました。")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    flow = load_flow()
    parser = argparse.ArgumentParser(
        prog=flow.get("slug") or "flow-app",
        description=f"{flow.get('name')} — {flow.get('description') or 'Control Deck Workflowから書き出した実行アプリ'}",
    )
    parser.add_argument("--input", help="入力JSON（例: '{\"text\":\"hello\"}'）。'-' で標準入力から読み込む")
    parser.add_argument("--input-file", help="入力JSONファイル")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="入力を個別指定（複数可）")
    parser.add_argument("--env-file", help="環境変数ファイル（KEY=VALUE 形式）")
    parser.add_argument("--json", action="store_true", help="結果をJSONで出力する")
    parser.add_argument("--gui", action="store_true", help="ローカルGUIを起動する（引数なしと同じ）")
    parser.add_argument("--host", default="127.0.0.1", help="GUIの待受host（既定: 127.0.0.1）")
    parser.add_argument("--port", type=int, default=0, help="GUIの待受port（既定: 空きportを自動選択）")
    parser.add_argument("--open", action="store_true", help="GUI起動時にブラウザを開く")
    parser.add_argument("--info", action="store_true", help="このアプリの入出力を表示して終了する")
    args = parser.parse_args(argv)

    if args.env_file:
        _load_env_file(args.env_file)
    if args.info:
        print(f"{flow.get('name')}  (workflow #{flow.get('workflowId')}, 書き出し {flow.get('generatedAt')})")
        print(f"ノード数: {len(flow['definition'].get('nodes', []))}")
        for field in flow.get("inputs", []):
            required = "必須" if field.get("required") else "任意"
            print(f"  入力  {field.get('name')} ({field.get('type')}, {required}) {field.get('label') or ''}")
        for field in flow.get("outputs", []):
            print(f"  出力  {field.get('name')} ({field.get('type')})")
        return 0

    raw: dict = {}
    if args.input_file:
        with open(args.input_file, encoding="utf-8") as handle:
            raw = json.load(handle)
    elif args.input == "-":
        raw = json.loads(sys.stdin.read() or "{}")
    elif args.input:
        raw = json.loads(args.input)
    for item in args.set:
        if "=" not in item:
            parser.error(f"--set は KEY=VALUE 形式で指定してください: {item}")
        key, value = item.split("=", 1)
        raw[key] = value
    if not isinstance(raw, dict):
        parser.error("入力JSONはオブジェクトで指定してください")

    if args.gui or (not args.input and not args.input_file and not args.set):
        return serve(flow, args.host, args.port, args.open)

    data = _normalize_input(raw, flow.get("inputs", []))
    try:
        result = run_flow(flow["definition"], data)
    except FlowError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for name, item in (result.get("outputs") or {}).items():
            value = item.get("value")
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            print(f"{name}: {rendered}")
        if not result.get("outputs"):
            print("（表示出力はありません）", file=sys.stderr)
    if result.get("status") != "SUCCEEDED":
        print(f"失敗: {result.get('error')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
