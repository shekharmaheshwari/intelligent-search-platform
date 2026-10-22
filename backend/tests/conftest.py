# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Shared pytest fixtures for ISP backend tests.

All fixtures that touch the database use a real asyncpg connection — never
mocked — because mock/prod divergence has burned us before.
"""
from __future__ import annotations
import os
import time
import uuid

import jwt
import pytest
import asyncpg
from httpx import AsyncClient

# Base URL of the already-running uvicorn server inside the container
_BASE_URL = "http://localhost:8001"


# ── Auth helpers ──────────────────────────────────────────────────────────────

@pytest.fixture
def auth_token() -> str:
    secret = os.environ.get("JWT_SECRET", "changeme")
    return jwt.encode(
        {"sub": "pytest-user", "exp": int(time.time()) + 3600},
        secret,
        algorithm="HS256",
    )


@pytest.fixture
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}


# ── HTTP client ───────────────────────────────────────────────────────────────

@pytest.fixture
async def client():
    async with AsyncClient(base_url=_BASE_URL) as c:
        yield c


# ── Database ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """Raw asyncpg connection for seeding and assertion queries."""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    yield conn
    await conn.close()


# ── Test index fixture ────────────────────────────────────────────────────────

@pytest.fixture
async def test_index(db) -> dict:
    """
    Insert a minimal index row and yield its metadata.
    Deleted after the test to leave the DB clean.
    """
    index_id = uuid.uuid4().hex[:12]
    now = str(int(time.time()))

    await db.execute(
        """
        INSERT INTO indexes (id, name, description, created_at, doc_count, status, connectors, collection_name, slug)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
        """,
        index_id,
        f"Pytest Index {index_id}",
        "Created by pytest",
        now,
        0,
        "ready",
        "[]",
        f"isp_{index_id}",
        index_id,
    )
    yield {"id": index_id, "name": f"Pytest Index {index_id}"}

    # cleanup
    await db.execute("DELETE FROM search_events WHERE index_id = $1", index_id)
    await db.execute("DELETE FROM indexes WHERE id = $1", index_id)


# ── Search event seeding helper ───────────────────────────────────────────────

async def seed_events(db, index_id: str, events: list[dict]) -> None:
    """
    Insert rows into search_events.

    Each dict may have: session_id, query, doc_id, rank, clicked, source, created_at.
    Defaults are applied for missing keys.
    """
    now_ms = int(time.time() * 1000)
    for ev in events:
        await db.execute(
            """
            INSERT INTO search_events
              (index_id, session_id, query, doc_id, rank, clicked, source, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            index_id,
            ev.get("session_id", "s1"),
            ev.get("query", "test"),
            ev.get("doc_id", "d1"),
            ev.get("rank", 0),
            ev.get("clicked", False),
            ev.get("source", "pytest"),
            ev.get("created_at", now_ms),
        )
