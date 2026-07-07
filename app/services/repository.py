from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas import TaskRecord, TaskStatus
from app.services import mysql_store


class JsonTaskRepository:
    """Development fallback repository with explicit tenant filtering."""

    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self.path = path or settings.storage_dir / "tasks.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _read_all_sync(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write_all_sync(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def save(self, record: TaskRecord) -> None:
        if mysql_store.is_available():
            mysql_store.execute(
                """
                INSERT INTO scholar_tasks
                    (task_id, tenant_id, user_id, status, phase, percent, trace_id, request_json, result_json, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    status = VALUES(status),
                    phase = VALUES(phase),
                    percent = VALUES(percent),
                    trace_id = VALUES(trace_id),
                    request_json = VALUES(request_json),
                    result_json = VALUES(result_json),
                    error = VALUES(error)
                """,
                (
                    record.task_id,
                    record.tenant_id,
                    record.user_id,
                    record.status.value,
                    record.phase,
                    record.percent,
                    record.trace_id,
                    mysql_store.encode_json(record.request),
                    mysql_store.encode_json(record.result) if record.result is not None else None,
                    record.error,
                ),
            )
            return
        async with self._lock:
            data = self._read_all_sync()
            data[record.task_id] = record.to_dict()
            self._write_all_sync(data)

    async def get(self, tenant_id: str, task_id: str) -> TaskRecord | None:
        if mysql_store.is_available():
            row = mysql_store.fetch_one(
                """
                SELECT *
                FROM scholar_tasks
                WHERE tenant_id = %s AND task_id = %s
                LIMIT 1
                """,
                (tenant_id, task_id),
            )
            return self._from_mysql_row(row) if row else None
        async with self._lock:
            raw = self._read_all_sync().get(task_id)
        if not raw or raw.get("tenant_id") != tenant_id:
            return None
        return TaskRecord(
            task_id=raw["task_id"],
            tenant_id=raw["tenant_id"],
            user_id=raw["user_id"],
            status=TaskStatus(raw["status"]),
            phase=raw["phase"],
            request=raw["request"],
            percent=int(raw.get("percent", 0)),
            trace_id=raw.get("trace_id"),
            error=raw.get("error"),
            result=raw.get("result"),
        )

    async def update(self, tenant_id: str, task_id: str, **fields: Any) -> TaskRecord:
        if mysql_store.is_available():
            allowed = {
                "status": "status",
                "phase": "phase",
                "percent": "percent",
                "trace_id": "trace_id",
                "error": "error",
                "result": "result_json",
            }
            assignments: list[str] = []
            params: list[Any] = []
            for key, value in fields.items():
                column = allowed.get(key)
                if column is None:
                    continue
                assignments.append(f"{column} = %s")
                if key == "result":
                    params.append(mysql_store.encode_json(value) if value is not None else None)
                else:
                    params.append(value.value if isinstance(value, TaskStatus) else value)
            if assignments:
                params.extend([tenant_id, task_id])
                mysql_store.execute(
                    f"""
                    UPDATE scholar_tasks
                    SET {', '.join(assignments)}
                    WHERE tenant_id = %s AND task_id = %s
                    """,
                    tuple(params),
                )
            record = await self.get(tenant_id, task_id)
            if record is None:
                raise KeyError(f"task not found after update: {task_id}")
            return record
        async with self._lock:
            data = self._read_all_sync()
            raw = data.get(task_id)
            if not raw or raw.get("tenant_id") != tenant_id:
                raise KeyError(f"task not found: {task_id}")
            raw.update(fields)
            data[task_id] = raw
            self._write_all_sync(data)
        record = await self.get(tenant_id, task_id)
        if record is None:
            raise KeyError(f"task not found after update: {task_id}")
        return record

    async def list_by_user(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        if mysql_store.is_available():
            rows = mysql_store.fetch_all(
                """
                SELECT *
                FROM scholar_tasks
                WHERE tenant_id = %s AND user_id = %s
                ORDER BY updated_at ASC
                """,
                (tenant_id, user_id),
            )
            return [self._from_mysql_row(row).to_dict() for row in rows]
        async with self._lock:
            data = self._read_all_sync()
        return [
            item
            for item in data.values()
            if item.get("tenant_id") == tenant_id and item.get("user_id") == user_id
        ]

    async def delete(self, tenant_id: str, user_id: str, task_id: str) -> bool:
        if mysql_store.is_available():
            affected = mysql_store.execute(
                """
                DELETE FROM scholar_tasks
                WHERE tenant_id = %s AND user_id = %s AND task_id = %s
                """,
                (tenant_id, user_id, task_id),
            )
            return bool(affected)
        async with self._lock:
            data = self._read_all_sync()
            raw = data.get(task_id)
            if not raw or raw.get("tenant_id") != tenant_id or raw.get("user_id") != user_id:
                return False
            del data[task_id]
            self._write_all_sync(data)
        return True

    def _from_mysql_row(self, raw: dict[str, Any]) -> TaskRecord:
        return TaskRecord(
            task_id=raw["task_id"],
            tenant_id=raw["tenant_id"],
            user_id=raw["user_id"],
            status=TaskStatus(raw["status"]),
            phase=raw["phase"],
            request=mysql_store.decode_json(raw.get("request_json"), {}),
            percent=int(raw.get("percent", 0)),
            trace_id=raw.get("trace_id"),
            error=raw.get("error"),
            result=mysql_store.decode_json(raw.get("result_json"), None),
        )


task_repository = JsonTaskRepository()
