from __future__ import annotations

import time
from collections import defaultdict


class InMemoryRateLimiter:
    def __init__(self, limit: int = 20, window_seconds: int = 3600) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        bucket = [ts for ts in self._buckets[key] if ts >= window_start]
        if len(bucket) >= self.limit:
            self._buckets[key] = bucket
            return False
        bucket.append(now)
        self._buckets[key] = bucket
        return True


rate_limiter = InMemoryRateLimiter()
