# app/core/db.py
import os
import asyncio
from typing import Any, Iterable
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text

_engine: AsyncEngine | None = None

def get_database_url() -> str | None:
    return os.getenv("DATABASE_URL")

async def init_engine() -> AsyncEngine | None:
    global _engine
    url = get_database_url()
    if not url:
        return None
    _engine = create_async_engine(url, pool_pre_ping=True)
    return _engine

async def close_engine():
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None

async def exec_sql(sql: str, params: dict[str, Any] | None = None):
    if not _engine:
        return None
    async with _engine.begin() as conn:
        return await conn.execute(text(sql), params or {})

async def exec_many(sql: str, rows: Iterable[dict[str, Any]]):
    if not _engine:
        return None
    async with _engine.begin() as conn:
        await conn.execute(text(sql), list(rows))
