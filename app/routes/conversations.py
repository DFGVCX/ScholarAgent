from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import AuthError, authenticate_api_key
from app.services.conversation_service import conversation_repository
from app.services.memory_service import user_memory_service
from app.services.conversation_state_service import conversation_state_service
from agents.conversation_tool_loop import conversation_tool_loop
from agents.context import conversation_context_manager

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationCreateDTO(BaseModel):
    title: str = Field(default="", max_length=240)
    skill_id: str = Field(default="general_assistant", max_length=120)
    initial_message: str = Field(default="", max_length=8000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationMessageDTO(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)
    skill_id: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolConfirmationDTO(BaseModel):
    approved: bool = True


class ConversationEventDTO(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=80)
    summary: str = Field(..., min_length=1, max_length=500)
    status: str = Field(default="succeeded", max_length=40)
    payload: dict[str, Any] = Field(default_factory=dict)


class MemoryCreateDTO(BaseModel):
    memory_type: str = Field(default="instruction", min_length=1, max_length=40)
    content: str = Field(..., min_length=3, max_length=1000)
    importance: float = Field(default=0.8, ge=0.0, le=1.0)


def _current_user(x_api_key: str | None):
    try:
        return authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/skills")
async def list_conversation_skills(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    _current_user(x_api_key)
    return {"items": conversation_repository.skills()}


@router.get("")
async def list_conversations(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    return {"items": await conversation_repository.list_by_user(user)}


@router.get("/memories")
async def list_memories(
    limit: int = 50,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    return {"items": user_memory_service.list_memories(user, limit)}


@router.post("/memories")
async def create_memory(
    request: MemoryCreateDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    item = user_memory_service.remember(
        user,
        memory_type=request.memory_type,
        content=request.content,
        importance=request.importance,
        metadata={"source": "user_api"},
    )
    if item is None:
        raise HTTPException(status_code=422, detail="memory content is too short")
    return {"item": item.__dict__}


@router.delete("/memories/{memory_id}")
async def forget_memory(
    memory_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    if not user_memory_service.forget(user, memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"forgotten": True, "memory_id": memory_id}


@router.post("")
async def create_conversation(
    request: ConversationCreateDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    title = request.title or request.initial_message[:42] or "新的会话"
    conversation = await conversation_repository.create(
        user,
        title=title,
        skill_id=request.skill_id,
        metadata=request.metadata,
    )
    messages = []
    if request.initial_message.strip():
        user_message = await conversation_repository.add_message(
            user,
            conversation["conversation_id"],
            "user",
            request.initial_message.strip(),
            skill_id=request.skill_id,
            metadata=request.metadata,
        )
        response_text, response_metadata = await conversation_repository.dispatch_message(
            user,
            conversation["conversation_id"],
            request.initial_message.strip(),
            request.skill_id,
            request.metadata,
        )
        assistant_message = await conversation_repository.add_message(
            user,
            conversation["conversation_id"],
            "assistant",
            response_text,
            skill_id=request.skill_id,
            metadata=response_metadata,
        )
        messages = [user_message, assistant_message]
    return {"item": {**conversation, "messages": messages}}


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    conversation = await conversation_repository.get(user, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"item": conversation}


@router.get("/{conversation_id}/state")
async def get_conversation_state(
    conversation_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    conversation = await conversation_repository.get(user, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"item": conversation_state_service.get(user, conversation_id)}


@router.post("/{conversation_id}/messages")
async def append_message(
    conversation_id: str,
    request: ConversationMessageDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    conversation = await conversation_repository.get(user, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    skill_id = request.skill_id or conversation.get("skill_id") or "general_assistant"
    user_message = await conversation_repository.add_message(
        user,
        conversation_id,
        "user",
        request.content.strip(),
        skill_id=skill_id,
        metadata=request.metadata,
    )
    response_text, response_metadata = await conversation_repository.dispatch_message(
        user,
        conversation_id,
        request.content.strip(),
        skill_id,
        request.metadata,
    )
    assistant_message = await conversation_repository.add_message(
        user,
        conversation_id,
        "assistant",
        response_text,
        skill_id=skill_id,
        metadata=response_metadata,
    )
    return {"items": [user_message, assistant_message]}


@router.post("/{conversation_id}/tool-calls/{call_id}/confirm")
async def confirm_tool_call(
    conversation_id: str,
    call_id: str,
    request: ToolConfirmationDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    conversation = await conversation_repository.get(user, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    outcome = await conversation_tool_loop.confirm(
        user,
        conversation_id,
        call_id,
        approved=request.approved,
    )
    assistant_message = await conversation_repository.add_message(
        user,
        conversation_id,
        "assistant",
        outcome.content,
        skill_id=conversation.get("skill_id") or "general_assistant",
        metadata=outcome.metadata,
    )
    return {"item": assistant_message}


@router.post("/{conversation_id}/archive")
async def archive_conversation(
    conversation_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    archived = await conversation_repository.archive(user, conversation_id)
    if not archived:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"archived": True, "conversation_id": conversation_id}


@router.post("/{conversation_id}/events")
async def record_conversation_event(
    conversation_id: str,
    request: ConversationEventDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    conversation = await conversation_repository.get(user, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"item": conversation_context_manager.record_event(
        user, conversation_id, request.event_type, request.summary, request.payload, request.status
    )}
