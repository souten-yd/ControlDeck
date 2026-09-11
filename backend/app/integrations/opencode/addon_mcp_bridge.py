"""Minimal stdio MCP bridge for ControlDeck Add-on agent tools.

This process owns no Add-on or user authority. It forwards requests to the
loopback Host endpoint with the short-lived, user-bound token supplied in its
private OpenCode runtime configuration.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MAX_MESSAGE_BYTES = 1024 * 1024

# 道具の結果をそのまま会話へ載せてよい長さ（文字数）。
#
# 実測: 262,142 トークンで打ち止めた会話の内訳は、道具の結果が 48.1% で最大だった。
# 上限が MAX_MESSAGE_BYTES（1 MB）しか無く、1 件で 30 万トークン——文脈の丸ごと
# ぶんを一度に食える形になっていた。50 枚の batch や長い書き起こしが現にそこへ
# 近づく。
#
# 8,000 文字は JSON で約 2,300 トークン。job_id と件ごとの成否が読める量である。
RESULT_INLINE_LIMIT = 8000
# 削るときに残す量。形が分かればよいので、頭だけ残す。
RESULT_LIST_HEAD = 5
RESULT_STRING_HEAD = 400
RESULT_DIR_ENV = "CONTROL_DECK_ADDON_MCP_RESULT_DIR"
# 削らない道具。
#
# 契約を取りに来る道具の返り値は、契約そのものである。これを削ると、契約を
# 一覧から外した意味が無くなる——読ませるために取りに来させているのに、
# 読める形で渡らない。呼ぶ側が要ると判断して取りに来たものなので、そのまま渡す。
RESULT_VERBATIM_TOOLS = frozenset({"control_deck.tool_contract"})
# 落とした全文を置いておく日数。読み返すのは同じ会話の中だけである。
RESULT_RETENTION_SECONDS = 3 * 24 * 60 * 60

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


def _condense(value: Any) -> Any:
    """形を保ったまま短くする。

    頭から切ると JSON が壊れて読めなくなる。件ごとの成否や job_id は先頭に
    あることが多いので、深さは保ったまま、並びと長い文字列だけを刈る。
    """
    if isinstance(value, dict):
        return {key: _condense(item) for key, item in value.items()}
    if isinstance(value, list):
        head = [_condense(item) for item in value[:RESULT_LIST_HEAD]]
        remaining = len(value) - RESULT_LIST_HEAD
        if remaining > 0:
            head.append(f"…残り {remaining} 件は全文を見ること")
        return head
    if isinstance(value, str) and len(value) > RESULT_STRING_HEAD:
        return f"{value[:RESULT_STRING_HEAD]}…（{len(value)} 文字。全文を見ること）"
    return value


def _spill(name: str, text: str) -> str | None:
    """削る前の全文を file へ落とす。落とせなければ None を返す。

    落とせないことを失敗にしない。会話に載る分は削った側で成立しており、
    file はあくまで「必要なら読める」ための控えである。
    """
    directory = os.environ.get(RESULT_DIR_ENV, "")
    if not directory:
        return None
    try:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for stale in root.glob("*.json"):
            try:
                if now - stale.stat().st_mtime > RESULT_RETENTION_SECONDS:
                    stale.unlink()
            except OSError:
                pass
        safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)[:64]
        path = root / f"{safe}-{int(now * 1000):x}.json"
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return str(path)


def _tool_result(name: str, value: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(value, ensure_ascii=False)
    if name in RESULT_VERBATIM_TOOLS or len(text) <= RESULT_INLINE_LIMIT:
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": value,
            "isError": False,
        }
    spilled = _spill(name, text)
    condensed = _condense(value)
    shortened = json.dumps(condensed, ensure_ascii=False)
    if len(shortened) > RESULT_INLINE_LIMIT:
        # 刈っても収まらない形（巨大な dict）。ここまで来たら頭で切る。
        shortened = shortened[:RESULT_INLINE_LIMIT]
        condensed = None
    note = (
        f"\n\n（結果が長いので削った。元は {len(text)} 文字。"
        + (f"全文は {spilled}。grep や jq で必要なところだけ読むこと。）"
           if spilled else "全文は残っていない。）")
    )
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": shortened + note}],
        "isError": False,
    }
    if condensed is not None:
        result["structuredContent"] = condensed
    return result


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
                result = _tool_result(params["name"], value)
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
