"""作業の道具（MCP サーバー）の接続診断。

「入れたのに AI が使わない」の原因は、たいてい手前にある——node が無い、Chrome が無い、
起動はするが道具の一覧を返さない、ブラウザが立ち上がらない。OpenCode を開く前に、
同じ起動コマンドで 1 つずつ確かめて、どこで止まったかを画面へ返す。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

STEP_TIMEOUT_SEC = 45.0


async def _call(process: asyncio.subprocess.Process, message: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
    await process.stdin.drain()
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=STEP_TIMEOUT_SEC)
        if not line:
            raise RuntimeError("MCP サーバーが応答の前に終了しました")
        try:
            reply = json.loads(line)
        except ValueError:
            continue  # ログ行
        if reply.get("id") == message.get("id"):
            if "error" in reply:
                raise RuntimeError(str(reply["error"].get("message") or reply["error"])[:300])
            return reply.get("result") or {}


async def diagnose_ui_verify() -> dict[str, Any]:
    """Playwright MCP を session と同じ指定で起こし、道具の一覧とブラウザの起動まで確かめる。"""
    from app.integrations.opencode import provider

    steps: list[dict[str, Any]] = []

    def step(name: str, ok: bool, detail: str = "", started: float | None = None) -> None:
        steps.append({"name": name, "ok": ok, "detail": detail,
                      "ms": round((time.monotonic() - started) * 1000) if started else None})

    from app.features import registry

    entry = registry.mcp_entry(provider.WORKFLOW_MCP_FEATURES["ui_verify"])
    step("導入", entry is not None, str(entry or "設定 → オプション機能で Playwright MCP を導入してください"))
    browser = provider._browser_executable()
    step("ブラウザ（Chrome）", bool(browser), browser or "google-chrome が見つかりません")
    settings = provider.get_settings()
    if not settings.get("ui_verify"):
        step("設定", False, "OpenCode の設定で「UI 検証」が切られています")
    server = provider.ui_verify_server("diagnose")
    if server is None:
        return {"ok": False, "steps": steps, "tools": 0}
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *server["command"], stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        step("起動", False, str(exc)[:300], started)
        return {"ok": False, "steps": steps, "tools": 0}
    tools: list[str] = []
    try:
        await _call(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "controldeck-diagnose", "version": "1"}}})
        assert process.stdin is not None
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        step("起動", True, "", started)
        started = time.monotonic()
        listed = await _call(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = [str(item.get("name")) for item in listed.get("tools") or []]
        step("道具の一覧", bool(tools), f"{len(tools)} 個", started)
        started = time.monotonic()
        await _call(process, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "browser_navigate", "arguments": {"url": "about:blank"}}})
        step("ブラウザを開く", True, "about:blank", started)
        await _call(process, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "browser_close", "arguments": {}}})
    except (RuntimeError, TimeoutError, AssertionError) as exc:
        step("応答", False, str(exc)[:300] or "時間切れ", started)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return {"ok": all(item["ok"] for item in steps), "steps": steps, "tools": len(tools)}
