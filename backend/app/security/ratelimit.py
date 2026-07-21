"""シンプルなインメモリレート制限（固定ウィンドウ）。"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

_MAX_KEYS = 20_000
_attempts: dict[str, deque[float]] = {}
_lock = Lock()


def _bucket(key: str) -> deque[float]:
    bucket = _attempts.get(key)
    if bucket is None:
        bucket = deque()
        _attempts[key] = bucket
    return bucket


def _prune_keys(protected_key: str) -> None:
    if len(_attempts) <= _MAX_KEYS:
        return
    for key in [key for key, values in _attempts.items() if key != protected_key and not values]:
        _attempts.pop(key, None)
        if len(_attempts) <= _MAX_KEYS:
            return
    ordered = sorted(
        (key for key in _attempts if key != protected_key),
        key=lambda key: _attempts[key][-1],
    )
    for key in ordered[: len(_attempts) - _MAX_KEYS]:
        _attempts.pop(key, None)


def check(key: str, max_attempts: int, window_seconds: float) -> bool:
    """ウィンドウ内の記録済み失敗回数が上限未満なら True（記録はしない）。"""
    now = time.monotonic()
    with _lock:
        q = _bucket(key)
        while q and now - q[0] > window_seconds:
            q.popleft()
        allowed = len(q) < max_attempts
        _prune_keys(key)
        return allowed


def record(key: str) -> None:
    """失敗を記録する。"""
    with _lock:
        _bucket(key).append(time.monotonic())
        _prune_keys(key)


def allow(key: str, max_attempts: int, window_seconds: float) -> bool:
    """check + record を同時に行う（成功もカウントする用途向け）。"""
    if not check(key, max_attempts, window_seconds):
        return False
    record(key)
    return True


def reset(key: str) -> None:
    with _lock:
        _attempts.pop(key, None)
