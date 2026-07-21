from app.security import ratelimit


def test_failed_attempt_limiter_bounds_key_count(monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_KEYS", 2)
    with ratelimit._lock:
        ratelimit._attempts.clear()
    try:
        ratelimit.record("oldest")
        ratelimit.record("middle")
        ratelimit.record("newest")
        assert len(ratelimit._attempts) <= 2
        assert "oldest" not in ratelimit._attempts

        # checkだけで生成される空bucketも無制限に残さない。
        assert ratelimit.check("empty", max_attempts=1, window_seconds=60)
        assert len(ratelimit._attempts) <= 2
    finally:
        with ratelimit._lock:
            ratelimit._attempts.clear()
