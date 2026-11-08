# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for query_boost.scheduler.run_due.

schedule_store is replaced with an in-memory fake whose ``claim`` follows
the same rule as the SQL, so these cover the loop's control flow without
Postgres. The real claim SQL is covered in test_schedule_store.py.
"""
from __future__ import annotations
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from isp.services.domain.query_boost import schedule, scheduler
from isp.services.domain.query_boost.schedule import Schedule

NOW = datetime(2026, 11, 8, 3, 0, tzinfo=timezone.utc)
ENABLED_AT = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp())


class FakeStore:
    def __init__(self, *schedules: Schedule):
        self.rows = {s.index_id: s for s in schedules}
        self.results: dict[str, tuple[str, str]] = {}

    async def list_enabled(self):
        return [s for s in self.rows.values() if s.enabled]

    async def claim(self, index_id, slot, now):
        s = self.rows[index_id]
        if s.enabled and s.enabled_at < slot and (s.last_run_at is None or s.last_run_at < slot):
            s.last_run_at = now
            return s.days
        return None

    async def record_result(self, index_id, status, message):
        self.results[index_id] = (status, message)


def _s(index_id: str, **kw) -> Schedule:
    return Schedule(index_id=index_id, **{"enabled": True, "enabled_at": ENABLED_AT, **kw})


async def _run(store: FakeStore, train):
    with patch.object(scheduler, "schedule_store", store):
        return await scheduler.run_due(NOW, train=train)


@pytest.mark.asyncio
async def test_trains_due_index_with_its_own_days():
    store = FakeStore(_s("a", days=7))
    calls = []

    async def train(idx, days):
        calls.append((idx, days))
        return {"trained": True, "docs_boosted": 2, "n_clusters": 1, "n_events": 30}

    assert await _run(store, train) == ["a"]
    assert calls == [("a", 7)]
    assert store.results["a"][0] == schedule.STATUS_TRAINED


@pytest.mark.asyncio
async def test_skips_not_due_and_disabled():
    ran_today = int(datetime(2026, 11, 8, 2, 0, 5, tzinfo=timezone.utc).timestamp())
    store = FakeStore(_s("done", last_run_at=ran_today), _s("off", enabled=False))

    async def train(idx, days):
        raise AssertionError("should not train")

    assert await _run(store, train) == []


@pytest.mark.asyncio
async def test_second_run_in_same_slot_does_nothing():
    store = FakeStore(_s("a"))
    n = 0

    async def train(idx, days):
        nonlocal n
        n += 1
        return {"trained": True}

    await _run(store, train)
    await _run(store, train)
    assert n == 1


@pytest.mark.asyncio
async def test_lost_claim_is_skipped():
    store = FakeStore(_s("a"))

    async def lost(*_):
        return None
    store.claim = lost

    async def train(idx, days):
        raise AssertionError("another instance owns this run")

    assert await _run(store, train) == []


@pytest.mark.asyncio
async def test_failure_is_recorded_and_other_indexes_still_run():
    store = FakeStore(_s("bad"), _s("good"))

    async def train(idx, days):
        if idx == "bad":
            raise RuntimeError("embedding provider down")
        return {"trained": False, "n_events": 1}

    assert await _run(store, train) == ["bad", "good"]
    assert store.results["bad"] == (schedule.STATUS_ERROR, "RuntimeError: embedding provider down")
    assert store.results["good"][0] == schedule.STATUS_SKIPPED


def test_start_respects_config_flag():
    from isp.config import settings
    with patch.object(settings, "training_scheduler_enabled", False):
        scheduler.start()
    assert scheduler._task is None
