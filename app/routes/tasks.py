from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dependencies import AuthError, authenticate_api_key
from app.schemas import CitationStyle, InputType, SurveyTaskRequest
from app.services.event_bus import event_bus
from app.services.outline_approval import outline_approval_registry
from app.services.repository import task_repository
from app.services.task_service import RateLimitExceeded, task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


class SurveyTaskRequestDTO(BaseModel):
    topic: str = Field(..., min_length=1, max_length=300)
    input_type: InputType
    input_value: str = Field(default="", max_length=500)
    citation_style: CitationStyle = CitationStyle.IEEE
    max_papers: int = Field(default=12, ge=1, le=1500)
    require_outline_confirmation: bool = False


class OutlineApprovalDTO(BaseModel):
    comment: str = Field(default="", max_length=500)
    outline_markdown: str = Field(default="", max_length=20000)


def _current_user(x_api_key: str | None) -> Any:
    try:
        return authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/survey")
async def create_survey_task(
    request: SurveyTaskRequestDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        record = await task_service.create_survey_task(
            SurveyTaskRequest(
                topic=request.topic,
                input_type=request.input_type,
                input_value=request.input_value or request.topic,
                citation_style=request.citation_style,
                max_papers=request.max_papers,
                require_outline_confirmation=request.require_outline_confirmation,
            ),
            user,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {
        "task_id": record.task_id,
        "status": record.status.value,
        "stream_url": f"/tasks/{record.task_id}/stream",
        "trace_id": record.trace_id,
    }


@router.post("/survey/pdf")
async def create_pdf_survey_task(
    file: UploadFile,
    topic: str = "",
    citation_style: CitationStyle = CitationStyle.IEEE,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    settings = get_settings()
    safe_name = "".join(ch for ch in file.filename if ch.isalnum() or ch in {".", "-", "_"})[:120]
    task_seed = safe_name or "uploaded.pdf"
    upload_dir = settings.upload_dir / user.tenant_id / user.user_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / task_seed
    path.write_bytes(await file.read())
    record = await task_service.create_survey_task(
        SurveyTaskRequest(
            topic=topic or path.stem,
            input_type=InputType.PDF,
            input_value=str(path),
            citation_style=citation_style,
        ),
        user,
    )
    return {
        "task_id": record.task_id,
        "status": record.status.value,
        "stream_url": f"/tasks/{record.task_id}/stream",
    }


@router.get("/{task_id}/stream")
async def stream_task(
    task_id: str,
    api_key: str | None = None,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> StreamingResponse:
    user = _current_user(x_api_key or api_key)
    record = await task_repository.get(user.tenant_id, task_id)
    if record is None or record.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Task not found")

    async def generator():
        async for event in event_bus.subscribe(task_id):
            yield f"event: {event.event}\n"
            yield f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream")


@router.get("/{task_id}/result")
async def get_task_result(
    task_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    record = await task_repository.get(user.tenant_id, task_id)
    if record is None or record.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    return record.to_dict()


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    record = await task_repository.get(user.tenant_id, task_id)
    if record is None or record.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    if record.status.value not in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail="Only completed or failed tasks can be deleted")
    deleted = await task_repository.delete(user.tenant_id, user.user_id, task_id)
    return {"deleted": deleted, "task_id": task_id}


@router.post("/{task_id}/outline/approve")
async def approve_outline(
    task_id: str,
    request: OutlineApprovalDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    record = await task_repository.get(user.tenant_id, task_id)
    if record is None or record.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    if outline_approval_registry.pending_payload(task_id) is None:
        raise HTTPException(status_code=409, detail="Outline approval is not pending")
    approved = outline_approval_registry.approve(
        task_id,
        request.comment,
        request.outline_markdown,
    )
    if not approved:
        raise HTTPException(status_code=409, detail="Outline approval is not pending")
    return {"status": "approved", "task_id": task_id}


@router.get("/{task_id}/audit")
async def get_task_audit(
    task_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    record = await task_repository.get(user.tenant_id, task_id)
    if record is None or record.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    result = record.result or {}
    audit = result.get("citation_audit") or {
        "is_valid": False,
        "found_ids": [],
        "missing_ids": [],
        "message": "Citation audit is not available before task completion",
    }
    return {
        "task_id": record.task_id,
        "status": record.status.value,
        "topic": record.request.get("topic", ""),
        "citation_audit": audit,
        "references": result.get("references", []),
        "papers": result.get("papers", []),
        "reflection_logs": result.get("reflection_logs", []),
        "global_review": result.get("global_review", {}),
    }


@router.get("")
async def list_tasks(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict[str, Any]:
    user = _current_user(x_api_key)
    return {"items": await task_repository.list_by_user(user.tenant_id, user.user_id)}
