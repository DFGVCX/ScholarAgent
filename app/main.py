from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes.auth import router as auth_router
from app.routes.agents import router as agents_router
from app.routes.conversations import router as conversations_router
from app.routes.health import router as health_router
from app.routes.knowledge import router as knowledge_router
from app.routes.institutional_access import router as institutional_access_router
from app.routes.settings import router as settings_router
from app.routes.tasks import router as tasks_router
from app.routes.translations import router as translations_router
from app.services import mysql_store
from agents.checkpointing import checkpoint_provider
from app.services.task_queue import task_queue

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")


@app.on_event("startup")
async def initialize_runtime_database() -> None:
    await asyncio.to_thread(mysql_store.initialize_database)


@app.on_event("shutdown")
async def close_runtime_resources() -> None:
    await task_queue.close()
    await checkpoint_provider.close()


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allow_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def prevent_app_shell_cache(request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/app.html"} or request.url.path.endswith(".html"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

app.include_router(auth_router)
app.include_router(agents_router)
app.include_router(health_router)
app.include_router(settings_router)
app.include_router(conversations_router)
app.include_router(tasks_router)
app.include_router(knowledge_router)
app.include_router(translations_router)
app.include_router(institutional_access_router)

try:
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
except RuntimeError:
    pass
