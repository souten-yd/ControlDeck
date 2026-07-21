from __future__ import annotations


def test_sliding_window_limit_retry_and_key_bound():
    from app.security.rate_limit import SlidingWindowRateLimiter

    limiter = SlidingWindowRateLimiter(max_keys=2)
    assert limiter.check("api", "one", 2, now=10) == (True, 0)
    assert limiter.check("api", "one", 2, now=11) == (True, 0)
    allowed, retry = limiter.check("api", "one", 2, now=12)
    assert allowed is False and retry == 58
    assert limiter.check("api", "one", 2, now=71) == (True, 0)
    limiter.check("api", "two", 2, now=71)
    limiter.check("api", "three", 2, now=71)
    assert len(limiter._events) <= 2


def test_http_api_and_download_limits_use_direct_peer(client, monkeypatch):
    from app import main

    calls: list[tuple[str, str, int]] = []

    def check(scope: str, key: str, limit: int, **_kwargs):
        calls.append((scope, key, limit))
        return (False, 17) if scope == "api" else (True, 0)

    monkeypatch.setattr(main.api_rate_limiter, "check", check)
    limited = client.get("/api/v1/apps", headers={"X-Forwarded-For": "203.0.113.99"})
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "17"
    assert calls[0][0] == "api"
    assert calls[0][1] != "203.0.113.99"
    assert client.get("/api/v1/health").status_code == 200

    client.get("/api/v1/files/download?path=/missing")
    assert calls[-1][0] == "download"


def test_websocket_handshake_rate_limit_closes_4429(admin_client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from app.security.rate_limit import api_rate_limiter

    monkeypatch.setattr(api_rate_limiter, "check", lambda *_args, **_kwargs: (False, 9))
    try:
        with admin_client.websocket_connect("/api/v1/system/metrics/stream"):
            raise AssertionError("rate limited websocket must not connect")
    except WebSocketDisconnect as error:
        assert error.code == 4429
