from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from redis import Redis

from app.config import get_settings


@dataclass
class OutlineDecision:
    approved: bool
    comment: str = ""
    outline_markdown: str = ""


class OutlineApprovalRegistry:
    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._decisions: dict[str, OutlineDecision] = {}
        self._outlines: dict[str, dict[str, Any]] = {}
        self._redis: Redis | None = None

    def _redis_client(self) -> Redis | None:
        settings = get_settings()
        if settings.task_execution_mode != "queue" or not settings.redis_url:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(settings.redis_url, decode_responses=True)
        return self._redis

    @staticmethod
    def _key(task_id: str) -> str:
        return f"scholar:outline-approval:{task_id}"

    def open(self, task_id: str, outline_payload: dict[str, Any]) -> None:
        client = self._redis_client()
        if client is not None:
            client.hset(self._key(task_id), mapping={
                "status": "pending",
                "payload": json.dumps(outline_payload, ensure_ascii=False),
            })
            client.expire(self._key(task_id), 3600)
            return
        self._events[task_id] = asyncio.Event()
        self._outlines[task_id] = outline_payload

    def approve(self, task_id: str, comment: str = "", outline_markdown: str = "") -> bool:
        client = self._redis_client()
        if client is not None:
            key = self._key(task_id)
            if not client.exists(key):
                return False
            client.hset(key, mapping={
                "status": "approved",
                "decision": json.dumps({
                    "approved": True,
                    "comment": comment,
                    "outline_markdown": outline_markdown,
                }, ensure_ascii=False),
            })
            client.expire(key, 3600)
            return True
        event = self._events.get(task_id)
        if event is None:
            return False
        self._decisions[task_id] = OutlineDecision(
            approved=True,
            comment=comment,
            outline_markdown=outline_markdown,
        )
        event.set()
        return True

    async def wait(self, task_id: str, timeout_seconds: float = 1800) -> OutlineDecision:
        client = self._redis_client()
        if client is not None:
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            key = self._key(task_id)
            while asyncio.get_running_loop().time() < deadline:
                values = await asyncio.to_thread(client.hgetall, key)
                if values.get("status") == "approved":
                    raw = json.loads(values.get("decision") or "{}")
                    await asyncio.to_thread(client.delete, key)
                    return OutlineDecision(
                        approved=bool(raw.get("approved")),
                        comment=str(raw.get("comment") or ""),
                        outline_markdown=str(raw.get("outline_markdown") or ""),
                    )
                if not values:
                    raise RuntimeError("Outline approval state expired or was removed")
                await asyncio.sleep(0.5)
            raise TimeoutError("Outline approval timed out")
        event = self._events.get(task_id)
        if event is None:
            raise RuntimeError("Outline approval is not pending")
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
            return self._decisions.get(task_id, OutlineDecision(approved=False, comment="No approval decision"))
        finally:
            self._events.pop(task_id, None)
            self._decisions.pop(task_id, None)
            self._outlines.pop(task_id, None)

    def pending_payload(self, task_id: str) -> dict[str, Any] | None:
        client = self._redis_client()
        if client is not None:
            values = client.hgetall(self._key(task_id))
            if values.get("status") not in {"pending", "approved"}:
                return None
            return json.loads(values.get("payload") or "{}")
        return self._outlines.get(task_id)


outline_approval_registry = OutlineApprovalRegistry()
