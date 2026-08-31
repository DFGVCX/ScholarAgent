from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import sys
import warnings

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def _configure_psycopg_event_loop_policy() -> None:
    """Use a Windows loop implementation supported by psycopg async connections.

    Python 3.14 still defaults to Proactor on Windows, while psycopg requires a
    selector-based loop. This module is imported before Uvicorn/unittest creates
    application loops. The policy API is deprecated for Python 3.16, so the
    compatibility shim is isolated here for later replacement with loop_factory.
    """
    if sys.platform != "win32":
        return
    selector_policy_type = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy_type is None:
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        current_policy = asyncio.get_event_loop_policy()
        if not isinstance(current_policy, selector_policy_type):
            asyncio.set_event_loop_policy(selector_policy_type())


_configure_psycopg_event_loop_policy()


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
async def worker_transaction() -> AsyncIterator[AsyncSession]:
    """Run narrowly scoped worker operations such as SECURITY DEFINER job claiming."""
    async with async_session_factory() as session, session.begin():
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
