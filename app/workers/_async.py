"""Helpers for running async code inside Celery prefork workers."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

T = TypeVar("T")


@asynccontextmanager
async def worker_session_scope():
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url, poolclass=NullPool, future=True
    )
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    finally:
        await engine.dispose()


def run_async(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Run ``coro_factory()`` in a fresh event loop."""
    return asyncio.run(coro_factory())
