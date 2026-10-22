# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Tests for GET /v1/indexes/{index_id}/analytics

Covers:
  - Auth guard (401 without token, 401 with bad token)
  - Validation (422 on invalid range)
  - 404 on unknown index
  - Empty response when no events exist
  - Response shape for all three ranges (7d / 30d / 90d)
  - Aggregation correctness: total_searches, unique_queries, CTR, zero_result_rate
  - Volume array: correct length, all dates present, counts and clicks per day
  - Top queries: sorted by count descending, capped at 20
  - Zero results list: only queries with no clicks
  - Daily click counts in volume points
"""
from __future__ import annotations
import time
import datetime

import pytest

from tests.conftest import seed_events


# ── Auth & validation ─────────────────────────────────────────────────────────

async def test_analytics_requires_auth(client, test_index):
    res = await client.get(f"/v1/indexes/{test_index['id']}/analytics?range=30d")
    assert res.status_code == 401


async def test_analytics_rejects_invalid_token(client, test_index):
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert res.status_code == 401


async def test_analytics_invalid_range_returns_422(client, auth_headers, test_index):
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=999d",
        headers=auth_headers,
    )
    assert res.status_code == 422


async def test_analytics_unknown_index_returns_404(client, auth_headers):
    res = await client.get(
        "/v1/indexes/nonexistent-index-xyz/analytics?range=30d",
        headers=auth_headers,
    )
    assert res.status_code == 404


# ── Response shape ────────────────────────────────────────────────────────────

async def test_analytics_response_has_required_keys(client, auth_headers, test_index):
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == {"summary", "top_queries", "zero_results", "volume", "top_facets"}


async def test_analytics_summary_has_required_fields(client, auth_headers, test_index):
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    summary = res.json()["summary"]
    assert "total_searches" in summary
    assert "unique_queries" in summary
    assert "ctr"             in summary
    assert "zero_result_rate" in summary
    assert "avg_results"     in summary


async def test_analytics_volume_point_has_required_fields(client, auth_headers, db, test_index):
    await seed_events(db, test_index["id"], [{"query": "q", "clicked": False}])
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=7d",
        headers=auth_headers,
    )
    volume = res.json()["volume"]
    assert len(volume) > 0
    pt = volume[0]
    assert "date"   in pt
    assert "count"  in pt
    assert "clicks" in pt


# ── Empty state ───────────────────────────────────────────────────────────────

async def test_analytics_empty_when_no_events(client, auth_headers, test_index):
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["total_searches"] == 0
    assert body["summary"]["ctr"] == 0.0
    assert body["top_queries"] == []
    assert body["zero_results"] == []


# ── Range lengths ─────────────────────────────────────────────────────────────

async def test_analytics_7d_volume_has_7_points(client, auth_headers, db, test_index):
    await seed_events(db, test_index["id"], [{"query": "test"}])
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=7d",
        headers=auth_headers,
    )
    assert len(res.json()["volume"]) == 7


async def test_analytics_30d_volume_has_30_points(client, auth_headers, db, test_index):
    await seed_events(db, test_index["id"], [{"query": "test"}])
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    assert len(res.json()["volume"]) == 30


async def test_analytics_90d_volume_has_90_points(client, auth_headers, db, test_index):
    await seed_events(db, test_index["id"], [{"query": "test"}])
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=90d",
        headers=auth_headers,
    )
    assert len(res.json()["volume"]) == 90


async def test_analytics_volume_dates_are_continuous(client, auth_headers, db, test_index):
    await seed_events(db, test_index["id"], [{"query": "test"}])
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=7d",
        headers=auth_headers,
    )
    dates = [pt["date"] for pt in res.json()["volume"]]
    today = datetime.date.today()
    expected = [(today - datetime.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    assert dates == expected


# ── Aggregation correctness ───────────────────────────────────────────────────

async def test_analytics_counts_distinct_session_query_pairs(client, auth_headers, db, test_index):
    """
    3 distinct (session, query) pairs → total_searches = 3,
    even if one pair has multiple impression rows.
    """
    now_ms = int(time.time() * 1000)
    events = [
        {"session_id": "s1", "query": "iphone",  "clicked": True,  "created_at": now_ms},
        {"session_id": "s1", "query": "iphone",  "clicked": False, "created_at": now_ms},  # duplicate
        {"session_id": "s2", "query": "iphone",  "clicked": False, "created_at": now_ms},
        {"session_id": "s3", "query": "samsung",  "clicked": False, "created_at": now_ms},
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    assert res.json()["summary"]["total_searches"] == 3


async def test_analytics_ctr_is_fraction_of_clicked_sessions(client, auth_headers, db, test_index):
    """
    2 sessions: one clicked, one did not → CTR = 0.5
    """
    now_ms = int(time.time() * 1000)
    events = [
        {"session_id": "s1", "query": "q", "clicked": True,  "created_at": now_ms},
        {"session_id": "s2", "query": "q", "clicked": False, "created_at": now_ms},
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    assert res.json()["summary"]["ctr"] == 0.5


async def test_analytics_zero_result_rate_is_complement_of_ctr(client, auth_headers, db, test_index):
    now_ms = int(time.time() * 1000)
    events = [
        {"session_id": "s1", "query": "q", "clicked": True,  "created_at": now_ms},
        {"session_id": "s2", "query": "q", "clicked": False, "created_at": now_ms},
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    summary = res.json()["summary"]
    assert round(summary["ctr"] + summary["zero_result_rate"], 4) == 1.0


async def test_analytics_unique_queries_excludes_empty_strings(client, auth_headers, db, test_index):
    now_ms = int(time.time() * 1000)
    events = [
        {"session_id": "s1", "query": "iphone",  "created_at": now_ms},
        {"session_id": "s2", "query": "samsung",  "created_at": now_ms},
        {"session_id": "s3", "query": "",          "created_at": now_ms},
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    assert res.json()["summary"]["unique_queries"] == 2


# ── Top queries ───────────────────────────────────────────────────────────────

async def test_analytics_top_queries_sorted_by_count(client, auth_headers, db, test_index):
    now_ms = int(time.time() * 1000)
    events = (
        [{"session_id": f"s{i}", "query": "iphone",  "created_at": now_ms} for i in range(5)] +
        [{"session_id": f"s{i}", "query": "samsung", "created_at": now_ms} for i in range(5, 7)]
    )
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    queries = res.json()["top_queries"]
    assert queries[0]["query"] == "iphone"
    assert queries[0]["count"] > queries[1]["count"]


async def test_analytics_top_queries_capped_at_20(client, auth_headers, db, test_index):
    now_ms = int(time.time() * 1000)
    events = [
        {"session_id": f"s{i}", "query": f"query-{i}", "created_at": now_ms}
        for i in range(25)
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    assert len(res.json()["top_queries"]) <= 20


# ── Zero results ──────────────────────────────────────────────────────────────

async def test_analytics_zero_results_only_contains_unclicked_queries(client, auth_headers, db, test_index):
    now_ms = int(time.time() * 1000)
    events = [
        {"session_id": "s1", "query": "clicked-query",   "clicked": True,  "created_at": now_ms},
        {"session_id": "s2", "query": "unclicked-query",  "clicked": False, "created_at": now_ms},
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    zero_queries = [q["query"] for q in res.json()["zero_results"]]
    assert "unclicked-query" in zero_queries
    assert "clicked-query" not in zero_queries


async def test_analytics_zero_results_empty_when_all_clicked(client, auth_headers, db, test_index):
    now_ms = int(time.time() * 1000)
    events = [
        {"session_id": "s1", "query": "iphone", "clicked": True, "created_at": now_ms},
        {"session_id": "s2", "query": "samsung", "clicked": True, "created_at": now_ms},
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    assert res.json()["zero_results"] == []


# ── Volume / clicks per day ───────────────────────────────────────────────────

async def test_analytics_volume_counts_events_on_correct_day(client, auth_headers, db, test_index):
    today_ms = int(datetime.datetime.now(datetime.timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0,
    ).timestamp() * 1000)
    events = [
        {"session_id": f"s{i}", "query": "q", "clicked": False, "created_at": today_ms}
        for i in range(3)
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=7d",
        headers=auth_headers,
    )
    today_str = datetime.date.today().isoformat()
    today_pt = next(pt for pt in res.json()["volume"] if pt["date"] == today_str)
    assert today_pt["count"] == 3


async def test_analytics_volume_click_count_per_day(client, auth_headers, db, test_index):
    today_ms = int(datetime.datetime.now(datetime.timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0,
    ).timestamp() * 1000)
    events = [
        {"session_id": "s1", "query": "q", "clicked": True,  "created_at": today_ms},
        {"session_id": "s2", "query": "q", "clicked": True,  "created_at": today_ms},
        {"session_id": "s3", "query": "q", "clicked": False, "created_at": today_ms},
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=7d",
        headers=auth_headers,
    )
    today_str = datetime.date.today().isoformat()
    today_pt = next(pt for pt in res.json()["volume"] if pt["date"] == today_str)
    assert today_pt["clicks"] == 2


async def test_analytics_volume_gaps_filled_with_zero(client, auth_headers, db, test_index):
    """Days with no events should still appear with count=0, clicks=0."""
    now_ms = int(time.time() * 1000)
    await seed_events(db, test_index["id"], [{"query": "q", "created_at": now_ms}])
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=7d",
        headers=auth_headers,
    )
    volume = res.json()["volume"]
    zero_days = [pt for pt in volume if pt["count"] == 0]
    assert len(zero_days) >= 1
    for pt in zero_days:
        assert pt["clicks"] == 0


async def test_analytics_excludes_events_outside_range(client, auth_headers, db, test_index):
    """Events older than the range window must not appear in the results."""
    old_ms = int((time.time() - 91 * 86400) * 1000)   # 91 days ago — outside 90d window
    now_ms = int(time.time() * 1000)
    events = [
        {"session_id": "old", "query": "old-query", "clicked": False, "created_at": old_ms},
        {"session_id": "new", "query": "new-query", "clicked": False, "created_at": now_ms},
    ]
    await seed_events(db, test_index["id"], events)
    res = await client.get(
        f"/v1/indexes/{test_index['id']}/analytics?range=30d",
        headers=auth_headers,
    )
    query_names = [q["query"] for q in res.json()["top_queries"]]
    assert "old-query" not in query_names
    assert "new-query" in query_names
