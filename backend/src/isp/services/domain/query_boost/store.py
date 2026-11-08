# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Persistence and caching for the learned query boost (Case B).

Two tables, both created in ``database.init_db()``:

  query_boost_clusters  (index_id, cluster_id) → member queries, centroid,
                        embedding model the centroid came from
  query_boosts          (index_id, cluster_id, doc_id) → boost in [0, 1]
                        plus the raw impression/click counts behind it

Why Postgres and not a field on each Typesense document (the design doc's
first idea, ``boost_queries_learned``): serving needs query → cluster
before it needs cluster → doc, and the cluster table has to live somewhere
anyway. Keeping both here lets one training run replace a whole model in a
single transaction, with no per-document Typesense writes and no schema
change to existing collections.

Serving reads through a small in-process cache (``CACHE_TTL_SECONDS``), so
the search hot path does one dict lookup, not a DB query. Saving a model
invalidates this process's entry; other API processes pick it up when
their entry expires.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from isp.services.infra.database import get_pool
from isp.services.domain.query_boost.clustering import QueryCluster
from isp.services.domain.query_boost.scoring import LearnedBoost

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0


@dataclass
class QueryBoostModel:
    index_id: str
    embed_model: str
    query_to_cluster: dict[str, int] = field(default_factory=dict)
    centroids: dict[int, list[float]] = field(default_factory=dict)
    # cluster_id → {doc_id: boost}
    boosts: dict[int, dict[str, float]] = field(default_factory=dict)
    trained_at: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.boosts


# index_id → (expires_at monotonic seconds, model or None)
_cache: dict[str, tuple[float, QueryBoostModel | None]] = {}


def invalidate(index_id: str | None = None) -> None:
    """Drop one index's cached model, or every index's when ``index_id`` is None."""
    if index_id is None:
        _cache.clear()
    else:
        _cache.pop(index_id, None)


async def save_model(
    index_id: str,
    embed_model: str,
    clusters: list[QueryCluster],
    boosts: list[LearnedBoost],
) -> None:
    """
    Replace this index's stored model in one transaction.

    **Every** cluster is stored, including ones that learned no boost. They
    act as matching boundaries: a query whose own cluster learned nothing
    must still match *that* cluster at serve time and get no lift. If the
    cluster were dropped, its exact-text mapping and centroid would go with
    it, and the query could fall through to a neighbouring cluster above
    the similarity threshold and pick up that cluster's boosts.
    """
    now_ms = int(time.time() * 1000)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM query_boosts WHERE index_id = $1", index_id)
            await conn.execute("DELETE FROM query_boost_clusters WHERE index_id = $1", index_id)
            cluster_rows = [
                (index_id, c.cluster_id, c.queries, c.centroid, embed_model, now_ms)
                for c in clusters
            ]
            if cluster_rows:
                await conn.executemany(
                    """
                    INSERT INTO query_boost_clusters
                        (index_id, cluster_id, queries, centroid, embed_model, trained_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    cluster_rows,
                )
            if boosts:
                await conn.executemany(
                    """
                    INSERT INTO query_boosts
                        (index_id, cluster_id, doc_id, boost, n_impressions, n_clicks)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    [(index_id, b.cluster_id, b.doc_id, b.boost, b.n_impressions, b.n_clicks)
                     for b in boosts],
                )
    invalidate(index_id)


async def delete_model(index_id: str) -> None:
    """Remove an index's stored model. Search then applies no learned boost (the off switch)."""
    await save_model(index_id, "", [], [])


async def _read_model(index_id: str) -> QueryBoostModel | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # One REPEATABLE READ snapshot for both reads. Under the default READ
        # COMMITTED, a retrain committing between the two SELECTs would pair
        # the old run's query → cluster mapping with the new run's boosts.
        # Cluster ids are reused across runs, so that could boost unrelated
        # documents until the cache entry expired.
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            cluster_rows = await conn.fetch(
                "SELECT cluster_id, queries, centroid, embed_model, trained_at "
                "FROM query_boost_clusters WHERE index_id = $1",
                index_id,
            )
            if not cluster_rows:
                return None
            boost_rows = await conn.fetch(
                "SELECT cluster_id, doc_id, boost FROM query_boosts WHERE index_id = $1",
                index_id,
            )
    model = QueryBoostModel(
        index_id=index_id,
        embed_model=cluster_rows[0]["embed_model"] or "",
        trained_at=max(r["trained_at"] or 0 for r in cluster_rows),
    )
    for r in cluster_rows:
        cid = r["cluster_id"]
        for q in r["queries"] or []:
            model.query_to_cluster[q] = cid
        if r["centroid"]:
            model.centroids[cid] = list(r["centroid"])
    for r in boost_rows:
        model.boosts.setdefault(r["cluster_id"], {})[r["doc_id"]] = float(r["boost"])
    return model


async def load_model(index_id: str) -> QueryBoostModel | None:
    """
    The stored model for an index, or None if none has been trained.

    Never raises: a DB error is logged and treated as "no model", so a
    broken boost table can only cost the learned boost, never the search.
    Both hits and misses are cached for ``CACHE_TTL_SECONDS``.
    """
    now = time.monotonic()
    cached = _cache.get(index_id)
    if cached and cached[0] > now:
        return cached[1]
    try:
        model = await _read_model(index_id)
    except Exception:
        log.warning("query_boost: failed to load model for index %s", index_id, exc_info=True)
        model = None
    _cache[index_id] = (now + CACHE_TTL_SECONDS, model)
    return model


async def model_summary(index_id: str) -> dict:
    """Uncached, human-readable view of the stored model, for the inspection endpoint."""
    model = await _read_model(index_id)
    if model is None:
        return {"trained": False, "clusters": []}
    clusters = []
    by_cluster: dict[int, list[str]] = {}
    for q, cid in model.query_to_cluster.items():
        by_cluster.setdefault(cid, []).append(q)
    for cid in sorted(by_cluster):
        top = sorted(model.boosts.get(cid, {}).items(), key=lambda kv: -kv[1])[:10]
        clusters.append({
            "cluster_id": cid,
            "queries":    sorted(by_cluster[cid]),
            "top_boosts": [{"doc_id": d, "boost": b} for d, b in top],
        })
    return {
        "trained":     True,
        "embed_model": model.embed_model,
        "trained_at":  model.trained_at,
        "n_clusters":  len(clusters),
        "n_boosts":    sum(len(v) for v in model.boosts.values()),
        "clusters":    clusters,
    }
