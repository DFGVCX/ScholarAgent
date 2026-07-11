from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import AuthError, authenticate_api_key
from app.services.institutional_access.service import (
    InstitutionalAccessError,
    institutional_access_service,
)
from app.services.browser_worker_client import BrowserWorkerError, browser_worker_client


router = APIRouter(prefix="/institutional-access", tags=["institutional-access"])


def _current_user(x_api_key: str | None):
    try:
        return authenticate_api_key(x_api_key)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _raise_api_error(exc: InstitutionalAccessError) -> None:
    status = 404 if exc.code.endswith("NOT_FOUND") else 400
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc


class InstitutionProfileDTO(BaseModel):
    profile_id: str = Field(default="", max_length=80)
    institution_name: str = Field(..., min_length=2, max_length=255)
    access_type: str = Field(default="system_vpn", max_length=32)
    login_url: str = Field(..., min_length=8, max_length=1000)
    proxy_prefix: str = Field(default="", max_length=1000)


class VerifySessionDTO(BaseModel):
    probe_url: str = Field(..., min_length=8, max_length=2000)


class PrepareDownloadDTO(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=80)
    source_url: str = Field(..., min_length=8, max_length=3000)
    title: str = Field(default="机构文献", max_length=500)
    doi: str = Field(default="", max_length=255)
    source: str = Field(default="institution", max_length=40)
    conversation_id: str = Field(default="", max_length=80)


class ConfirmDownloadDTO(BaseModel):
    confirmation_token: str = Field(..., min_length=1, max_length=120)


class BrowserSearchDTO(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=50)


class BrowserDownloadDTO(BaseModel):
    indexes: list[int] = Field(..., min_length=1, max_length=5)


@router.get("/profiles")
async def list_profiles(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    return {"items": institutional_access_service.list_profiles(user)}


@router.post("/profiles")
async def save_profile(
    request: InstitutionProfileDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        item = institutional_access_service.save_profile(
            user,
            institution_name=request.institution_name,
            access_type=request.access_type,
            login_url=request.login_url,
            proxy_prefix=request.proxy_prefix,
            profile_id=request.profile_id,
        )
        return {"item": item}
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)


@router.post("/sessions/{profile_id}")
async def start_session(
    profile_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        item = institutional_access_service.start_session(user, profile_id)
        browser = await browser_worker_client.start_session(
            session_id=item["session_id"],
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            login_url=item["login_url"],
            headless=False,
        )
        return {"item": {**item, "browser_managed": True, "browser": browser}}
    except BrowserWorkerError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "BROWSER_WORKER_UNAVAILABLE", "message": str(exc)},
        ) from exc
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)


@router.get("/sessions/status")
async def session_status(
    session_id: str = "",
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    return {"item": institutional_access_service.status(user, session_id)}


@router.post("/sessions/{session_id}/verify")
async def verify_session(
    session_id: str,
    request: VerifySessionDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        return {"item": await institutional_access_service.verify(user, session_id, request.probe_url)}
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)


@router.post("/sessions/{session_id}/revoke")
async def revoke_session(
    session_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        try:
            await browser_worker_client.close(session_id)
        except BrowserWorkerError:
            pass
        return {"item": institutional_access_service.revoke(user, session_id)}
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)


@router.get("/sessions/{session_id}/browser")
async def browser_status(
    session_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    session = institutional_access_service.status(user, session_id)
    if session.get("status") == "disconnected":
        raise HTTPException(status_code=404, detail="institution session not found")
    try:
        return {"item": await browser_worker_client.status(session_id)}
    except BrowserWorkerError as exc:
        institutional_access_service.mark_browser_unavailable(user, session_id, str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/browser-authenticated")
async def browser_authenticated(
    session_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        browser = await browser_worker_client.mark_authenticated(session_id)
        session = institutional_access_service.activate_browser_session(
            user, session_id, str(browser.get("current_url") or "")
        )
        return {"item": session, "browser": browser}
    except BrowserWorkerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)


@router.post("/sessions/{session_id}/cnki/search")
async def browser_cnki_search(
    session_id: str,
    request: BrowserSearchDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    session = institutional_access_service.status(user, session_id)
    if session.get("status") != "active":
        raise HTTPException(status_code=400, detail="机构登录已失效，请重新连接并在浏览器完成登录")
    try:
        await browser_worker_client.status(session_id)
        return await browser_worker_client.search_cnki(session_id, request.query, request.limit)
    except BrowserWorkerError as exc:
        institutional_access_service.mark_browser_unavailable(user, session_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/cnki/download")
async def browser_cnki_download(
    session_id: str,
    request: BrowserDownloadDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    session = institutional_access_service.status(user, session_id)
    if session.get("status") != "active":
        raise HTTPException(status_code=400, detail="机构浏览器会话已失效，请重新连接并完成登录")
    try:
        await browser_worker_client.status(session_id)
        result = await browser_worker_client.download_cnki(session_id, request.indexes)
        papers = [
            await institutional_access_service.ingest_browser_download(user, session_id, item)
            for item in result.get("items", [])
        ]
        return {"items": papers, "browser": result}
    except BrowserWorkerError as exc:
        institutional_access_service.mark_browser_unavailable(user, session_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)


@router.post("/downloads/prepare")
async def prepare_download(
    request: PrepareDownloadDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        item = institutional_access_service.prepare_download(
            user,
            session_id=request.session_id,
            source_url=request.source_url,
            title=request.title,
            doi=request.doi,
            source=request.source,
            conversation_id=request.conversation_id,
        )
        return {"item": item, "requires_confirmation": True}
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)


@router.post("/downloads/{download_id}/confirm")
async def confirm_download(
    download_id: str,
    request: ConfirmDownloadDTO,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        return {"item": await institutional_access_service.confirm_download(
            user, download_id, confirmation_token=request.confirmation_token
        )}
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)


@router.get("/downloads/{download_id}")
async def get_download(
    download_id: str,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    user = _current_user(x_api_key)
    try:
        return {"item": institutional_access_service.get_download(user, download_id)}
    except InstitutionalAccessError as exc:
        _raise_api_error(exc)
