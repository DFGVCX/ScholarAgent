from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import AuthError, authenticate_api_key
from app.services.conversation_service import conversation_repository

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
