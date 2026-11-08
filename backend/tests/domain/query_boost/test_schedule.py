# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for query_boost.schedule — pure due-time logic, no I/O."""
from __future__ import annotations
from datetime import datetime, timezone

import pytest

from isp.services.domain.query_boost import schedule
from isp.services.domain.query_boost.schedule import Schedule


def _t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def _ts(day: int, hour: int, minute: int = 0) -> int:
    return int(_t(day, hour, minute).timestamp())


def _sched(**kw) -> Schedule:
    base = dict(index_id="idx", enabled=True, run_hour_utc=2, enabled_at=_ts(1, 12))
    return Schedule(**{**base, **kw})


class TestLatestSlot:
    def test_after_the_hour_is_today(self):
        assert schedule.latest_slot(2, _t(8, 10)) == _t(8, 2)

    def test_before_the_hour_is_yesterday(self):
        assert schedule.latest_slot(2, _t(8, 1, 59)) == _t(7, 2)

    def test_exactly_on_the_hour_is_now(self):
        assert schedule.latest_slot(2, _t(8, 2)) == _t(8, 2)

    def test_non_utc_now_is_converted(self):
        from datetime import timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        # 08:00 IST on the 8th is 02:30 UTC, so today's 02:00 UTC slot.
        assert schedule.latest_slot(2, datetime(2026, 11, 8, 8, 0, tzinfo=ist)) == _t(8, 2)


class TestIsDue:
    def test_never_run_and_slot_after_enabling(self):
        assert schedule.is_due(_sched(), _t(8, 3))

    def test_disabled_never_due(self):
        assert not schedule.is_due(_sched(enabled=False), _t(8, 3))

    def test_missing_enabled_at_never_due(self):
        assert not schedule.is_due(_sched(enabled_at=None), _t(8, 3))

    def test_enabling_after_todays_slot_waits_for_tomorrow(self):
        s = _sched(enabled_at=_ts(8, 10))
        assert not schedule.is_due(s, _t(8, 11))
        assert schedule.is_due(s, _t(9, 2))

    def test_already_ran_this_slot(self):
        assert not schedule.is_due(_sched(last_run_at=_ts(8, 2, 1)), _t(8, 23))

    def test_ran_yesterday_is_due_today(self):
        assert schedule.is_due(_sched(last_run_at=_ts(7, 2, 1)), _t(8, 2, 1))

    def test_many_missed_days_give_one_run(self):
        s = _sched(last_run_at=_ts(2, 2))
        assert schedule.is_due(s, _t(8, 9))
        s.last_run_at = _ts(8, 9)
        assert not schedule.is_due(s, _t(8, 9, 1))


class TestNextRunAt:
    def test_disabled_has_no_next_run(self):
        assert schedule.next_run_at(_sched(enabled=False), _t(8, 3)) is None

    def test_after_running_today_is_tomorrow(self):
        assert schedule.next_run_at(_sched(last_run_at=_ts(8, 2)), _t(8, 3)) == _ts(9, 2)

    def test_before_the_hour_is_today(self):
        assert schedule.next_run_at(_sched(last_run_at=_ts(7, 2)), _t(8, 1)) == _ts(8, 2)

    def test_due_now_reports_the_overdue_slot(self):
        assert schedule.next_run_at(_sched(), _t(8, 5)) == _ts(8, 2)


class TestDescribeResult:
    def test_trained(self):
        status, msg = schedule.describe_result(
            {"trained": True, "docs_boosted": 3, "n_clusters": 2, "n_events": 40})
        assert status == schedule.STATUS_TRAINED
        assert msg == "3 products boosted across 2 query groups from 40 events"

    def test_busy(self):
        status, msg = schedule.describe_result({"trained": False, "busy": True})
        assert status == schedule.STATUS_SKIPPED and "in progress" in msg

    def test_superseded(self):
        status, msg = schedule.describe_result({"trained": False, "superseded": True})
        assert status == schedule.STATUS_SKIPPED and "discarded" in msg

    def test_not_enough_traffic(self):
        status, msg = schedule.describe_result({"trained": False, "n_events": 2})
        assert status == schedule.STATUS_SKIPPED
        assert "2 events" in msg and "previous model kept" in msg


def test_to_dict_includes_next_run():
    d = schedule.to_dict(_sched(last_run_at=_ts(8, 2)), _t(8, 3))
    assert d["next_run_at"] == _ts(9, 2)
    assert d["days"] == 30 and d["enabled"] is True


@pytest.mark.parametrize("hour", [0, 23])
def test_edge_hours(hour):
    s = _sched(run_hour_utc=hour, enabled_at=_ts(1, 0) - 1)
    assert schedule.is_due(s, _t(8, hour))
