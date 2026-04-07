"""
Async MySQL connection pool.

Uses aiomysql so the FastAPI event loop isn't blocked on DB queries.
Connection pooling matters here because the extract pipeline can fan out
several concurrent DB writes per job (update job status, insert extraction,
update pcr_reports) and we don't want to reconnect for each one.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import aiomysql

from .config import settings

logger = logging.getLogger(__name__)

_pool: Optional[aiomysql.Pool] = None


async def init_pool() -> None:
    """Initialize the global connection pool. Called on app startup."""
    global _pool
    if _pool is not None:
        return
    _pool = await aiomysql.create_pool(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        db=settings.mysql_database,
        minsize=1,
        maxsize=settings.mysql_pool_size,
        autocommit=True,
        charset="utf8mb4",
    )
    logger.info(
        "MySQL pool initialized: %s:%s/%s pool_size=%s",
        settings.mysql_host,
        settings.mysql_port,
        settings.mysql_database,
        settings.mysql_pool_size,
    )


async def close_pool() -> None:
    """Close the pool on shutdown."""
    global _pool
    if _pool is None:
        return
    _pool.close()
    await _pool.wait_closed()
    _pool = None
    logger.info("MySQL pool closed")


@asynccontextmanager
async def get_cursor() -> AsyncIterator[aiomysql.DictCursor]:
    """Yield a DictCursor from the pool. Commits via autocommit."""
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call init_pool() on startup")
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            yield cur


async def fetch_one(sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
    async with get_cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def fetch_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    async with get_cursor() as cur:
        await cur.execute(sql, params)
        rows = await cur.fetchall()
        return list(rows)


async def execute(sql: str, params: tuple = ()) -> int:
    """Execute a write. Returns lastrowid for INSERTs."""
    async with get_cursor() as cur:
        await cur.execute(sql, params)
        return cur.lastrowid
