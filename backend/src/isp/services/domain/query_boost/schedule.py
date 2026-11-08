# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Per-index batch-learning schedule for the learned query boost: when a
scheduled retrain is due.

Pure functions only, with no I/O. ``schedule_store`` persists schedules and
``scheduler`` runs the background loop that acts on them.

Semantics
---------
A schedule runs once a day at ``run_hour_utc``:00 UTC. A run is due when
the most recent such time (the *slot*):

* is after ``enabled_at``. Turning a schedule on, or changing its hour,
  starts it from the next slot, never immediately. Use the "train now"
  endpoint for an immediate run.
* has not been run yet: ``last_run_at`` is before the slot.

A missed slot, such as when the API was down at 02:00, runs on the next
check after the API comes back, once. Several missed days still give one
run, because each run reads the latest ``days`` of events anyway.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

DEFAULT_DAYS = 30
DEFAULT_RUN_HOUR_UTC = 2
MIN_DAYS, MAX_DAYS = 1, 365

# last_status values
STATUS_RUNNING = "running"
STATUS_TRAINED = "trained"
STATUS_SKIPPED = "skipped"   # ran, but not enough traffic; previous model kept
STATUS_ERROR = "error"


@dataclass
class Schedule:
    index_id: str
    enabled: bool = False
    days: int = DEFAULT_DAYS
    run_hour_utc: int = DEFAULT_RUN_HOUR_UTC
    enabled_at: int | None = None        # epoch seconds; None while disabled
    last_run_at: int | None = None       # epoch seconds a run was claimed
    last_finished_at: int | None = None
    last_status: str = ""
    last_message: str = ""


def latest_slot(run_hour_utc: int, now: datetime) -> datetime:
    """The most recent ``run_hour_utc``:00 UTC at or before ``now``."""
    now = now.astimezone(timezone.utc)
    slot = now.replace(hour=run_hour_utc, minute=0, second=0, microsecond=0)
    return slot if slot <= now else slot - timedelta(days=1)


def is_due(s: Schedule, now: datetime) -> bool:
    """True when ``s`` should run now (see module docstring)."""
    if not s.enabled or s.enabled_at is None:
        return False
    slot = latest_slot(s.run_hour_utc, now).timestamp()
    return slot > s.enabled_at and (s.last_run_at is None or s.last_run_at < slot)


def next_run_at(s: Schedule, now: datetime) -> int | None:
    """
    Epoch seconds of the next scheduled run, or None when disabled.

    When a run is already due (it will start on the scheduler's next check)
    this is the overdue slot, which is in the past.
    """
    if not s.enabled:
        return None
    slot = latest_slot(s.run_hour_utc, now)
    return int(slot.timestamp() if is_due(s, now) else (slot + timedelta(days=1)).timestamp())


def to_dict(s: Schedule, now: datetime) -> dict:
    """API representation: the stored fields plus ``next_run_at``."""
    return {**asdict(s), "next_run_at": next_run_at(s, now)}


def describe_result(result: dict) -> tuple[str, str]:
    """Map a ``trainer.train`` result to ``(last_status, last_message)``."""
    if result.get("busy"):
        return STATUS_SKIPPED, "Another training run for this index was in progress"
    if result.get("superseded"):
        return STATUS_SKIPPED, (
            "The model was deleted or replaced, or the schedule turned off, while this run "
            "was training; result discarded"
        )
    if result.get("trained"):
        return STATUS_TRAINED, (
            f"{result.get('docs_boosted', 0)} products boosted across "
            f"{result.get('n_clusters', 0)} query groups from {result.get('n_events', 0)} events"
        )
    return STATUS_SKIPPED, (
        f"Not enough query traffic ({result.get('n_events', 0)} events); previous model kept"
    )
