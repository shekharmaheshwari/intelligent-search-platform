# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Router tests for /v1/training/query-boost (Case B learned query boost).

Like test_training.py, these run against the already-running server process
and the real Postgres test DB (see conftest.py). They cover routing, auth,
and the train → inspect → delete life cycle. Ranking effects are covered by
the unit tests in tests/domain/query_boost/, since conftest's test_index
has no real Typesense collection to search.
"""
from __future__ import annotations

import pytest

from tests.conftest import seed_events


def _events(query: str, doc: str, n: int, clicks: int, rank: int = 0) -> list[dict]:
    """``n`` impression rows plus ``clicks`` separate click rows, as analytics logs them."""
    row = {"query": query, "doc_id": doc, "rank": rank}
    return [{**row, "clicked": False} for _ in range(n)] + [{**row, "clicked": True} for _ in range(clicks)]


async def test_requires_auth(client, test_index):
    for method in ("post", "get", "delete"):
        res = await getattr(client, method)(f"/v1/training/query-boost?index_id={test_index['id']}")
        assert res.status_code == 401, method


async def test_unknown_index_is_404(client, auth_headers):
    res = await client.post("/v1/training/query-boost?index_id=nonexistent-index-xyz", headers=auth_headers)
    assert res.status_code == 404


async def test_too_few_events_does_not_train(client, auth_headers, db, test_index):
    await seed_events(db, test_index["id"], _events("cheap iphone", "se", 2, 1))
    res = await client.post(f"/v1/training/query-boost?index_id={test_index['id']}", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["trained"] is False


async def test_train_inspect_delete(client, auth_headers, db, test_index):
    idx = test_index["id"]
    await seed_events(db, idx, _events("cheap iphone", "iphone-se", 10, 7)
                      + _events("cheap iphone", "iphone-pro", 10, 0, rank=1))

    res = await client.post(f"/v1/training/query-boost?index_id={idx}", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["trained"] is True and body["docs_boosted"] == 1

    summary = (await client.get(f"/v1/training/query-boost?index_id={idx}", headers=auth_headers)).json()
    assert summary["trained"] is True
    (cluster,) = [c for c in summary["clusters"] if "cheap iphone" in c["queries"]]
    assert cluster["top_boosts"][0]["doc_id"] == "iphone-se"

    run = await db.fetchrow(
        "SELECT case_name, model_type FROM training_runs WHERE index_id = $1 ORDER BY id DESC LIMIT 1", idx)
    assert (run["case_name"], run["model_type"]) == ("B", "ctr_shrinkage")

    res = await client.delete(f"/v1/training/query-boost?index_id={idx}", headers=auth_headers)
    assert res.status_code == 200
    summary = (await client.get(f"/v1/training/query-boost?index_id={idx}", headers=auth_headers)).json()
    assert summary["trained"] is False


# ── Batch-learning schedule ─────────────────────────────────────────────────

async def test_schedule_requires_auth(client, test_index):
    url = f"/v1/training/query-boost/schedule?index_id={test_index['id']}"
    assert (await client.get(url)).status_code == 401
    assert (await client.put(url, json={"enabled": True})).status_code == 401


async def test_schedule_unknown_index_is_404(client, auth_headers):
    url = "/v1/training/query-boost/schedule?index_id=nonexistent-index-xyz"
    assert (await client.get(url, headers=auth_headers)).status_code == 404
    assert (await client.put(url, json={"enabled": True}, headers=auth_headers)).status_code == 404


async def test_schedule_defaults_save_and_read(client, auth_headers, test_index):
    url = f"/v1/training/query-boost/schedule?index_id={test_index['id']}"
    body = (await client.get(url, headers=auth_headers)).json()
    assert (body["enabled"], body["days"], body["run_hour_utc"], body["next_run_at"]) == (False, 30, 2, None)

    res = await client.put(url, json={"enabled": True, "days": 14, "run_hour_utc": 4}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert (body["enabled"], body["days"], body["run_hour_utc"]) == (True, 14, 4)
    assert body["next_run_at"] > body["enabled_at"]   # turning on never runs immediately

    assert (await client.get(url, headers=auth_headers)).json()["days"] == 14


@pytest.mark.parametrize("payload", [
    {"enabled": True, "days": 0},
    {"enabled": True, "days": 366},
    {"enabled": True, "run_hour_utc": 24},
    {"enabled": True, "run_hour_utc": -1},
    {"days": 30},
])
async def test_schedule_validation(client, auth_headers, test_index, payload):
    url = f"/v1/training/query-boost/schedule?index_id={test_index['id']}"
    assert (await client.put(url, json=payload, headers=auth_headers)).status_code == 422


async def test_delete_model_also_turns_schedule_off(client, auth_headers, test_index):
    idx = test_index["id"]
    url = f"/v1/training/query-boost/schedule?index_id={idx}"
    await client.put(url, json={"enabled": True, "days": 10}, headers=auth_headers)

    res = await client.delete(f"/v1/training/query-boost?index_id={idx}", headers=auth_headers)
    assert res.json()["schedule_disabled"] is True

    body = (await client.get(url, headers=auth_headers)).json()
    assert body["enabled"] is False and body["days"] == 10


async def test_train_now_while_a_run_holds_the_lock_is_409(client, auth_headers, db, test_index):
    # Simulate a scheduled run in progress (from any process): hold the same
    # per-index advisory lock that trainer.train takes.
    from isp.services.domain.query_boost.coordination import _LOCK_NAMESPACE
    key = _LOCK_NAMESPACE + test_index["id"]
    await db.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", key)
    try:
        res = await client.post(f"/v1/training/query-boost?index_id={test_index['id']}", headers=auth_headers)
        assert res.status_code == 409
    finally:
        await db.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", key)

    res = await client.post(f"/v1/training/query-boost?index_id={test_index['id']}", headers=auth_headers)
    assert res.status_code == 200
