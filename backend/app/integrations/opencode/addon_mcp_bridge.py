"""Minimal stdio MCP bridge for ControlDeck Add-on agent tools.

This process owns no Add-on or user authority. It forwards requests to the
loopback Host endpoint with the short-lived, user-bound token supplied in its
private OpenCode runtime configuration.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

MAX_MESSAGE_BYTES = 1024 * 1024

# 1 コールを待つ長さ。socket の timeout なので本来は「無音が続いた長さ」だが、
# host は Add-on の job を待ち切ってから一括で返すため、実質は総時間になる。
#
# 130 秒だった。終わりを決めるのは host 側で、そちらは「進捗が 600 秒止まったら
# job を cancel する」（addons/execution.py の wait_agent_tool_job）。bridge が
# それより先に切っても job は走り続けるので、呼び出し側からは「失敗したのに
# 物はできている」状態になる（実測で 130 秒超えの job が 42 件、うち 1770 秒の
# ものまで成功していた）。tool 1 コールで 50 枚作る batch は正常でも十数分かかる。
#
# ここは「host が答えを返すまで」を待つ側なので、正常な処理時間で切らない。
# 進んでいる限り host は返さず、止まれば host が自分で打ち切って返す。
CALL_TIMEOUT_SECONDS = 3600
LATEST_PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", LATEST_PROTOCOL_VERSION}


class BridgeError(RuntimeError):
    pass


RENEW_HEADER = "X-Control-Deck-MCP-Token"

# 起動時の token は設定ファイル由来。host が新しいものを返したらそれに乗り換える。
# 設定ファイルは OpenCode を起動した時点のもので書き換えられないため、
# 更新はこのプロセスが生きている間だけ手元に持つ。
_token: str | None = None


def _current_token() -> str:
    global _token
    if _token is None:
        _token = os.environ.get("CONTROL_DECK_ADDON_MCP_TOKEN", "")
    return _token


def _host_request(path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    global _token
    base_url = os.environ.get("CONTROL_DECK_ADDON_MCP_URL", "").rstrip("/")
    token = _current_token()
    if not base_url.startswith("http://127.0.0.1:") or not token:
        raise BridgeError("ControlDeck Add-on MCP bridge is not configured")
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method="GET" if body is None else "POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "ControlDeck",
            **({} if body is None else {"Content-Type": "application/json"}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=CALL_TIMEOUT_SECONDS) as response:
            content = response.read(MAX_MESSAGE_BYTES + 1)
            renewed = getattr(response, "headers", {}).get(RENEW_HEADER)
            if renewed and renewed != token:
                _token = renewed
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BridgeError("ControlDeck Add-on MCP request failed") from exc
    if len(content) > MAX_MESSAGE_BYTES:
        raise BridgeError("ControlDeck Add-on MCP response is too large")
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BridgeError("ControlDeck Add-on MCP response is invalid") from exc
    if not isinstance(value, dict):
        raise BridgeError("ControlDeck Add-on MCP response is invalid")
    return value


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid Request"}}
    if request_id is None:
        return None
    try:
        if method == "initialize":
            params = message.get("params")
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            protocol = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
            result = {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ControlDeck Add-on tools", "version": "1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = _host_request("/tools")
        elif method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                raise BridgeError("Tool call is invalid")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise BridgeError("Tool arguments must be an object")
            try:
                value = _host_request("/call", payload={"name": params["name"], "arguments": arguments})
            except BridgeError as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            else:
                result = {
                    "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                    "structuredContent": value,
                    "isError": False,
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except BridgeError as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32000, "message": str(exc)},
        }


def main() -> int:
    for raw in sys.stdin.buffer:
        if len(raw) > MAX_MESSAGE_BYTES:
            return 1
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            response: dict[str, Any] | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            response = handle_message(message) if isinstance(message, dict) else {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
