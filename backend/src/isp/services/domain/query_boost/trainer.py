# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Training orchestration for the learned query boost (Case B).

    search_events (query <> '')
      → normalise, drop superlative queries, keep the top MAX_QUERIES
      → embed each distinct query            (embedding_service, cached)
      → cluster by intent                    (clustering.py)
      → per-(cluster, doc) shrunk CTR lift   (scoring.py)
      → replace the stored model             (store.py)
      → log a training_runs row, case_name='B'

Exits early, keeping whatever model is already stored, when there is too
little query traffic. That mirrors Case A, which leaves ``popularity``
untouched on thin data. A full run that learns nothing does replace the
stored model, because that reflects what the data now says.

Concurrency: ``train()`` holds a per-index lock (``coordination``) for the
whole run, so manual and scheduled runs never overlap. A second caller gets
``busy=True`` right away. The publish is also conditional on the model
generation read before the run started. If the model was deleted (or
replaced) meanwhile, the result is discarded with ``superseded=True``.

Boosts are not checked against Typesense for deleted documents: a boost
for a doc that no longer exists never meets a candidate set at serve time,
so the check would cost a full collection scan for no ranking effect.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from isp.services.infra.database import get_pool
from isp.services.domain import index_service
from isp.services.domain.query_boost import clustering, coordination, scoring, store

MIN_EVENTS = 5
# Distinct queries embedded and clustered per run, most frequent first. Caps
# embedding calls; the long tail rarely has enough clicks to learn from.
MAX_QUERIES = 500
_EMBED_CONCURRENCY = 8

MODEL_TYPE = "ctr_shrinkage"

Embedder = Callable[[str], Awaitable[list[float]]]


def is_excluded_query(norm_query: str) -> bool:
    """
    Queries the learned boost must not train on.

    Superlative queries ("cheapest phone") are resolved deterministically to
    a field sort (``superlativePriceDesign.md``); their clicks follow that
    sort, so learning from them would only echo it back as a boost. The
    wildcard "*" is browse, which is Case A's data.
    """
    if not norm_query or norm_query == "*":
        return True
    from isp.services.infra.chat.concept_resolution import detect_superlative_concept
    return detect_superlative_concept(norm_query) is not None


async def _fetch_query_events(index_id: str, days: int) -> list[dict]:
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT query, doc_id, rank, clicked
            FROM search_events
            WHERE index_id = $1
              AND query IS NOT NULL AND query <> ''
              AND doc_id IS NOT NULL
              AND created_at >= $2
            """,
            index_id, cutoff_ms,
        )
    return [dict(r) for r in rows]


async def _embed_all(queries: list[str], embed: Embedder) -> dict[str, list[float]]:
    """Embed each query, a few at a time. A failed call leaves that query unembedded."""
    sem = asyncio.Semaphore(_EMBED_CONCURRENCY)

    async def one(q: str) -> tuple[str, list[float]]:
        async with sem:
            try:
                return q, (await embed(q)) or []
            except Exception:
                return q, []

    return {q: v for q, v in await asyncio.gather(*(one(q) for q in queries)) if v}


async def _log_run(index_id: str, n_events: int, n_docs: int, n_clusters: int | None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO training_runs
                (index_id, case_name, model_type, n_events, n_docs_scored, metric_name, metric_value, trained_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            index_id, "B", MODEL_TYPE, n_events, n_docs,
            "n_clusters" if n_clusters is not None else None,
            float(n_clusters) if n_clusters is not None else None,
            int(time.time() * 1000),
        )


async def train(
    index_id: str,
    days: int = 30,
    *,
    embed: Embedder | None = None,
    threshold: float = clustering.DEFAULT_THRESHOLD,
    prior_strength: float = scoring.DEFAULT_PRIOR_STRENGTH,
    scheduled: bool = False,
) -> dict:
    """
    Train and store the learned query boost for one index.

    ``scheduled=True`` (the scheduler) also requires the index's schedule to
    still be on at publish time, checked atomically with the write, so a
    DELETE or a schedule switched off mid-run wins.

    Result flags beyond the counts:
    - ``trained``: a new model was published.
    - ``busy``: another run for this index held the lock; nothing was done.
    - ``superseded``: the model was deleted or replaced during this run, or
      (when scheduled) the schedule was turned off, so the result was
      discarded.

    ``embed`` defaults to ``embedding_service.embed_query`` with the index's
    own embedding model, the same model search uses, so stored centroids are
    comparable with live query vectors. Tests pass a stub.
    """
    meta = await index_service.get_index(index_id)
    if not meta:
        raise ValueError(f"index not found: {index_id}")

    async with coordination.training_lock(index_id) as acquired:
        if not acquired:
            return {**_empty_summary(0), "busy": True}
        return await _train_locked(meta, days, embed, threshold, prior_strength, scheduled)


def _empty_summary(n_events: int) -> dict:
    return {"n_events": n_events, "n_queries": 0, "n_clusters": 0,
            "n_boosts": 0, "docs_boosted": 0, "trained": False,
            "busy": False, "superseded": False}


async def _train_locked(meta, days, embed, threshold, prior_strength, scheduled) -> dict:
    index_id = meta.id
    # Read before any events, so a delete at any later point is detected.
    generation = await store.current_generation(index_id)

    from isp.services.infra import embedding_service
    embed_model = meta.embed_model or embedding_service.get_current_model()
    if embed is None:
        async def embed(q: str) -> list[float]:
            return await embedding_service.embed_query(q, model=embed_model)

    rows = await _fetch_query_events(index_id, days)
    summary = _empty_summary(len(rows))

    rows = [{**r, "query": clustering.normalize_query(r["query"])} for r in rows]
    rows = [r for r in rows if not is_excluded_query(r["query"])]
    if len(rows) < MIN_EVENTS:
        await _log_run(index_id, summary["n_events"], 0, None)
        return summary

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["query"]] = counts.get(r["query"], 0) + 1
    kept = sorted(counts, key=lambda q: (-counts[q], q))[:MAX_QUERIES]
    counts = {q: counts[q] for q in kept}

    vectors = await _embed_all(kept, embed)
    clusters = clustering.cluster_queries(counts, vectors, threshold)
    q_to_c = {q: c.cluster_id for c in clusters for q in c.queries}

    events = [
        scoring.Event(q_to_c[r["query"]], r["doc_id"], int(r["rank"] or 0), bool(r["clicked"]))
        for r in rows if r["query"] in q_to_c
    ]
    boosts = scoring.compute_boosts(events, prior_strength=prior_strength)

    published = await store.save_model(
        index_id, embed_model if vectors else "", clusters, boosts,
        expected_generation=generation, require_schedule_enabled=scheduled,
    )
    if not published:
        await _log_run(index_id, summary["n_events"], 0, None)
        return {**summary, "n_queries": len(kept), "superseded": True}

    n_docs = len({b.doc_id for b in boosts})
    n_clusters = len({b.cluster_id for b in boosts})
    await _log_run(index_id, summary["n_events"], n_docs, n_clusters)
    return {**summary, "n_queries": len(kept), "n_clusters": n_clusters,
            "n_boosts": len(boosts), "docs_boosted": n_docs, "trained": True}
