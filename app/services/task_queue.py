from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings


class TaskQueue:
    def __init__(self) -> None:
        self._client: Redis | None = None

    def enabled(self) -> bool:
        settings = get_settings()
        return settings.task_execution_mode == "queue" and bool(settings.redis_url)

    def _redis(self) -> Redis:
        settings = get_settings()
        if not settings.redis_url:
            raise RuntimeError("SCHOLAR_REDIS_URL is required for queued execution")
        if self._client is None:
            self._client = Redis.from_url(settings.redis_url, decode_responses=True)
        return self._client

    async def health(self) -> bool:
        if not self.enabled():
            return False
        try:
            return bool(await self._redis().ping())
        except Exception:
            return False

    async def enqueue(
        self, tenant_id: str, user_id: str, task_id: str, *, attempt: int = 0
    ) -> None:
        settings = get_settings()
        payload = json.dumps({
            "tenant_id": tenant_id,
            "user_id": user_id,
            "task_id": task_id,
            "attempt": attempt,
        }, ensure_ascii=True)
        await self._redis().lpush(settings.task_queue_name, payload)

    async def reserve(self, timeout: int = 5) -> tuple[str, dict[str, Any]] | None:
        settings = get_settings()
        processing = f"{settings.task_queue_name}:processing"
        raw = await self._redis().brpoplpush(settings.task_queue_name, processing, timeout)
        if raw is None:
            return None
        return raw, json.loads(raw)

    async def acknowledge(self, raw: str) -> None:
        processing = f"{get_settings().task_queue_name}:processing"
        await self._redis().lrem(processing, 1, raw)

    async def recover_processing(self) -> int:
        """Return unacknowledged jobs after a single-worker restart."""
        settings = get_settings()
        processing = f"{settings.task_queue_name}:processing"
        recovered = 0
        while True:
            raw = await self._redis().rpoplpush(processing, settings.task_queue_name)
            if raw is None:
                return recovered
            recovered += 1

    async def retry(self, raw: str, payload: dict[str, Any]) -> None:
        await self.acknowledge(raw)
        await self.enqueue(
            str(payload["tenant_id"]),
            str(payload["user_id"]),
            str(payload["task_id"]),
            attempt=int(payload.get("attempt") or 0) + 1,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


task_queue = TaskQueue()
