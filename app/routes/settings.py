from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from agents.factory import model_factory
from app.config import get_settings
from app.dependencies import AuthError, authenticate_api_key
from app.services.auth_service import auth_service
from app.services.model_configuration import resolve_model_candidate
from app.services.runtime_config import public_runtime_config, update_runtime_config

router = APIRouter(prefix="/settings", tags=["settings"])


class RuntimeConfigUpdateDTO(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class ModelProbeDTO(BaseModel):
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    anthropic_base_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    prompt: str = Field(default="用一句中文回答：ScholarAgent 模型接入已连通。", max_length=1000)


def _require_tenant_admin(api_key: str | None) -> dict[str, Any]:
    try:
        user = authenticate_api_key(api_key)
        profile = auth_service.profile_for(user)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if "tenant_admin" not in set(profile.get("roles") or []):
        raise HTTPException(status_code=403, detail="Tenant admin role is required")
    return profile


@router.get("/runtime")
async def get_runtime_settings(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    return {"profile": profile, "config": public_runtime_config()}


@router.put("/runtime")
async def update_runtime_settings(
    request: RuntimeConfigUpdateDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    try:
        update_runtime_config(request.values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "status": "saved",
        "profile": profile,
        "config": public_runtime_config(),
        "note": "Runtime settings were saved. Running requests read the updated values on demand.",
    }


@router.post("/model/probe")
async def probe_model(
    request: ModelProbeDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    try:
        candidate = resolve_model_candidate(request.model_dump(exclude={"prompt"}), get_settings())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        response = await model_factory.probe(candidate, request.prompt)
    except Exception as exc:
        detail = str(exc)
        for secret in (candidate.api_key, candidate.anthropic_api_key):
            if secret:
                detail = detail.replace(secret, "***")
        raise HTTPException(status_code=502, detail=detail[:1000]) from exc
    return {
        "status": "ok",
        "profile": profile,
        "provider": response.provider,
        "model": response.model,
        "content": response.content,
    }
