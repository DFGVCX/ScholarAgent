from __future__ import annotations

from typing import Any

from app.schemas import TaskRecord, TaskStatus
from app.services import mysql_store


class PostgresTaskRepository:
    async def save(self, record: TaskRecord) -> None:
        mysql_store.execute(
            """INSERT INTO scholar_tasks
                (task_id, tenant_id, user_id, status, phase, percent, trace_id,
                 request_json, result_json, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (task_id) DO UPDATE SET status=EXCLUDED.status,
                phase=EXCLUDED.phase, percent=EXCLUDED.percent, trace_id=EXCLUDED.trace_id,
                request_json=EXCLUDED.request_json, result_json=EXCLUDED.result_json,
                error=EXCLUDED.error, updated_at=now()""",
            (
                record.task_id, record.tenant_id, record.user_id, record.status.value,
                record.phase, record.percent, record.trace_id,
                mysql_store.encode_json(record.request),
                mysql_store.encode_json(record.result) if record.result is not None else None,
                record.error,
            ),
        )

    async def get(self, tenant_id: str, task_id: str) -> TaskRecord | None:
        row = mysql_store.fetch_one(
            "SELECT * FROM scholar_tasks WHERE tenant_id=%s AND task_id=%s LIMIT 1",
            (tenant_id, task_id),
        )
        return self._from_row(row) if row else None

    async def update(self, tenant_id: str, task_id: str, **fields: Any) -> TaskRecord:
        allowed = {
            "status": "status", "phase": "phase", "percent": "percent",
            "trace_id": "trace_id", "error": "error", "result": "result_json",
        }
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            column = allowed.get(key)
            if column is None:
                continue
            assignments.append(f"{column}=%s")
            if key == "result":
                params.append(mysql_store.encode_json(value) if value is not None else None)
            else:
                params.append(value.value if isinstance(value, TaskStatus) else value)
        if assignments:
            params.extend([tenant_id, task_id])
            mysql_store.execute(
                f"UPDATE scholar_tasks SET {', '.join(assignments)}, updated_at=now() "
                "WHERE tenant_id=%s AND task_id=%s",
                tuple(params),
            )
        record = await self.get(tenant_id, task_id)
        if record is None:
            raise KeyError(f"task not found after update: {task_id}")
        return record

    async def list_by_user(self, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        rows = mysql_store.fetch_all(
            "SELECT * FROM scholar_tasks WHERE tenant_id=%s AND user_id=%s ORDER BY updated_at ASC",
            (tenant_id, user_id),
        )
        return [self._from_row(row).to_dict() for row in rows]

    async def delete(self, tenant_id: str, user_id: str, task_id: str) -> bool:
        return bool(
            mysql_store.execute(
                "DELETE FROM scholar_tasks WHERE tenant_id=%s AND user_id=%s AND task_id=%s",
                (tenant_id, user_id, task_id),
            )
        )

    @staticmethod
    def _from_row(raw: dict[str, Any]) -> TaskRecord:
        return TaskRecord(
            task_id=raw["task_id"], tenant_id=raw["tenant_id"], user_id=raw["user_id"],
            status=TaskStatus(raw["status"]), phase=raw["phase"],
            request=mysql_store.decode_json(raw.get("request_json"), {}),
            percent=int(raw.get("percent", 0)), trace_id=raw.get("trace_id"),
            error=raw.get("error"), result=mysql_store.decode_json(raw.get("result_json"), None),
        )


task_repository = PostgresTaskRepository()
