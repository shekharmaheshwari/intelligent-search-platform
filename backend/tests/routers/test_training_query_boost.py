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
