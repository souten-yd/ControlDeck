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
LATEST_PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", LATEST_PROTOCOL_VERSION}


class BridgeError(RuntimeError):
    pass


def _host_request(path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url = os.environ.get("CONTROL_DECK_ADDON_MCP_URL", "").rstrip("/")
    token = os.environ.get("CONTROL_DECK_ADDON_MCP_TOKEN", "")
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
        with urllib.request.urlopen(request, timeout=130) as response:
            content = response.read(MAX_MESSAGE_BYTES + 1)
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
            value = _host_request("/call", payload={"name": params["name"], "arguments": arguments})
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
