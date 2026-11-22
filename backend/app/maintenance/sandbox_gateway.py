"""開発用サンドボックスの LLM ゲートウェイ中継（箱の外、ホストで動く）。

箱の AI が本番のゲートウェイへ届くのは、この中継の 127.0.0.1:<gateway_port> だけ。
本番のゲートウェイは「同じ機械（loopback）＋ x-requested-with: ControlDeck」なら鍵なしで通すので、
箱から本番へ直に届くと、モデルの切り替え（POST .../models/{m}/load）まで呼べてしまう
（2026-11-21 実測）。そこで中継が、許した 3 つの呼び出しだけを通し、受けた頭（Authorization・
Cookie など）は上流へ渡さない。

MCP（ControlDeck の道具）も同じ口で中継する（P2a）。箱の持ち主として動く MCP のトークンは、箱の外のこの
プロセスだけが持ち（環境変数 CONTROL_DECK_SANDBOX_MCP_TOKEN。systemd の EnvironmentFile で渡す）、上流へ
`Authorization: Bearer` を付ける。箱から来た Authorization は捨てる。箱から呼べる道具は MCP_ALLOWED_TOOLS だけ。

起動: python -m app.maintenance.sandbox_gateway --name N --port P --upstream http://127.0.0.1:8765 [--allow-load]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from urllib.parse import quote

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger("control_deck.sandbox_gateway")

API = "/api/v1/llm/v1"
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_CONCURRENT = 4
FORWARD_REQUEST_HEADERS = {"content-type", "accept", "accept-encoding", "x-control-deck-confidential"}
_DROP_RESPONSE_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "set-cookie",
    # 上流が返す更新後の MCP トークン。箱へ渡さない（トークンは箱の外に置く）
    "x-control-deck-mcp-token",
}
MCP_API = "/api/v1/addons/agent-mcp"
MCP_TOKEN_ENV = "CONTROL_DECK_SANDBOX_MCP_TOKEN"
# 箱から呼べる ControlDeck の道具。これ以外（診断・ナレッジ・Add-on の道具など）は 403。
MCP_ALLOWED_TOOLS = frozenset({
    "control_deck.lab_projects", "control_deck.lab_run_start", "control_deck.lab_run_status",
    "control_deck.lab_run_logs", "control_deck.lab_run_stop",
    "control_deck.report", "control_deck.research",
})
_LOAD = re.compile(rf"^{re.escape(API)}/models/([^/\s?#%]+)/load$")


def _deny(detail: str, status: int = 403) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def _wants_cloud(body: bytes) -> bool:
    """chat の model が外部のモデル（cloud:）か。読めない本文は上流の判断に任せる。"""
    try:
        model = json.loads(body).get("model")
    except (ValueError, AttributeError):
        return False
    return isinstance(model, str) and model.strip().lower().startswith("cloud:")


def create_app(*, name: str, upstream: str, allow_load: bool = False, allow_cloud: bool = False,
               transport: httpx.AsyncBaseTransport | None = None,
               max_concurrent: int = MAX_CONCURRENT, mcp_token: str = "") -> Starlette:
    base = upstream.rstrip("/")
    state = {"active": 0}

    def allowed(method: str, path: str) -> str | None:
        """通してよい呼び出しなら上流のパスを返す。"""
        if method == "GET" and path == f"{API}/models":
            return path
        if method == "POST" and path == f"{API}/chat/completions":
            return path
        if mcp_token and method == "GET" and path == f"{MCP_API}/tools":
            return path
        if mcp_token and method == "POST" and path == f"{MCP_API}/call":
            return path
        match = _LOAD.fullmatch(path)
        if allow_load and method == "POST" and match and match.group(1) not in {".", ".."}:
            return f"{API}/models/{quote(match.group(1), safe='')}/load"
        return None

    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok", "sandbox": name, "mcp": bool(mcp_token)})

    async def relay(request: Request) -> Response:
        started = time.monotonic()
        method, path = request.method, request.url.path
        upstream_path = allowed(method, path)
        if upstream_path is None:
            logger.info("%s %s 403 %.0fms", method, path[:120], (time.monotonic() - started) * 1000)
            return _deny("このサンドボックスの中継は、モデル一覧・チャット・許可した ControlDeck の道具（・許可した場合のモデル読み込み）だけを通します")
        if state["active"] >= max_concurrent:
            logger.info("%s %s 429 %.0fms", method, path[:120], (time.monotonic() - started) * 1000)
            return _deny("同時に処理できる数の上限です。少し待ってからもう一度送ってください", 429)
        state["active"] += 1
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                state["active"] -= 1

        def done(status: int) -> None:
            logger.info("%s %s %s %.0fms", method, path[:120], status, (time.monotonic() - started) * 1000)

        try:
            declared = request.headers.get("content-length")
            try:
                too_big = declared is not None and int(declared) > MAX_BODY_BYTES
            except ValueError:
                release()
                done(400)
                return _deny("Content-Length が不正です", 400)
            body = bytearray()
            if not too_big:
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_BODY_BYTES:
                        too_big = True
                        break
            if too_big:
                release()
                done(413)
                return _deny("リクエストが 32 MiB を超えています", 413)
            if not allow_cloud and upstream_path.endswith("/chat/completions") and _wants_cloud(bytes(body)):
                # 箱の OpenCode はローカル LLM だけ（2026-11-21 利用者）。外部のモデルの費用と外部送信を箱から起こさない。
                release()
                done(403)
                return _deny("このサンドボックスでは外部のモデル（cloud:）は使えません。ローカルのモデルを選んでください")
            is_mcp = upstream_path.startswith(MCP_API)
            if is_mcp and upstream_path.endswith("/call"):
                try:
                    tool = json.loads(bytes(body)).get("name")
                except (ValueError, AttributeError):
                    tool = None
                if not isinstance(tool, str) or tool not in MCP_ALLOWED_TOOLS:
                    release()
                    done(403)
                    return _deny("この道具はサンドボックスからは呼べません")
            headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_REQUEST_HEADERS}
            headers.update({"x-requested-with": "ControlDeck", "x-control-deck-client": "opencode",
                            "x-control-deck-sandbox": name})
            if is_mcp:
                # 箱の持ち主のトークンは箱の外のここだけが持つ。箱から来た Authorization は上で捨てている
                headers["authorization"] = f"Bearer {mcp_token}"
                headers["accept-encoding"] = "identity"
            client = httpx.AsyncClient(timeout=httpx.Timeout(20, read=3600 if is_mcp else 900),
                                       follow_redirects=False, transport=transport)
            try:
                response = await client.send(
                    client.build_request(method, f"{base}{upstream_path}", headers=headers, content=bytes(body)),
                    stream=True,
                )
            except httpx.HTTPError as exc:
                await client.aclose()
                release()
                done(502)
                logger.warning("上流へ接続できません (%s)", type(exc).__name__)
                return _deny("本番のゲートウェイへ接続できません", 502)
        except BaseException:
            release()
            raise
        out_headers = {k: v for k, v in response.headers.items()
                       if k.lower() not in _DROP_RESPONSE_HEADERS and k.lower() != "content-length"}
        if upstream_path == f"{MCP_API}/tools":
            # 道具の一覧も許可した道具だけに絞る（見えない道具は呼ばせない）
            try:
                raw = await response.aread()
            finally:
                await response.aclose()
                await client.aclose()
                release()
            done(response.status_code)
            out_headers = {k: v for k, v in out_headers.items() if k.lower() not in {"content-encoding", "content-type"}}
            if response.status_code != 200:
                return Response(raw, status_code=response.status_code, headers=out_headers,
                                media_type=response.headers.get("content-type"))
            try:
                listed = json.loads(raw)
                tools = [t for t in listed.get("tools", []) if isinstance(t, dict) and t.get("name") in MCP_ALLOWED_TOOLS]
            except (ValueError, AttributeError):
                return _deny("本番の MCP の応答を読めません", 502)
            return JSONResponse({"tools": tools}, headers=out_headers)

        async def stream():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
                release()
                done(response.status_code)

        return StreamingResponse(stream(), status_code=response.status_code, headers=out_headers)

    return Starlette(routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Route("/{path:path}", relay, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]),
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbox_gateway")
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--upstream", default="")
    parser.add_argument("--allow-load", action="store_true")
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args(argv)
    mcp_token = os.environ.get(MCP_TOKEN_ENV, "")
    upstream = args.upstream
    if not upstream:
        from app.config import get_config

        upstream = f"http://127.0.0.1:{int(get_config().server.port)}"
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    uvicorn.run(create_app(name=args.name, upstream=upstream, allow_load=args.allow_load,
                           allow_cloud=args.allow_cloud, mcp_token=mcp_token),
                host="127.0.0.1", port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
