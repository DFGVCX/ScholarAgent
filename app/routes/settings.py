from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from agents.factory import model_factory
from app.dependencies import AuthError, authenticate_api_key
from app.services import mysql_store
from app.services.auth_service import auth_service
from app.services.runtime_config import public_runtime_config, update_runtime_config

router = APIRouter(prefix="/settings", tags=["settings"])


class RuntimeConfigUpdateDTO(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class MysqlBootstrapDTO(BaseModel):
    admin_url: str = Field(..., min_length=1)
    mysql_url: str | None = Field(default=None, min_length=1)
    seed_rag: bool = True


class MysqlQueryDTO(BaseModel):
    sql: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(default=50, ge=1, le=200)


class ModelProbeDTO(BaseModel):
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
async def get_runtime_settings(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> dict[str, Any]:
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
    mysql_store.reset_availability_cache()
    return {
        "status": "saved",
        "profile": profile,
        "config": public_runtime_config(),
        "note": "Runtime settings were saved. Running requests read the updated values on demand.",
    }


@router.post("/mysql/bootstrap")
async def bootstrap_mysql(
    request: MysqlBootstrapDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    values: dict[str, Any] = {"SCHOLAR_STORAGE_BACKEND": "mysql"}
    if request.mysql_url:
        values["SCHOLAR_MYSQL_URL"] = request.mysql_url
    try:
        update_runtime_config(values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    import os

    from scripts.bootstrap_mysql import _seed_rag, _table_counts, ensure_database_and_user

    os.environ["SCHOLAR_MYSQL_ADMIN_URL"] = request.admin_url
    mysql_store.reset_availability_cache()
    try:
        bootstrap = ensure_database_and_user()
        schema = mysql_store.initialize_database(create_database=False)
        rag = await _seed_rag() if request.seed_rag else None
        tables = _table_counts()
    except Exception as exc:
        mysql_store.reset_availability_cache()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "status": "ok",
        "profile": profile,
        "bootstrap": bootstrap,
        "schema": schema,
        "tables": tables,
        "rag": rag,
        "config": public_runtime_config(),
    }


@router.post("/mysql/query")
async def query_mysql(
    request: MysqlQueryDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    profile = _require_tenant_admin(x_api_key)
    sql = request.sql.strip().rstrip(";")
    if ";" in sql:
        raise HTTPException(status_code=422, detail="Only one read-only SQL statement is allowed")
    if not re.match(r"^(select|show|describe|desc|explain)\b", sql, flags=re.IGNORECASE):
        raise HTTPException(status_code=422, detail="Only SELECT, SHOW, DESCRIBE, DESC, and EXPLAIN are allowed")
    if re.match(r"^select\b", sql, flags=re.IGNORECASE) and not re.search(r"\blimit\b", sql, flags=re.IGNORECASE):
        sql = f"{sql} LIMIT {request.limit}"
    try:
        rows = mysql_store.fetch_all(sql)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    columns = list(rows[0].keys()) if rows else []
    return {
        "status": "ok",
        "profile": profile,
        "sql": sql,
        "columns": columns,
        "rows": rows[: request.limit],
        "row_count": len(rows[: request.limit]),
        "database": mysql_store.configured_database_name(),
    }


@router.post("/model/probe")
async def probe_model(
    request: ModelProbeDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    try:
        user = authenticate_api_key(x_api_key)
        profile = auth_service.profile_for(user)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        response = await model_factory.generate_text(
            "config_probe",
            request.prompt,
            {"tenant_id": profile.get("tenant_id"), "user_id": profile.get("user_id")},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "status": "ok",
        "profile": profile,
        "provider": response.provider,
        "model": response.model,
        "content": response.content,
    }
