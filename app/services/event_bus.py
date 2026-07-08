from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncIterator

from app.schemas import TaskEvent
from app.services import mysql_store


class EventBus:
    def __init__(self) -> None:
        self._history: dict[str, list[TaskEvent]] = defaultdict(list)
        self._queues: dict[str, list[asyncio.Queue[TaskEvent]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def _persist_event(self, event: TaskEvent) -> None:
        if mysql_store.is_available():
            mysql_store.execute(
                "INSERT INTO scholar_task_events "
                "(task_id, tenant_id, user_id, event, phase, message, percent, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event.task_id, event.tenant_id, event.user_id,
                 event.event, event.phase, event.message,
                 event.percent, mysql_store.encode_json(event.payload)),
            )

    def _load_persisted_events(self, task_id: str) -> list[TaskEvent]:
        if not mysql_store.is_available():
            return []
        rows = mysql_store.fetch_all(
            """
            SELECT task_id, tenant_id, user_id, event, phase, message, percent, payload_json
            FROM scholar_task_events
            WHERE task_id = %s
            ORDER BY event_id ASC
            """,
            (task_id,),
        )
        return [
            TaskEvent(
                event=row["event"],
                task_id=row["task_id"],
                phase=row["phase"],
                message=row.get("message") or "",
                percent=int(row.get("percent") or 0),
                payload=mysql_store.decode_json(row.get("payload_json"), {}),
                tenant_id=row.get("tenant_id") or "",
                user_id=row.get("user_id") or "",
            )
            for row in rows
        ]

    async def publish(self, event: TaskEvent) -> None:
        async with self._lock:
            self._history[event.task_id].append(event)
            queues = list(self._queues[event.task_id])
        try:
            self._persist_event(event)
        except Exception:
            pass
        for queue in queues:
            await queue.put(event)

    async def history(self, task_id: str) -> list[TaskEvent]:
        async with self._lock:
            if not self._history.get(task_id):
                self._history[task_id].extend(self._load_persisted_events(task_id))
            return list(self._history.get(task_id, []))

    async def subscribe(self, task_id: str, start_index: int = 0) -> AsyncIterator[TaskEvent]:
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue()
        async with self._lock:
            if not self._history.get(task_id):
                self._history[task_id].extend(self._load_persisted_events(task_id))
            history = list(self._history.get(task_id, []))
            self._queues[task_id].append(queue)

        try:
            for event in history[start_index:]:
                yield event
                if event.event in {"completed", "failed"}:
                    return

            while True:
                event = await queue.get()
                yield event
                if event.event in {"completed", "failed"}:
                    return
        finally:
            async with self._lock:
                if queue in self._queues.get(task_id, []):
                    self._queues[task_id].remove(queue)


event_bus = EventBus()
