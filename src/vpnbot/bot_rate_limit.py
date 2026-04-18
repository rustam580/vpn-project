from __future__ import annotations

import time
from collections import deque

class InMemoryRateLimiter:
    def __init__(self, limit: int, window_sec: int):
        self.limit = max(1, limit)
        self.window_sec = max(1, window_sec)
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        dq = self._events.get(key)
        if dq is None:
            dq = deque()
            self._events[key] = dq
        cutoff = now - self.window_sec
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(now)
        return True

