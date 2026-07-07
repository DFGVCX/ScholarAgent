from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import AuthError, authenticate_api_key
from app.services.auth_service import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequestDTO(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=120)
    tenant_id: str | None = Field(default=None, max_length=120)


@router.post("/login")
async def login(request: LoginRequestDTO) -> dict[str, Any]:
    try:
        return auth_service.login(request.username, request.password, request.tenant_id)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me")
async def me(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict[str, Any]:
    try:
        user = authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return auth_service.profile_for(user)
