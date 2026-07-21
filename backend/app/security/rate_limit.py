"""プロセス内の接続元別sliding-window rate limiter。

リバースプロキシheaderを無条件に信用せず、ASGI serverが確定した直接peerだけをkeyにする。
"""
from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    def __init__(self, *, max_keys: int = 20_000) -> None:
        self._events: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def check(
        self,
        scope: str,
        key: str,
        limit: int,
        *,
        window_seconds: int = 60,
        now: float | None = None,
    ) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - window_seconds
        bucket_key = (scope, key)
        with self._lock:
            bucket = self._events.setdefault(bucket_key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (current - bucket[0]) + 0.999))
                return False, retry_after
            bucket.append(current)
            if len(self._events) > self._max_keys:
                self._prune(cutoff)
            return True, 0

    def _prune(self, cutoff: float) -> None:
        stale = [key for key, values in self._events.items() if not values or values[-1] <= cutoff]
        for key in stale:
            self._events.pop(key, None)
        if len(self._events) > self._max_keys:
            # 攻撃的なkey増加でもmemoryを固定上限へ戻す。古い末尾時刻から捨てる。
            ordered = sorted(self._events, key=lambda key: self._events[key][-1])
            for key in ordered[: len(self._events) - self._max_keys]:
                self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


api_rate_limiter = SlidingWindowRateLimiter()
