"""httpx の SSL コンテキストを使い回す。

`httpx.AsyncClient()` は作るたびに CA 証明書を読み込んで SSL コンテキストを作る。
負荷のときは数秒かかり、event loop の上で作ると画面ごと止まる。コンテキストは
一度作れば使い回せるので、ここで 1 つだけ作る（起動時に thread で温めておく）。
"""
from __future__ import annotations

import ssl
from functools import lru_cache

import httpx


@lru_cache(maxsize=1)
def shared_ssl_context() -> ssl.SSLContext:
    return httpx.create_ssl_context()
