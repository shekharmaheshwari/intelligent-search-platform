# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
query_boost.schedule_store against the real Postgres test DB. Skipped
without DATABASE_URL, like test_store.py.
"""
from __future__ import annotations
import asyncio
import os

import pytest

from isp.services.domain.query_boost import schedule_store
from isp.services.infra import database as database_module

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Postgres)")


@pytest.fixture(autouse=True)
async def _fresh_pool():
    database_module._pool = None
    yield
    if database_module._pool is not None:
        await database_module._pool.close()
        database_module._pool = None


@pytest.mark.asyncio
async def test_default_when_never_saved(test_index):
    s = await schedule_store.get_schedule(test_index["id"])
    assert (s.enabled, s.days, s.run_hour_utc, s.enabled_at) == (False, 30, 2, None)


@pytest.mark.asyncio
async def test_save_and_read_back(test_index):
    idx = test_index["id"]
    saved = await schedule_store.save_schedule(idx, enabled=True, days=14, run_hour_utc=5)
    assert saved.enabled and saved.days == 14 and saved.run_hour_utc == 5
    assert saved.enabled_at is not None
    assert await schedule_store.get_schedule(idx) == saved


@pytest.mark.asyncio
async def test_enabled_at_kept_on_days_change_reset_on_hour_change_cleared_on_off(test_index, db):
    idx = test_index["id"]
    await schedule_store.save_schedule(idx, enabled=True, days=30, run_hour_utc=2)
    await db.execute("UPDATE query_boost_schedules SET enabled_at = 100 WHERE index_id = $1", idx)

    s = await schedule_store.save_schedule(idx, enabled=True, days=7, run_hour_utc=2)
    assert s.enabled_at == 100

    s = await schedule_store.save_schedule(idx, enabled=True, days=7, run_hour_utc=4)
    assert s.enabled_at > 100

    s = await schedule_store.save_schedule(idx, enabled=False, days=7, run_hour_utc=4)
    assert s.enabled_at is None and s.days == 7


@pytest.mark.asyncio
async def test_claim_once_per_slot_and_concurrently_exclusive(test_index, db):
    idx = test_index["id"]
    await schedule_store.save_schedule(idx, enabled=True, days=9, run_hour_utc=2)
    await db.execute("UPDATE query_boost_schedules SET enabled_at = 100 WHERE index_id = $1", idx)

    # Five "instances" race for the same slot; exactly one wins.
    results = await asyncio.gather(*(schedule_store.claim(idx, 1000, 1001) for _ in range(5)))
    assert sorted(results, key=lambda r: r is None) == [9, None, None, None, None]

    s = await schedule_store.get_schedule(idx)
    assert (s.last_run_at, s.last_status) == (1001, "running")

    assert await schedule_store.claim(idx, 2000, 2001) == 9      # next slot
    assert await schedule_store.claim(idx, 2000, 2002) is None   # same slot again


@pytest.mark.asyncio
async def test_claim_refused_when_disabled_or_enabled_after_slot(test_index, db):
    idx = test_index["id"]
    await schedule_store.save_schedule(idx, enabled=True, days=30, run_hour_utc=2)
    await db.execute("UPDATE query_boost_schedules SET enabled_at = 5000 WHERE index_id = $1", idx)
    assert await schedule_store.claim(idx, 1000, 1001) is None

    await schedule_store.save_schedule(idx, enabled=False, days=30, run_hour_utc=2)
    assert await schedule_store.claim(idx, 9000, 9001) is None


@pytest.mark.asyncio
async def test_record_result_and_listing(test_index):
    idx = test_index["id"]
    await schedule_store.save_schedule(idx, enabled=True, days=30, run_hour_utc=2)
    await schedule_store.record_result(idx, "trained", "x" * 600)
    s = await schedule_store.get_schedule(idx)
    assert s.last_status == "trained" and len(s.last_message) == 500 and s.last_finished_at
    assert idx in [r.index_id for r in await schedule_store.list_enabled()]
