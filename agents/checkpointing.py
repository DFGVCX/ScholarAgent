from __future__ import annotations

import asyncio
import os
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def _checkpoint_url() -> str:
    url = os.getenv(
        "SCHOLAR_DATABASE_URL",
        "postgresql+psycopg://scholar:scholar@localhost:5432/scholar_agent",
    )
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


class CheckpointProvider:
    """Own the PostgreSQL LangGraph checkpointer for the process lifetime."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._context: Any | None = None
        self._saver: Any | None = None

    async def get(self) -> Any:
        backend = os.getenv("SCHOLAR_CHECKPOINT_BACKEND", "postgres").strip().lower()
        if backend == "memory":
            return InMemorySaver()
        if backend not in {"postgres", "postgresql"}:
            raise ValueError("SCHOLAR_CHECKPOINT_BACKEND must be postgres or memory")
        async with self._lock:
            if self._saver is not None:
                return self._saver
            self._context = AsyncPostgresSaver.from_conn_string(_checkpoint_url())
            self._saver = await self._context.__aenter__()
            await self._saver.setup()
            return self._saver

    async def close(self) -> None:
        async with self._lock:
            if self._context is not None:
                await self._context.__aexit__(None, None, None)
            self._context = None
            self._saver = None


checkpoint_provider = CheckpointProvider()
