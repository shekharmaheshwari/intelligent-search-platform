# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Training coordination against real Postgres: the per-index lock, the
generation- and schedule-checked publish, and the races they close.

1. A model deleted while a run is in flight must stay deleted, including
   a scheduled run that starts after the delete.
2. A manual run started during a scheduled run (or vice versa) must not
   overlap it.
3. Training must never exhaust the shared pool, even with max_size=1.

The embedder is a stub that parks the run mid-training until the test
lets it continue, so each race is reproduced deterministically.
Skipped without DATABASE_URL.
"""
from __future__ import annotations
import asyncio
import os

import pytest

from isp.services.domain.query_boost import coordination, store, trainer
from isp.services.domain.query_boost.clustering import QueryCluster
from isp.services.domain.query_boost.scoring import LearnedBoost
from isp.services.infra import database as database_module
from tests.conftest import seed_events

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Postgres)")


@pytest.fixture(autouse=True)
async def _fresh_pool_and_cache():
    database_module._pool = None
    store.invalidate()
    yield
    store.invalidate()
    if database_module._pool is not None:
        await database_module._pool.close()
        database_module._pool = None


def _events(query: str, doc: str, n: int, clicks: int) -> list[dict]:
    row = {"query": query, "doc_id": doc, "rank": 0}
    return [{**row, "clicked": False}] * n + [{**row, "clicked": True}] * clicks


class _ParkedEmbedder:
    """Embeds, but first blocks until ``release()``. ``entered`` fires once training reaches it."""

    def __init__(self):
        self.entered = asyncio.Event()
        self._go = asyncio.Event()

    async def __call__(self, q: str) -> list[float]:
        self.entered.set()
        await self._go.wait()
        return [1.0, 0.0]

    def release(self):
        self._go.set()


# ── Lock ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lock_is_exclusive_per_index_and_released_on_exit():
    async with coordination.training_lock("idx-a") as a1:
        assert a1 is True
        async with coordination.training_lock("idx-a") as a2:
            assert a2 is False
        async with coordination.training_lock("idx-b") as b:
            assert b is True
    async with coordination.training_lock("idx-a") as again:
        assert again is True


@pytest.mark.asyncio
async def test_lock_released_when_the_body_raises():
    with pytest.raises(RuntimeError):
        async with coordination.training_lock("idx-err"):
            raise RuntimeError("boom")
    async with coordination.training_lock("idx-err") as acquired:
        assert acquired is True


# ── Generation-checked publish ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_generation_writes_nothing(test_index):
    idx = test_index["id"]
    c = QueryCluster(cluster_id=0)
    c.add("cheap iphone", [1.0, 0.0])
    gen = await store.current_generation(idx)

    await store.delete_model(idx)                       # bumps the generation
    ok = await store.save_model(idx, "m1", [c], [LearnedBoost(0, "se", 0.9, 10, 7)],
                                expected_generation=gen)
    assert ok is False
    assert await store.load_model(idx) is None

    ok = await store.save_model(idx, "m1", [c], [LearnedBoost(0, "se", 0.9, 10, 7)],
                                expected_generation=await store.current_generation(idx))
    assert ok is True
    assert (await store.load_model(idx)).boosts == {0: {"se": 0.9}}


# ── Race 1: delete during training ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_model_deleted_during_training_stays_deleted(db, test_index):
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7))
    embed = _ParkedEmbedder()

    run = asyncio.create_task(trainer.train(idx, 30, embed=embed))
    await asyncio.wait_for(embed.entered.wait(), 10)    # training has read events and is mid-run
    await store.delete_model(idx)                       # operator turns it off
    embed.release()
    result = await asyncio.wait_for(run, 10)

    assert result["superseded"] is True and result["trained"] is False
    store.invalidate()
    assert await store.load_model(idx) is None          # not resurrected


# ── Race 2: manual run during a scheduled run ────────────────────────────────

@pytest.mark.asyncio
async def test_second_run_for_the_same_index_is_busy_not_overlapping(db, test_index):
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7))
    embed = _ParkedEmbedder()

    first = asyncio.create_task(trainer.train(idx, 30, embed=embed))
    await asyncio.wait_for(embed.entered.wait(), 10)

    second = await trainer.train(idx, 30, embed=embed)  # "Train now" while the batch runs
    assert second["busy"] is True and second["trained"] is False

    embed.release()
    result = await asyncio.wait_for(first, 10)
    assert result["trained"] is True

    # Lock released: the next run proceeds normally.
    embed2 = _ParkedEmbedder()
    embed2.release()
    assert (await trainer.train(idx, 30, embed=embed2))["trained"] is True


# ── Pool safety: the lock never holds a pool connection ──────────────────────

@pytest.mark.asyncio
async def test_training_completes_with_a_single_connection_pool(db, test_index):
    # Regression: the lock used to hold a pool connection for the whole run
    # while training borrowed more. With max_size=1 that deadlocked.
    import asyncpg
    database_module._pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=1)
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7))

    async def embed(q):
        return [1.0, 0.0]
    result = await asyncio.wait_for(trainer.train(idx, 30, embed=embed), 15)
    assert result["trained"] is True


@pytest.mark.asyncio
async def test_many_concurrent_locks_leave_the_pool_usable():
    import asyncpg
    database_module._pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=1)

    async def run(i):
        async with coordination.training_lock(f"idx-{i}") as ok:
            assert ok
            async with database_module._pool.acquire() as conn:   # a training step
                await asyncio.sleep(0.01)
                return await conn.fetchval("SELECT 1")
    assert await asyncio.wait_for(asyncio.gather(*(run(i) for i in range(5))), 15) == [1] * 5


# ── DELETE is atomic with turning the schedule off ───────────────────────────

async def _enable_schedule(idx):
    from isp.services.domain.query_boost import schedule_store
    await schedule_store.save_schedule(idx, enabled=True, days=30, run_hour_utc=2)


@pytest.mark.asyncio
async def test_scheduled_run_starting_after_delete_cannot_publish(db, test_index):
    # Reviewer's interleaving: the scheduled run reads its generation *after*
    # the delete bumped it, so the generation check alone passes. The
    # schedule-enabled check, done under the same row lock, refuses it.
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7))
    await _enable_schedule(idx)

    assert await store.delete_model(idx, disable_schedule=True) is True

    async def embed(q):
        return [1.0, 0.0]
    result = await trainer.train(idx, 30, embed=embed, scheduled=True)
    assert result["superseded"] is True
    store.invalidate()
    assert await store.load_model(idx) is None


@pytest.mark.asyncio
async def test_scheduled_run_in_flight_during_delete_is_discarded(db, test_index):
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7))
    await _enable_schedule(idx)
    embed = _ParkedEmbedder()

    run = asyncio.create_task(trainer.train(idx, 30, embed=embed, scheduled=True))
    await asyncio.wait_for(embed.entered.wait(), 10)
    await store.delete_model(idx, disable_schedule=True)
    embed.release()
    assert (await asyncio.wait_for(run, 10))["superseded"] is True
    store.invalidate()
    assert await store.load_model(idx) is None


@pytest.mark.asyncio
async def test_schedule_turned_off_mid_run_discards_the_scheduled_result(db, test_index):
    from isp.services.domain.query_boost import schedule_store
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7))
    await _enable_schedule(idx)
    embed = _ParkedEmbedder()

    run = asyncio.create_task(trainer.train(idx, 30, embed=embed, scheduled=True))
    await asyncio.wait_for(embed.entered.wait(), 10)
    await schedule_store.save_schedule(idx, enabled=False, days=30, run_hour_utc=2)
    embed.release()
    assert (await asyncio.wait_for(run, 10))["superseded"] is True


@pytest.mark.asyncio
async def test_manual_train_after_delete_is_an_explicit_retrain(db, test_index):
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7))
    await _enable_schedule(idx)
    await store.delete_model(idx, disable_schedule=True)

    async def embed(q):
        return [1.0, 0.0]
    assert (await trainer.train(idx, 30, embed=embed))["trained"] is True


@pytest.mark.asyncio
async def test_delete_without_schedule_reports_nothing_disabled(test_index):
    assert await store.delete_model(test_index["id"], disable_schedule=True) is False


@pytest.mark.asyncio
async def test_disable_committing_while_publish_waits_on_the_schedule_row_wins(db, test_index):
    # Reviewer's race: the schedule is turned off between the publish's
    # enabled check and its commit. The disable holds the schedule row
    # (uncommitted) while the scheduled run reaches publish. The publish's
    # FOR SHARE read must wait for it, then see "off" and refuse.
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7))
    await _enable_schedule(idx)

    tx = db.transaction()
    await tx.start()
    await db.execute("UPDATE query_boost_schedules SET enabled = FALSE, enabled_at = NULL WHERE index_id = $1", idx)

    async def embed(q):
        return [1.0, 0.0]
    run = asyncio.create_task(trainer.train(idx, 30, embed=embed, scheduled=True))
    await asyncio.sleep(0.5)
    assert not run.done()            # blocked on the schedule row, not past the check
    await tx.commit()                # the PUT "returns": schedule is off

    assert (await asyncio.wait_for(run, 10))["superseded"] is True
    store.invalidate()
    assert await store.load_model(idx) is None
