from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import AuthError, authenticate_api_key
from app.routes.knowledge import _find_user_paper
from app.services.translation_service import translation_service


router = APIRouter(prefix="/translations", tags=["translations"])


class TranslationRequestDTO(BaseModel):
    paper_id: str = Field(..., min_length=1, max_length=260)
    text: str = Field(..., min_length=1, max_length=8000)
    source_language: str = Field(default="auto", max_length=32)
    target_language: str = Field(default="中文", min_length=1, max_length=32)
    context: str = Field(default="", max_length=3000)


@router.post("")
async def translate_text(
    request: TranslationRequestDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    try:
        user = authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await _find_user_paper(request.paper_id, user)
    try:
        return await translation_service.translate(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            paper_id=request.paper_id,
            text=request.text,
            source_language=request.source_language,
            target_language=request.target_language,
            context=request.context,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
