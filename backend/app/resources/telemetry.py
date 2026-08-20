from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Any, Callable


class ResourceTelemetry:
    def __init__(self, *, max_events: int = 500, clock: Callable[[], float] = time.time):
        self._lock = threading.RLock()
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._clock = clock
        self._counters: Counter[str] = Counter()

    def record(
        self,
        event: str,
        *,
        request_id: str = "",
        lease_id: str = "",
        device_id: str = "",
        reason: str = "",
    ) -> None:
        # Resource requirements, user input, token values and arbitrary provider payloads
        # are intentionally excluded from telemetry.
        value = {
            "at": self._clock(),
            "event": event[:64],
            "request_id": request_id[:64],
            "lease_id": lease_id[:64],
            "device_id": device_id[:64],
            "reason": reason[:64],
        }
        with self._lock:
            self._events.appendleft(value)
            self._counters[event] += 1
            if reason:
                self._counters[f"reason:{reason}"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(sorted(self._counters.items())),
                "recent_events": list(self._events),
            }

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._counters.clear()

