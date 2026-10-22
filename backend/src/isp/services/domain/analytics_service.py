# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Analytics service — event ingestion, popularity aggregation, and query health metrics.

Events are written to ``search_events`` in PostgreSQL.  The aggregation job reads
those events, computes position-discounted CTR per document, normalises to a
0–1,000,000 ``popularity`` range, and writes back to Typesense via
``update_document_fields`` — no re-embedding, no rebuild required.
"""
from __future__ import annotations
import math
import time
from isp.services.infra.database import get_pool
from isp.services.infra import typesense_client as ts


async def log_event(
    *,
    index_id: str,
    session_id: str,
    query: str,
    doc_id: str,
    rank: int,
    clicked: bool,
    source: str,
) -> None:
    """Insert one search impression or click event row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO search_events
                (index_id, session_id, query, doc_id, rank, clicked, source, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            index_id, session_id, query, doc_id, rank, clicked, source,
            int(time.time() * 1000),
        )


async def log_events_batch(
    index_id: str,
    events: list[dict],
) -> None:
    """
    Insert multiple search events in a single round-trip using executemany.

    Each dict in ``events`` must have keys: session_id, query, doc_id, rank,
    clicked, source.  ``index_id`` is shared across all rows in the batch.
    """
    now_ms = int(time.time() * 1000)
    rows = [
        (
            index_id,
            e["session_id"],
            e["query"],
            e["doc_id"],
            e["rank"],
            e["clicked"],
            e["source"],
            now_ms,
        )
        for e in events
    ]
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO search_events
                (index_id, session_id, query, doc_id, rank, clicked, source, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            rows,
        )


_CLICK_WEIGHT      = 1.0   # full position-discounted weight for a click
_IMPRESSION_WEIGHT = 0.1   # impressions contribute 1/10th — seen but not clicked


async def aggregate_popularity(index_id: str, days: int = 30) -> dict[str, int]:
    """
    Compute normalised popularity scores from both impressions and clicks.

    Scoring formula:
        position_weight(rank) = 1 / log2(rank + 2)   — rank 0 = 1.0, rank 9 ≈ 0.29
        contribution(event)   = position_weight × (1.0 if clicked else 0.1)
        raw_score(doc)        = SUM(contribution) across all events for that doc
        popularity(doc)       = round(raw_score / max_raw_score × 1_000_000)

    Clicks are worth 10× a bare impression so that documents users actively open
    rank higher than documents that are merely displayed frequently.

    Returns a dict of doc_id → popularity int in [0, 1_000_000].
    Returns an empty dict when there are no events in the window.
    """
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT doc_id, rank, clicked
            FROM search_events
            WHERE index_id  = $1
              AND created_at >= $2
            """,
            index_id, cutoff_ms,
        )

    if not rows:
        return {}

    raw: dict[str, float] = {}
    for row in rows:
        position_w = 1.0 / math.log2(row["rank"] + 2)
        weight = position_w * (_CLICK_WEIGHT if row["clicked"] else _IMPRESSION_WEIGHT)
        raw[row["doc_id"]] = raw.get(row["doc_id"], 0.0) + weight

    max_score = max(raw.values())
    return {
        doc_id: round(score / max_score * 1_000_000)
        for doc_id, score in raw.items()
    }


async def apply_popularity(index_id: str, col_name: str, days: int = 30) -> int:
    """
    Run aggregation and write popularity scores back to Typesense.

    Skips documents where ``update_document_fields`` returns False (doc not found
    in Typesense, e.g. deleted since the click was recorded).

    Returns the number of documents successfully updated.
    """
    scores = await aggregate_popularity(index_id, days)
    updated = 0
    for doc_id, popularity in scores.items():
        if ts.update_document_fields(col_name, doc_id, {"popularity": popularity}):
            updated += 1
    return updated


async def get_summary(index_id: str, days: int = 30) -> dict:
    """
    Return query health metrics for an index over the last ``days`` days.

    Metrics:
    - total_searches     — distinct (session_id, query) search pairs with any impression
    - zero_result_rate   — fraction of searches where no result was ever clicked
    - top_queries        — up to 10 most frequent queries by impression count
    - low_ctr_queries    — up to 10 queries with impressions but zero clicks
    """
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT query, session_id, clicked
            FROM search_events
            WHERE index_id = $1 AND created_at >= $2
            """,
            index_id, cutoff_ms,
        )

    if not rows:
        return {
            "total_searches":  0,
            "zero_result_rate": 0.0,
            "top_queries":     [],
            "low_ctr_queries": [],
        }

    # (session_id, query) → did any event in that search have a click?
    search_clicked: dict[tuple, bool] = {}
    query_counts:   dict[str, int]   = {}
    for row in rows:
        key = (row["session_id"], row["query"] or "")
        search_clicked[key] = search_clicked.get(key, False) or bool(row["clicked"])
        q = row["query"] or ""
        query_counts[q] = query_counts.get(q, 0) + 1

    total = len(search_clicked)
    no_click = sum(1 for clicked in search_clicked.values() if not clicked)
    zero_result_rate = round(no_click / total, 4) if total else 0.0

    # Per-query: did any session ever click a result for this query?
    query_has_click: dict[str, bool] = {}
    for (_, q), clicked in search_clicked.items():
        query_has_click[q] = query_has_click.get(q, False) or clicked

    top_queries    = sorted(query_counts.items(), key=lambda x: -x[1])[:10]
    low_ctr_queries = [q for q, clicked in query_has_click.items() if not clicked][:10]

    return {
        "total_searches":   total,
        "zero_result_rate": zero_result_rate,
        "top_queries":      [{"q": q, "count": c} for q, c in top_queries],
        "low_ctr_queries":  [{"q": q} for q in low_ctr_queries],
    }


async def cleanup_old_events(days: int = 90) -> int:
    """
    Delete raw events older than ``days`` days.

    Returns the number of rows deleted.  Safe to call repeatedly — a no-op when
    there are no rows older than the cutoff.
    """
    cutoff_ms = int((time.time() - days * 86400) * 1000)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM search_events WHERE created_at < $1",
            cutoff_ms,
        )
    return int(result.split()[-1])
