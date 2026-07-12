"""シンプルなインメモリレート制限（固定ウィンドウ）。"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

_attempts: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def allow(key: str, max_attempts: int, window_seconds: float) -> bool:
    """key に対する試行を記録し、ウィンドウ内の回数が上限以内なら True。"""
    now = time.monotonic()
    with _lock:
        q = _attempts[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= max_attempts:
            return False
        q.append(now)
        return True


def reset(key: str) -> None:
    with _lock:
        _attempts.pop(key, None)
