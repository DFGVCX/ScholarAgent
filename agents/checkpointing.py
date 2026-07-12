from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class CheckpointProvider:
    """Own the durable LangGraph connection for the process lifetime."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._connection: aiosqlite.Connection | None = None
        self._saver: Any | None = None

    async def get(self) -> Any:
        backend = os.getenv("SCHOLAR_CHECKPOINT_BACKEND", "memory").strip().lower()
        if backend != "sqlite":
            return InMemorySaver()
        async with self._lock:
            if self._saver is not None:
                return self._saver
            default_path = Path(os.getenv("SCHOLAR_STORAGE_DIR", "storage/runtime")) / "langgraph.db"
            path = Path(os.getenv("SCHOLAR_CHECKPOINT_SQLITE_PATH", str(default_path)))
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = await aiosqlite.connect(str(path), check_same_thread=False)
            self._saver = AsyncSqliteSaver(self._connection)
            await self._saver.setup()
            return self._saver

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
            self._connection = None
            self._saver = None


checkpoint_provider = CheckpointProvider()
