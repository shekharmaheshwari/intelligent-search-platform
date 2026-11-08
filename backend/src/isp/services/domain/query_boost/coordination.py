# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Per-index mutual exclusion for query-boost training.

Every training run takes this lock, whether it's manual ("Train now", ``POST
/training/query-boost``) or scheduled. ``trainer.train`` takes it itself,
so no caller can bypass it. A second run for the same index, from any API
instance, doesn't wait. It gets ``acquired=False`` and reports "busy".

It uses a Postgres session-level advisory lock on a **dedicated direct
connection, not one from the shared pool**. Holding a pool connection for
the whole run, while the run borrows more pool connections for its reads
and writes, could exhaust the pool: with ``max_size`` concurrent runs every
connection would be held while each run waited for one more, and search
traffic would starve too. Now a run holds at most one pool connection at a
time. Each concurrent run costs one extra Postgres connection, which is
idle while it holds the lock.

If the process dies, the connection closes and Postgres releases the lock.
Unlike a Redis lock, there's no TTL to tune and nothing stale left behind.

This lock only orders training runs among themselves. Deleting a model
deliberately doesn't wait for it. It bumps the model generation instead
(``store.save_model``), so a run already in flight can't publish over the
deletion.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

_LOCK_NAMESPACE = "isp:query_boost:train:"


async def _connect() -> asyncpg.Connection:
    from isp.config import settings
    return await asyncpg.connect(settings.database_url)


@asynccontextmanager
async def training_lock(index_id: str) -> AsyncIterator[bool]:
    """``async with training_lock(idx) as acquired:``. ``acquired`` is False if another run holds it."""
    conn = await _connect()
    try:
        acquired = await conn.fetchval(
            "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", _LOCK_NAMESPACE + index_id
        )
        yield bool(acquired)
    finally:
        # Closing the session releases its advisory locks; no explicit unlock
        # is needed, and nothing can leak into a pooled connection.
        await conn.close()
