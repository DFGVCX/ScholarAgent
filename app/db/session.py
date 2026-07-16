from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def _database_url() -> str:
    url = get_settings().database_url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


async_engine: AsyncEngine = create_async_engine(
    _database_url(),
    pool_pre_ping=True,
    pool_recycle=1800,
)
async_session_factory = async_sessionmaker(async_engine, expire_on_commit=False)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def tenant_transaction(tenant_id: str, user_id: str) -> AsyncIterator[AsyncSession]:
    if not tenant_id or not user_id:
        raise ValueError("tenant_id and user_id are required")
    async with async_session_factory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :value, true)"),
            {"value": tenant_id},
        )
        await session.execute(
            text("SELECT set_config('app.user_id', :value, true)"),
            {"value": user_id},
        )
        yield session
