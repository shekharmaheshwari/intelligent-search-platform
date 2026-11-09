# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Postgres persistence for per-index query-boost schedules
(``query_boost_schedules``; see ``schedule`` for the semantics).

``claim()`` is the only coordination between API instances. It is one
conditional UPDATE, so when several instances find the same due run, exactly
one gets a row back and trains; the others skip it. No Redis lock is needed,
and a crashed instance can't leave a stale lock behind.
"""
from __future__ import annotations

import time

from isp.services.domain.query_boost.schedule import STATUS_RUNNING, Schedule
from isp.services.infra.database import get_pool

_COLUMNS = (
    "index_id, enabled, days, run_hour_utc, enabled_at, "
    "last_run_at, last_finished_at, last_status, last_message"
)


def _row_to_schedule(row) -> Schedule:
    return Schedule(
        index_id=row["index_id"],
        enabled=row["enabled"],
        days=row["days"],
        run_hour_utc=row["run_hour_utc"],
        enabled_at=row["enabled_at"],
        last_run_at=row["last_run_at"],
        last_finished_at=row["last_finished_at"],
        last_status=row["last_status"] or "",
        last_message=row["last_message"] or "",
    )


async def get_schedule(index_id: str) -> Schedule:
    """The stored schedule, or a disabled default when none was saved."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_COLUMNS} FROM query_boost_schedules WHERE index_id = $1", index_id
        )
    return _row_to_schedule(row) if row else Schedule(index_id=index_id)


async def save_schedule(index_id: str, *, enabled: bool, days: int, run_hour_utc: int) -> Schedule:
    """
    Create or update a schedule. Callers validate ``days`` and ``run_hour_utc``.

    ``enabled_at`` is set to now when the schedule is turned on, or when its
    hour changes while on, so the change takes effect from the next slot. It
    is kept when only ``days`` changes, and cleared when turned off. Run
    history (``last_*``) is always kept.
    """
    now = int(time.time())
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO query_boost_schedules
                (index_id, enabled, days, run_hour_utc, enabled_at, updated_at)
            VALUES ($1, $2, $3, $4, CASE WHEN $2 THEN $5::BIGINT END, $5)
            ON CONFLICT (index_id) DO UPDATE SET
                enabled      = EXCLUDED.enabled,
                days         = EXCLUDED.days,
                run_hour_utc = EXCLUDED.run_hour_utc,
                enabled_at   = CASE
                    WHEN NOT EXCLUDED.enabled THEN NULL
                    WHEN query_boost_schedules.enabled
                         AND query_boost_schedules.run_hour_utc = EXCLUDED.run_hour_utc
                        THEN query_boost_schedules.enabled_at
                    ELSE EXCLUDED.enabled_at
                END,
                updated_at   = EXCLUDED.updated_at
            RETURNING {_COLUMNS}
            """,
            index_id, enabled, days, run_hour_utc, now,
        )
    return _row_to_schedule(row)


async def list_enabled() -> list[Schedule]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_COLUMNS} FROM query_boost_schedules WHERE enabled ORDER BY index_id"
        )
    return [_row_to_schedule(r) for r in rows]


async def claim(index_id: str, slot: int, now: int) -> int | None:
    """
    Atomically claim the run for ``slot`` (epoch seconds).

    Returns the schedule's ``days`` when this caller won, or None when the
    run was already claimed, the schedule was turned off, or its hour
    changed since the caller read it.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            UPDATE query_boost_schedules
               SET last_run_at = $3, last_finished_at = NULL,
                   last_status = $4, last_message = ''
             WHERE index_id = $1
               AND enabled
               AND enabled_at < $2
               AND (last_run_at IS NULL OR last_run_at < $2)
            RETURNING days
            """,
            index_id, slot, now, STATUS_RUNNING,
        )


async def record_result(index_id: str, status: str, message: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE query_boost_schedules
               SET last_status = $2, last_message = $3, last_finished_at = $4
             WHERE index_id = $1
            """,
            index_id, status, message[:500], int(time.time()),
        )
