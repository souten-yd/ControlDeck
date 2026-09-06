"""応答を gzip で縮める。ただし逐次流すものには触らない。

携帯から使うと、圧縮の有無がそのまま待ち時間になる。Add-on の画面は sonic-forge で
index 267KB + app.js 184KB + css 32KB = 約 483KB あり、これがそのまま流れていた。
テキストなので gzip でおおむね 1/4 以下になる。

一方、逐次流す応答（LLM の token、SSE）を圧縮すると、まとめてからでないと出せず
「最初の 1 文字が出るまで待つ」ことになる。速くするために入れたものが体感を
悪くする方向へ効いてしまうので、そこは素通しする。

判断は要求だけで済ませる。応答の content-type を待って分岐すると、その間の
message を持ち回ることになり、壊したときの影響が読みにくい。
"""

from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# 逐次流す経路。LLM ゲートウェイは stream=true のとき token を 1 つずつ返す。
STREAMING_PATH_PREFIXES = ("/api/v1/llm/",)


class SelectiveGZipMiddleware:
    """gzip をかける。逐次性が要る要求だけ素通しする。"""

    def __init__(self, app: ASGIApp, *, minimum_size: int = 1024) -> None:
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._plain(scope):
            await self.app(scope, receive, send)
            return
        await self.compressed(scope, receive, send)

    @staticmethod
    def _plain(scope: Scope) -> bool:
        path = str(scope.get("path") or "")
        if path.startswith(STREAMING_PATH_PREFIXES):
            return True
        for key, value in scope.get("headers") or ():
            if key.lower() == b"accept" and b"text/event-stream" in value.lower():
                return True
        return False
