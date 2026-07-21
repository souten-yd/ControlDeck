"""Remote Desktop tunnel数だけを保持する非本文telemetry。"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_active_tunnels = 0


def connected() -> None:
    global _active_tunnels
    with _lock:
        _active_tunnels += 1


def disconnected() -> None:
    global _active_tunnels
    with _lock:
        _active_tunnels = max(0, _active_tunnels - 1)


def count() -> int:
    with _lock:
        return _active_tunnels
