"""手元からの呼び出しだけ資格情報を省く。外から名乗れないことを確かめる。"""

from __future__ import annotations

import pytest
from fastapi import Request

from app.security.localhost import is_loopback


def _request(host: str | None, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "method": "GET", "path": "/", "headers": raw,
        "client": None if host is None else (host, 40000),
    }
    return Request(scope)


HEADER = {"X-Requested-With": "ControlDeck"}


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "127.0.0.5"])
def test_loopback_addresses_with_the_client_header_are_local(host):
    assert is_loopback(_request(host, HEADER)) is True


@pytest.mark.parametrize("host", ["100.120.2.19", "192.168.1.10", "10.0.0.2", "8.8.8.8"])
def test_addresses_reachable_from_outside_are_never_local(host):
    # 0.0.0.0 待受なので tailnet や LAN からも届く。ここを取り違えると全部開く。
    assert is_loopback(_request(host, HEADER)) is False


def test_the_client_header_is_required():
    # 仕込まれた頁が 127.0.0.1 を叩く手を塞ぐ。素の form や img では付けられない。
    assert is_loopback(_request("127.0.0.1")) is False
    assert is_loopback(_request("127.0.0.1", {"X-Requested-With": "XMLHttpRequest"})) is False


def test_forwarded_headers_cannot_claim_to_be_local():
    spoofed = {**HEADER, "X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"}
    assert is_loopback(_request("100.120.2.19", spoofed)) is False


def test_a_request_without_a_peer_address_is_not_local():
    assert is_loopback(_request(None, HEADER)) is False
    assert is_loopback(_request("not-an-address", HEADER)) is False


def test_llm_gateway_needs_no_key_from_this_machine_but_still_does_from_outside():
    from fastapi import HTTPException

    from app.models_mgmt import gateway

    gateway._authorize(_request("127.0.0.1", HEADER))  # 例外が出ないこと

    with pytest.raises(HTTPException) as outside:
        gateway._authorize(_request("100.120.2.19", HEADER))
    assert outside.value.status_code in (401, 503)


def test_expired_mcp_token_still_works_from_this_machine_only():
    import time

    from app.addons import agent_mcp, tokens

    token = tokens.issue(
        "control-deck", subject="opencode:tui-1", kind="agent-mcp",
        actor_user_id=1, ttl_seconds=1,
    )
    expired_at = int(time.time()) + 5

    def verify(*, allow_expired):
        return tokens.verify(
            token, addon_id="control-deck", kind="agent-mcp",
            max_ttl_seconds=agent_mcp.MCP_TOKEN_TTL_SECONDS,
            now=expired_at, allow_expired=allow_expired,
        )

    assert verify(allow_expired=True)["sub"] == "opencode:tui-1"
    with pytest.raises(tokens.AddonTokenError):
        verify(allow_expired=False)


def test_a_tampered_token_is_refused_even_from_this_machine():
    """期限を見ないだけで、署名を見ないわけではない。"""
    from app.addons import agent_mcp, tokens

    token = tokens.issue(
        "control-deck", subject="opencode:tui-1", kind="agent-mcp",
        actor_user_id=1, ttl_seconds=60,
    )
    body, _, signature = token.partition(".")
    with pytest.raises(tokens.AddonTokenError):
        tokens.verify(
            f"{body}x.{signature}", addon_id="control-deck", kind="agent-mcp",
            max_ttl_seconds=agent_mcp.MCP_TOKEN_TTL_SECONDS, allow_expired=True,
        )
