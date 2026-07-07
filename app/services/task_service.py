from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from typing import Any

from app.schemas import SurveyTaskRequest, TaskEvent, TaskRecord, TaskStatus, UserContext
from app.services.event_bus import event_bus
from app.services import mysql_store
from app.services.rate_limit import rate_limiter
from app.services.repository import task_repository
from app.services.tracing import trace_recorder


class RateLimitExceeded(Exception):
    """Raised when a tenant/user exceeds task creation quota."""


class TaskService:
    async def create_survey_task(
        self,
        request: SurveyTaskRequest,
        user: UserContext,
        run_background: bool = True,
    ) -> TaskRecord:
        rate_key = f"task:{user.tenant_id}:{user.user_id}"
        if not rate_limiter.allow(rate_key):
            raise RateLimitExceeded("Too many survey tasks in the current window")

        task_id = str(uuid.uuid4())
        trace_id = f"trace-{task_id}"
        record = TaskRecord(
            task_id=task_id,
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            status=TaskStatus.QUEUED,
            phase="queued",
            request={
                "topic": request.topic,
                "input_type": request.input_type.value,
                "input_value": request.input_value,
                "citation_style": request.citation_style.value,
                "max_papers": request.max_papers,
                "require_outline_confirmation": request.require_outline_confirmation,
            },
            percent=0,
            trace_id=trace_id,
        )
        await task_repository.save(record)
        await event_bus.publish(
            TaskEvent(
                event="queued",
                task_id=task_id,
                phase="queued",
                message="Task accepted and queued",
                percent=0,
                payload={"trace_id": trace_id},
                tenant_id=user.tenant_id,
                user_id=user.user_id,
            )
        )
        if run_background:
            asyncio.create_task(self.run_survey_task(record))
        return record

    async def run_survey_task(self, record: TaskRecord) -> dict[str, Any]:
        from agents.graph import run_global_workflow

        await task_repository.update(
            record.tenant_id,
            record.task_id,
            status=TaskStatus.RUNNING.value,
            phase="running",
            percent=1,
        )
        initial_state = {
            "task_id": record.task_id,
            "tenant_id": record.tenant_id,
            "user_id": record.user_id,
            "trace_id": record.trace_id,
            **record.request,
        }

        final_result: dict[str, Any] = {}
        try:
            async for event in run_global_workflow(initial_state):
                task_event = TaskEvent(
                    event=event.get("event", "progress"),
                    task_id=record.task_id,
                    phase=event.get("phase", "running"),
                    message=event.get("message", ""),
                    percent=int(event.get("percent", 0)),
                    payload=event.get("payload", {}),
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                )
                await event_bus.publish(task_event)
                trace_recorder.record(
                    record.trace_id or f"trace-{record.task_id}",
                    task_event.phase,
                    task_event.event,
                    task_id=record.task_id,
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                    metadata={"message": task_event.message, "percent": task_event.percent},
                )
                await task_repository.update(
                    record.tenant_id,
                    record.task_id,
                    status=(
                        TaskStatus.WAITING_USER.value
                        if task_event.event == "outline_required" and record.request.get("require_outline_confirmation")
                        else TaskStatus.RUNNING.value
                    ),
                    phase=task_event.phase,
                    percent=task_event.percent,
                )
                if task_event.event == "completed":
                    final_result = task_event.payload

            await task_repository.update(
                record.tenant_id,
                record.task_id,
                status=TaskStatus.COMPLETED.value,
                phase="completed",
                percent=100,
                result=final_result,
            )
            await self._persist_result_artifacts(record, final_result)
            return final_result
        except Exception as exc:
            await task_repository.update(
                record.tenant_id,
                record.task_id,
                status=TaskStatus.FAILED.value,
                phase="failed",
                error=str(exc),
            )
            await event_bus.publish(
                TaskEvent(
                    event="failed",
                    task_id=record.task_id,
                    phase="failed",
                    message="Task failed safely",
                    percent=100,
                    payload={"error": str(exc)},
                    tenant_id=record.tenant_id,
                    user_id=record.user_id,
                )
            )
            trace_recorder.record(
                record.trace_id or f"trace-{record.task_id}",
                "failed",
                "workflow_error",
                task_id=record.task_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                metadata={"error": str(exc)},
            )
            raise

    async def _persist_result_artifacts(self, record: TaskRecord, result: dict[str, Any]) -> None:
        from mcp_server.scholar_mcp.models import PaperRecord
        from mcp_server.scholar_mcp.store import knowledge_store

        paper_fields = set(PaperRecord.__dataclass_fields__)
        for item in result.get("papers") or []:
            if not isinstance(item, dict) or not item.get("paper_id") or not item.get("title"):
                continue
            paper = {key: item.get(key) for key in paper_fields if key in item}
            paper["tenant_id"] = record.tenant_id
            paper["user_id"] = record.user_id
            paper.setdefault("source", "survey_pool")
            paper.setdefault("authors", [])
            paper.setdefault("abstract", "")
            paper.setdefault("full_text", "")
            paper.setdefault("metadata", {})
            paper["metadata"] = {
                **(paper.get("metadata") or {}),
                "auto_saved_from_task": record.task_id,
                "knowledge_origin": "survey_generation_pool",
            }
            await knowledge_store.save_paper(PaperRecord(**paper))

        if not mysql_store.is_available():
            return
        audit = result.get("citation_audit") or {}
        if audit:
            mysql_store.execute(
                """
                INSERT INTO scholar_citation_audits
                    (task_id, tenant_id, user_id, is_valid, found_ids_json,
                     hallucinated_ids_json, missing_ids_json, coverage, payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.task_id,
                    record.tenant_id,
                    record.user_id,
                    bool(audit.get("is_valid")),
                    mysql_store.encode_json(audit.get("found_ids", [])),
                    mysql_store.encode_json(audit.get("hallucinated_ids", [])),
                    mysql_store.encode_json(audit.get("missing_reference_ids") or audit.get("missing_ids", [])),
                    float(audit.get("coverage") or 0),
                    mysql_store.encode_json(audit),
                ),
            )
        for item in result.get("reflection_logs") or []:
            mysql_store.execute(
                """
                INSERT INTO scholar_reflection_logs
                    (task_id, tenant_id, user_id, phase, section_id, review_json)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    record.task_id,
                    record.tenant_id,
                    record.user_id,
                    str(item.get("phase") or item.get("section_id") or "review"),
                    item.get("section_id"),
                    mysql_store.encode_json(item),
                ),
            )


task_service = TaskService()
