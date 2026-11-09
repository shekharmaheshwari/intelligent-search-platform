# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
query_boost.store against the real Postgres test DB (never mocked, per
conftest.py). Needs DATABASE_URL and a schema created by init_db(); skipped
otherwise so the pure unit tests in this folder still run anywhere.
"""
from __future__ import annotations
import os

import pytest

from isp.services.domain.query_boost import store
from isp.services.domain.query_boost.clustering import QueryCluster
from isp.services.domain.query_boost.scoring import LearnedBoost
from isp.services.infra import database as database_module

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Postgres)")


@pytest.fixture(autouse=True)
async def _fresh_pool_and_cache():
    """Same per-test pool reset as test_popularity_trainer.py, plus a clean cache."""
    database_module._pool = None
    store.invalidate()
    yield
    store.invalidate()
    if database_module._pool is not None:
        await database_module._pool.close()
        database_module._pool = None


def _cluster(cid: int, queries: list[str], centroid=None) -> QueryCluster:
    c = QueryCluster(cluster_id=cid)
    for q in queries:
        c.add(q, centroid)
    return c


@pytest.mark.asyncio
async def test_round_trip(test_index):
    idx = test_index["id"]
    await store.save_model(
        idx, "m1",
        [_cluster(0, ["cheap iphone", "budget iphone"], [1.0, 0.0]), _cluster(1, ["unused"])],
        [LearnedBoost(0, "iphone-se", 0.8, 20, 13)],
    )
    model = await store.load_model(idx)
    assert model.embed_model == "m1"
    # Cluster 1 learned no boost but is kept as a matching boundary.
    assert model.query_to_cluster == {"cheap iphone": 0, "budget iphone": 0, "unused": 1}
    assert model.centroids[0] == pytest.approx([1.0, 0.0])
    assert model.boosts == {0: {"iphone-se": 0.8}}


@pytest.mark.asyncio
async def test_save_replaces_previous_model(test_index):
    idx = test_index["id"]
    await store.save_model(idx, "m1", [_cluster(0, ["a"])], [LearnedBoost(0, "x", 0.5, 1, 1)])
    await store.save_model(idx, "m1", [_cluster(0, ["b"])], [LearnedBoost(0, "y", 0.4, 1, 1)])
    model = await store.load_model(idx)
    assert model.query_to_cluster == {"b": 0}
    assert model.boosts == {0: {"y": 0.4}}


@pytest.mark.asyncio
async def test_delete_turns_it_off(test_index):
    idx = test_index["id"]
    await store.save_model(idx, "m1", [_cluster(0, ["a"])], [LearnedBoost(0, "x", 0.5, 1, 1)])
    await store.delete_model(idx)
    assert await store.load_model(idx) is None


@pytest.mark.asyncio
async def test_load_is_cached_until_invalidated(test_index, db):
    idx = test_index["id"]
    await store.save_model(idx, "m1", [_cluster(0, ["a"])], [LearnedBoost(0, "x", 0.5, 1, 1)])
    assert (await store.load_model(idx)).boosts == {0: {"x": 0.5}}
    # Written behind the cache's back, as another API process would.
    await db.execute("UPDATE query_boosts SET boost = 0.9 WHERE index_id = $1", idx)
    assert (await store.load_model(idx)).boosts == {0: {"x": 0.5}}
    store.invalidate(idx)
    assert (await store.load_model(idx)).boosts == {0: {"x": 0.9}}


@pytest.mark.asyncio
async def test_summary(test_index):
    idx = test_index["id"]
    assert (await store.model_summary(idx))["trained"] is False
    await store.save_model(idx, "m1", [_cluster(0, ["a", "b"])], [LearnedBoost(0, "x", 0.5, 1, 1)])
    summary = await store.model_summary(idx)
    assert summary["n_clusters"] == 1 and summary["n_boosts"] == 1
    assert summary["clusters"][0]["queries"] == ["a", "b"]


@pytest.mark.asyncio
async def test_load_reads_one_snapshot_even_if_a_retrain_commits_mid_read(test_index, monkeypatch):
    """
    Regression: clusters and boosts were read in two separate statements, so
    a retrain committing between them paired the old mapping with the new
    boosts. A retrain is injected right after the first SELECT; the load
    must still return the old run, consistently.
    """
    idx = test_index["id"]
    await store.save_model(idx, "m1", [_cluster(0, ["old query"])], [LearnedBoost(0, "old-doc", 0.5, 1, 1)])
    store.invalidate(idx)

    real_get_pool = store.get_pool
    injected = False

    class _Conn:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        async def fetch(self, sql, *args):
            nonlocal injected
            rows = await self._conn.fetch(sql, *args)
            if "query_boost_clusters" in sql and not injected:
                injected = True
                # A concurrent retrain from another connection, committed now.
                other = await __import__("asyncpg").connect(os.environ["DATABASE_URL"])
                try:
                    async with other.transaction():
                        await other.execute("DELETE FROM query_boosts WHERE index_id = $1", idx)
                        await other.execute("DELETE FROM query_boost_clusters WHERE index_id = $1", idx)
                        await other.execute(
                            "INSERT INTO query_boost_clusters (index_id, cluster_id, queries, embed_model, trained_at) "
                            "VALUES ($1, 0, $2, 'm1', 0)", idx, ["new query"])
                        await other.execute(
                            "INSERT INTO query_boosts (index_id, cluster_id, doc_id, boost) "
                            "VALUES ($1, 0, 'new-doc', 0.9)", idx)
                finally:
                    await other.close()
            return rows

    class _Acquire:
        def __init__(self, pool):
            self._cm = pool.acquire()

        async def __aenter__(self):
            return _Conn(await self._cm.__aenter__())

        async def __aexit__(self, *exc):
            return await self._cm.__aexit__(*exc)

    class _Pool:
        def __init__(self, pool):
            self._pool = pool

        def acquire(self):
            return _Acquire(self._pool)

    async def _get_pool():
        return _Pool(await real_get_pool())

    monkeypatch.setattr(store, "get_pool", _get_pool)
    model = await store.load_model(idx)

    assert injected
    assert model.query_to_cluster == {"old query": 0}
    assert model.boosts == {0: {"old-doc": 0.5}}  # never {"new-doc": ...} under the old mapping
