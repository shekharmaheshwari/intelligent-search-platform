# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Tests for POST /v1/training/popularity (Case A trained re-scorer).

Covers:
  - Auth guard (401 without token)
  - 404-shaped response on unknown index (matches /v1/analytics/aggregate's
    "error" body shape, not a raw 404 — same convention)
  - Graceful empty-data handling: no events, too few events, and events
    against a Typesense collection that doesn't exist (test_index only
    inserts a Postgres row, never a real Typesense collection) all return
    docs_scored=0 without erroring
  - A training_runs row is written for every call, even the empty ones

Full end-to-end training (real Typesense docs, a nonzero trained score
written back) is verified manually against a real index per
docs/trainedRescorerDesign.md's verification section — not reproducible
here since conftest.py's test_index fixture doesn't provision a real
Typesense collection.
"""
from __future__ import annotations

from tests.conftest import seed_events


async def test_training_requires_auth(client, test_index):
    res = await client.post(f"/v1/training/popularity?index_id={test_index['id']}")
    assert res.status_code == 401


async def test_training_unknown_index_returns_error_body(client, auth_headers):
    res = await client.post(
        "/v1/training/popularity?index_id=nonexistent-index-xyz",
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["docs_scored"] == 0
    assert "error" in body


async def test_training_no_events_returns_zero_scored(client, auth_headers, test_index):
    res = await client.post(
        f"/v1/training/popularity?index_id={test_index['id']}",
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["docs_scored"] == 0
    assert body["n_events"] == 0


async def test_training_too_few_events_returns_zero_scored(client, auth_headers, db, test_index):
    # Below _MIN_EVENTS (5) — should skip training gracefully, not error.
    await seed_events(db, test_index["id"], [
        {"query": "", "doc_id": "d1", "rank": 0, "clicked": True},
        {"query": "", "doc_id": "d2", "rank": 1, "clicked": False},
    ])
    res = await client.post(
        f"/v1/training/popularity?index_id={test_index['id']}",
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["docs_scored"] == 0
    assert body["n_events"] == 2


async def test_training_events_against_missing_typesense_collection_is_graceful(
    client, auth_headers, db, test_index,
):
    # test_index only inserts a Postgres row — no real Typesense collection
    # exists for it. Enough browse events to clear _MIN_EVENTS, but with no
    # documents to join features against, training should still degrade to
    # docs_scored=0 rather than a 500.
    await seed_events(db, test_index["id"], [
        {"query": "", "doc_id": f"d{i}", "rank": i, "clicked": i % 2 == 0}
        for i in range(6)
    ])
    res = await client.post(
        f"/v1/training/popularity?index_id={test_index['id']}",
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["docs_scored"] == 0
    assert body["n_events"] == 6


async def test_training_logs_a_training_run(client, auth_headers, db, test_index):
    await client.post(
        f"/v1/training/popularity?index_id={test_index['id']}",
        headers=auth_headers,
    )
    row = await db.fetchrow(
        "SELECT case_name, model_type FROM training_runs WHERE index_id = $1",
        test_index["id"],
    )
    assert row is not None
    assert row["case_name"] == "A"
    assert row["model_type"] == "logistic_regression"
