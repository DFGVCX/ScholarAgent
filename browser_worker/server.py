from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from browser_worker.manager import browser_session_manager


WORKER_TOKEN = os.getenv("SCHOLAR_BROWSER_WORKER_TOKEN", "").strip()


def _authorize(value: str | None) -> None:
    if WORKER_TOKEN and value != f"Bearer {WORKER_TOKEN}":
        raise HTTPException(status_code=401, detail="invalid browser worker token")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await browser_session_manager.start()
    try:
        yield
    finally:
        await browser_session_manager.stop()


app = FastAPI(title="ScholarAgent Browser Worker", version="0.1.0", lifespan=lifespan)


class SessionDTO(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=120)
    tenant_id: str = Field(..., min_length=2, max_length=120)
    user_id: str = Field(..., min_length=2, max_length=120)
    login_url: str = Field(..., min_length=8, max_length=2000)
    headless: bool = False


class SearchDTO(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=50)


class DownloadDTO(BaseModel):
    indexes: list[int] = Field(..., min_length=1, max_length=5)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "scholar-browser-worker", "sessions": len(browser_session_manager.sessions)}


@app.post("/sessions")
async def create_session(request: SessionDTO, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        return await browser_session_manager.create_session(**request.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sessions/{session_id}")
async def session_status(session_id: str, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        return await browser_session_manager.status(session_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/sessions/{session_id}/authenticated")
async def mark_authenticated(session_id: str, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        return await browser_session_manager.mark_authenticated(session_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sessions/{session_id}/cnki/search")
async def cnki_search(session_id: str, request: SearchDTO, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        return await browser_session_manager.search_cnki(session_id, request.query, request.limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sessions/{session_id}/cnki/download")
async def cnki_download(session_id: str, request: DownloadDTO, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        return await browser_session_manager.download_cnki(session_id, request.indexes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/sessions/{session_id}")
async def close_session(session_id: str, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    await browser_session_manager.close_session(session_id)
    return {"closed": True, "session_id": session_id}
