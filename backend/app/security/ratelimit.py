"""シンプルなインメモリレート制限（固定ウィンドウ）。"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

_attempts: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def check(key: str, max_attempts: int, window_seconds: float) -> bool:
    """ウィンドウ内の記録済み失敗回数が上限未満なら True（記録はしない）。"""
    now = time.monotonic()
    with _lock:
        q = _attempts[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        return len(q) < max_attempts


def record(key: str) -> None:
    """失敗を記録する。"""
    with _lock:
        _attempts[key].append(time.monotonic())


def allow(key: str, max_attempts: int, window_seconds: float) -> bool:
    """check + record を同時に行う（成功もカウントする用途向け）。"""
    if not check(key, max_attempts, window_seconds):
        return False
    record(key)
    return True


def reset(key: str) -> None:
    with _lock:
        _attempts.pop(key, None)
