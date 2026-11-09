# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Background loop that runs scheduled query-boost retrains.

Started from the app lifespan (``start()``/``stop()``). Every
``TICK_SECONDS`` it calls ``run_due()``, which:

1. lists enabled schedules,
2. keeps the ones that are due (``schedule.is_due``),
3. claims each with ``schedule_store.claim``, an atomic UPDATE, so only one
   API instance trains a given run,
4. trains with the schedule's ``days`` window and records the outcome.

Indexes train one at a time, so a nightly batch never competes with itself
for the embedding provider or Postgres. A failure on one index is recorded
on that index's schedule and doesn't stop the others. The loop itself
never dies on an error.

Set ``TRAINING_SCHEDULER_ENABLED=false`` to turn the loop off on an
instance, for example on read-only replicas. Schedules are still stored and
editable.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from isp.services.domain.query_boost import schedule, schedule_store

log = logging.getLogger(__name__)

TICK_SECONDS = 60

Trainer = Callable[[str, int], Awaitable[dict]]

_task: asyncio.Task | None = None


async def _default_train(index_id: str, days: int) -> dict:
    from isp.services.domain.query_boost import trainer
    return await trainer.train(index_id, days, scheduled=True)


async def run_due(now: datetime | None = None, *, train: Trainer | None = None) -> list[str]:
    """Run every due schedule once. Returns the ids of the indexes this call trained."""
    now = now or datetime.now(timezone.utc)
    train = train or _default_train
    ran: list[str] = []
    for s in await schedule_store.list_enabled():
        if not schedule.is_due(s, now):
            continue
        slot = int(schedule.latest_slot(s.run_hour_utc, now).timestamp())
        days = await schedule_store.claim(s.index_id, slot, int(now.timestamp()))
        if days is None:
            continue  # another instance claimed it, or the schedule just changed
        log.info("query-boost schedule: training index %s (days=%d)", s.index_id, days)
        try:
            status, message = schedule.describe_result(await train(s.index_id, days))
        except Exception as exc:
            log.exception("query-boost schedule: training failed for index %s", s.index_id)
            status, message = schedule.STATUS_ERROR, f"{type(exc).__name__}: {exc}"
        await schedule_store.record_result(s.index_id, status, message)
        ran.append(s.index_id)
    return ran


async def _loop() -> None:
    while True:
        try:
            await run_due()
        except Exception:
            log.exception("query-boost schedule: check failed; retrying next tick")
        await asyncio.sleep(TICK_SECONDS)


def start() -> None:
    """Start the loop unless disabled by config. Returns immediately."""
    global _task
    from isp.config import settings
    if not settings.training_scheduler_enabled:
        log.info("Training scheduler is OFF on this instance.")
        return
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
