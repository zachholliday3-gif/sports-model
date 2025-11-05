# app/core/db.py
import os
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text

_engine: AsyncEngine | None = None

def _ensure_asyncpg(url: str) -> str:
    """
    Normalize any postgres URL to asyncpg + sslmode=require.
    Works for:
      - postgres://...
      - postgresql://...
      - postgresql+psycopg2://...
    """
    if not url:
        return url

    # normalize scheme
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql://" + url[len("postgresql+psycopg2://"):]
    if not url.startswith("postgresql+asyncpg://"):
        url = "postgresql+asyncpg://" + url.split("postgresql://", 1)[-1]

    # add sslmode=require if missing
    parsed = urlparse(url)
    q = dict(parse_qsl(parsed.query))
    if "sslmode" not in q:
        q["sslmode"] = "require"
    new_query = urlencode(q)
    final_url = urlunparse(parsed._replace(query=new_query))

    # minimal debug (no secrets)
    try:
        host = parsed.hostname or "?"
        port = parsed.port or "?"
        print(f"[DB] Using asyncpg URL -> host={host} port={port} sslmode={q.get('sslmode')}")
    except Exception:
        pass

    return final_url

def get_database_url() -> str | None:
    raw = os.getenv("DATABASE_URL")
    if not raw:
        print("[DB] DATABASE_URL not set; DB layer disabled.")
        return None
    return _ensure_asyncpg(raw)

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
