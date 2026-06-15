"""
PostgreSQL connection pool and schema initialisation.

``get_pool()`` is a lazy singleton — the pool is created once on first access
and reused for the lifetime of the process.  ``init_db()`` is called from the
FastAPI lifespan handler on every startup; all ``CREATE TABLE`` statements use
``IF NOT EXISTS`` so they are safe to re-run against an existing database.
"""
from __future__ import annotations
import asyncpg
from isp.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return (and lazily create) the shared asyncpg connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    return _pool


async def init_db() -> None:
    """
    Create all required tables if they do not exist.

    Called once from the FastAPI lifespan handler before the app starts serving
    requests.  Adding new tables here is the only migration step required for
    this schema; no external migration tool is used.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS indexes (
                id              VARCHAR(12)  PRIMARY KEY,
                name            VARCHAR(80)  NOT NULL,
                description     TEXT         DEFAULT '',
                created_at      VARCHAR(30)  NOT NULL,
                doc_count       INTEGER      DEFAULT 0,
                last_ingested   VARCHAR(30),
                status          VARCHAR(20)  DEFAULT 'ready',
                connectors      JSONB        DEFAULT '[]',
                collection_name VARCHAR(200) DEFAULT '',
                is_external     BOOLEAN      DEFAULT FALSE,
                external_collection  VARCHAR(200) DEFAULT '',
                external_filter      VARCHAR(500) DEFAULT '',
                external_query_by    VARCHAR(100) DEFAULT 'display',
                external_title_field VARCHAR(100) DEFAULT 'display',
                external_url_field   TEXT         DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS ingest_status (
                index_id VARCHAR(12) PRIMARY KEY,
                state    VARCHAR(20) DEFAULT 'idle',
                total    INTEGER     DEFAULT 0,
                indexed  INTEGER     DEFAULT 0,
                error    TEXT        DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS deleted_splits (
                split_key TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS extensions (
                id          VARCHAR(12)  PRIMARY KEY,
                name        VARCHAR(120) NOT NULL,
                description TEXT         DEFAULT '',
                code        TEXT         NOT NULL DEFAULT '',
                created_at  VARCHAR(30)  NOT NULL,
                updated_at  VARCHAR(30)  NOT NULL
            );

            CREATE TABLE IF NOT EXISTS index_extensions (
                index_id     VARCHAR(12) NOT NULL REFERENCES indexes(id) ON DELETE CASCADE,
                extension_id VARCHAR(12) NOT NULL REFERENCES extensions(id) ON DELETE CASCADE,
                run_order    INTEGER     NOT NULL DEFAULT 0,
                enabled      BOOLEAN     NOT NULL DEFAULT TRUE,
                PRIMARY KEY (index_id, extension_id)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        # Idempotent column additions — safe to run on existing databases.
        await conn.execute("""
            ALTER TABLE indexes
            ADD COLUMN IF NOT EXISTS embed_model VARCHAR(50) DEFAULT '';
        """)
        await conn.execute("""
            ALTER TABLE indexes
            ADD COLUMN IF NOT EXISTS version_boost BOOLEAN DEFAULT FALSE;
        """)
