"""この機械の中からの呼び出しかどうかを判定する。

ControlDeck は 0.0.0.0 で待ち受けるので、tailnet や LAN からも届く。一方で
MediaForge・SonicForge・LLM ゲートウェイ・OpenCode の MCP bridge はどれも同じ機械の
中から 127.0.0.1 へ繋ぐ。この二つを分けられれば、手元からの呼び出しだけ資格情報を
省ける。

前段に proxy を置いていないので、接続元は kernel が教える peer address をそのまま
使う。X-Forwarded-For の類は見ない。見てしまうと、外から header を足すだけで
「手元から」を名乗れてしまう。
"""

from __future__ import annotations

from ipaddress import ip_address

from fastapi import Request

# ブラウザが仕込まれた頁から 127.0.0.1 を叩く手を塞ぐ。cross-origin では
# 素の form/img/script からこの header を付けられず、fetch で付ければ preflight が要る。
REQUIRED_HEADER = "x-requested-with"
REQUIRED_VALUE = "ControlDeck"


def is_loopback(request: Request) -> bool:
    """同じ機械の中から、ControlDeck の client として呼ばれているか。"""
    client = request.client
    if client is None or not client.host:
        return False
    try:
        address = ip_address(client.host)
    except ValueError:
        return False
    if not address.is_loopback:
        return False
    return request.headers.get(REQUIRED_HEADER, "") == REQUIRED_VALUE
