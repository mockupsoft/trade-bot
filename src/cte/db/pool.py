"""Async PostgreSQL connection pool management."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
import structlog

from cte.core.settings import DatabaseSettings

logger = structlog.get_logger(__name__)


class DatabasePool:
    """Manages asyncpg connection pool lifecycle."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._settings.dsn,
            min_size=self._settings.min_pool_size,
            max_size=self._settings.max_pool_size,
            statement_cache_size=self._settings.statement_cache_size,
        )
        await logger.ainfo("db_pool_connected", dsn=self._settings.dsn.split("@")[-1])

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            await logger.ainfo("db_pool_closed")

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[asyncpg.Connection, None]:
        if not self._pool:
            raise RuntimeError("Database pool not initialized. Call connect() first.")
        async with self._pool.acquire() as conn:
            yield conn

    async def execute(self, query: str, *args: object) -> str:
        async with self.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args: object) -> list[asyncpg.Record]:
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: object) -> asyncpg.Record | None:
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: object) -> object:
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)
