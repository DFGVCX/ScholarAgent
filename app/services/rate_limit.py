from __future__ import annotations

import time
from collections import defaultdict

from app.config import get_settings

try:
    import redis
except ImportError:  # pragma: no cover - optional dependency fallback
    redis = None


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


class RedisRateLimiter:
    def __init__(self, limit: int = 20, window_seconds: int = 3600) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._fallback = InMemoryRateLimiter(limit, window_seconds)
        self._client = None

    def _redis(self):
        if redis is None:
            return None
        if self._client is not None:
            return self._client
        try:
            client = redis.Redis.from_url(
                get_settings().redis_url,
                socket_connect_timeout=0.3,
                socket_timeout=0.3,
                decode_responses=True,
            )
            client.ping()
            self._client = client
            return client
        except Exception:
            self._client = None
            return None

    def allow(self, key: str) -> bool:
        client = self._redis()
        if client is None:
            return self._fallback.allow(key)
        redis_key = f"scholar:rate:{key}"
        try:
            current = client.incr(redis_key)
            if current == 1:
                client.expire(redis_key, self.window_seconds)
            return int(current) <= self.limit
        except Exception:
            self._client = None
            return self._fallback.allow(key)


rate_limiter = RedisRateLimiter()
