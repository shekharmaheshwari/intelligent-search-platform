# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
/v1/analytics — search event ingestion, aggregation, and query health metrics.

POST /v1/analytics/impressions
    Batch-ingest all result impressions from one search in a single request.
    Returns 204 — fire-and-forget from the PLP.

POST /v1/analytics/click
    Log a single click event when the user opens a product card.
    Returns 204 — fire-and-forget from the PLP.

POST /v1/analytics/aggregate?index_id=<slug-or-id>
    Compute position-discounted CTR and write popularity scores to Typesense.

GET  /v1/analytics/summary?index_id=<slug-or-id>&days=30
    Query health metrics: zero-result rate, top queries, low-CTR queries.
"""
from __future__ import annotations
import time
import datetime
from fastapi import APIRouter, Query
from pydantic import BaseModel
from starlette.responses import Response

router = APIRouter(tags=["analytics"])


class ImpressionItem(BaseModel):
    doc_id: str
    rank:   int


class ImpressionsBody(BaseModel):
    index_id:   str
    session_id: str = ""
    query:      str = ""
    source:     str = "plp"
    items:      list[ImpressionItem]


class ClickBody(BaseModel):
    index_id:   str
    session_id: str = ""
    query:      str = ""
    doc_id:     str
    rank:       int = 0
    source:     str = "plp"


@router.post(
    "/analytics/impressions",
    status_code=204,
    summary="Batch-log all impressions from one search result page",
)
async def log_impressions(body: ImpressionsBody):
    """
    Accepts all result impressions from a single search in one request.

    The PLP calls this once per search with every visible doc_id and its
    0-based rank — replacing N individual /event calls with one round-trip.
    ``index_id`` accepts a slug or hex ID. Unknown indexes are dropped silently.
    """
    from isp.routers.indexes import _resolve_index
    from isp.services.domain import analytics_service

    if not body.items:
        return Response(status_code=204)

    meta = await _resolve_index(body.index_id)
    if not meta:
        return Response(status_code=204)

    events = [
        {
            "session_id": body.session_id,
            "query":      body.query,
            "doc_id":     item.doc_id,
            "rank":       item.rank,
            "clicked":    False,
            "source":     body.source,
        }
        for item in body.items
    ]
    await analytics_service.log_events_batch(meta.id, events)
    return Response(status_code=204)


@router.post(
    "/analytics/click",
    status_code=204,
    summary="Log a single product click event",
)
async def log_click(body: ClickBody):
    """
    Logs one click event when the user opens a product card.

    ``index_id`` accepts a slug or hex ID. Unknown indexes are dropped silently.
    """
    from isp.routers.indexes import _resolve_index
    from isp.services.domain import analytics_service

    meta = await _resolve_index(body.index_id)
    if not meta:
        return Response(status_code=204)

    await analytics_service.log_event(
        index_id=meta.id,
        session_id=body.session_id,
        query=body.query,
        doc_id=body.doc_id,
        rank=body.rank,
        clicked=True,
        source=body.source,
    )
    return Response(status_code=204)


@router.post(
    "/analytics/aggregate",
    summary="Aggregate click signals into Typesense popularity scores",
)
async def aggregate(
    index_id: str = Query(..., description="Index slug or hex id"),
    days:     int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
):
    """
    Reads click events for the given index over the last ``days`` days,
    computes position-discounted CTR per document, normalises to 0–1,000,000,
    and writes the result back to Typesense via partial document update.

    The popularity field is consumed by the RRF post-processor:
        final_score += min(popularity, 1_000_000) / 2_000_000   → up to +0.5

    Safe to call repeatedly — scores are overwritten, not accumulated.
    """
    from isp.routers.indexes import _resolve_index
    from isp.services.domain import analytics_service

    meta = await _resolve_index(index_id)
    if not meta:
        return {"error": "index not found", "updated": 0}

    updated = await analytics_service.apply_popularity(meta.id, meta.collection_name, days)
    return {"index_id": meta.id, "slug": meta.slug, "updated": updated}


@router.get(
    "/indexes/{index_id}/analytics",
    summary="Full analytics dashboard data for an index",
)
async def index_analytics(
    index_id: str,
    range: str = Query(default="30d", pattern="^(7d|30d|90d)$", alias="range"),
):
    """
    Returns summary stats, daily search volume, top queries, and zero-result
    queries for the given index over the requested time range.

    ``range`` accepts ``7d``, ``30d``, or ``90d``.
    """
    from isp.routers.indexes import _resolve_index
    from isp.services.infra.database import get_pool
    import builtins

    days = int(range[:-1])
    _range = builtins.range  # avoid shadowing
    meta = await _resolve_index(index_id)
    if not meta:
        from fastapi import HTTPException
        raise HTTPException(404, "Index not found")

    cutoff_ms = int((time.time() - days * 86400) * 1000)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT query, session_id, clicked, created_at
            FROM search_events
            WHERE index_id = $1 AND created_at >= $2
            ORDER BY created_at ASC
            """,
            meta.id, cutoff_ms,
        )

    if not rows:
        empty_summary = {
            "total_searches": 0, "unique_queries": 0,
            "ctr": 0.0, "zero_result_rate": 0.0, "avg_results": 0.0,
        }
        return {
            "summary":      empty_summary,
            "top_queries":  [],
            "zero_results": [],
            "volume":       [],
            "top_facets":   [],
        }

    # -- aggregations ---------------------------------------------------------
    search_clicked: dict[tuple, bool] = {}
    query_counts:   dict[str, int]    = {}
    day_counts:     dict[str, int]    = {}
    day_clicks:     dict[str, int]    = {}

    for row in rows:
        key = (row["session_id"], row["query"] or "")
        search_clicked[key] = search_clicked.get(key, False) or bool(row["clicked"])
        q = row["query"] or ""
        query_counts[q] = query_counts.get(q, 0) + 1
        day = time.strftime("%Y-%m-%d", time.gmtime(row["created_at"] / 1000))
        day_counts[day] = day_counts.get(day, 0) + 1
        if row["clicked"]:
            day_clicks[day] = day_clicks.get(day, 0) + 1

    total         = len(search_clicked)
    no_click      = sum(1 for c in search_clicked.values() if not c)
    clicked_count = total - no_click
    ctr           = round(clicked_count / total, 4) if total else 0.0
    zero_rate     = round(no_click / total, 4) if total else 0.0
    unique_q      = len([q for q in query_counts if q])

    query_has_click: dict[str, bool] = {}
    for (_, q), clicked in search_clicked.items():
        query_has_click[q] = query_has_click.get(q, False) or clicked

    top_queries   = sorted(query_counts.items(), key=lambda x: -x[1])[:20]
    zero_results  = [q for q, clicked in query_has_click.items() if not clicked and q][:20]
    zero_counts   = {q: query_counts.get(q, 0) for q in zero_results}

    # fill in missing days with 0 so chart has a continuous x-axis
    today = datetime.date.today()
    volume = []
    for i in _range(days - 1, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        volume.append({"date": d, "count": day_counts.get(d, 0), "clicks": day_clicks.get(d, 0)})

    return {
        "summary": {
            "total_searches":   total,
            "unique_queries":   unique_q,
            "ctr":              ctr,
            "zero_result_rate": zero_rate,
            "avg_results":      0.0,   # requires results_count tracking (future)
        },
        "top_queries":  [{"query": q, "count": c, "avg_results": 0.0} for q, c in top_queries],
        "zero_results": [{"query": q, "count": zero_counts[q]} for q in zero_results],
        "volume":       volume,
        "top_facets":   [],
    }


@router.get(
    "/analytics/summary",
    summary="Query health metrics for an index",
)
async def summary(
    index_id: str = Query(..., description="Index slug or hex id"),
    days:     int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
):
    """
    Returns query health metrics computed from raw ``search_events`` rows.

    Response fields:
    - ``total_searches``    — distinct (session_id, query) pairs with any impression
    - ``zero_result_rate``  — fraction of searches with no clicks (proxy for relevance gaps)
    - ``top_queries``       — up to 10 most frequent queries
    - ``low_ctr_queries``   — up to 10 queries with impressions but zero clicks
    """
    from isp.routers.indexes import _resolve_index
    from isp.services.domain import analytics_service

    meta = await _resolve_index(index_id)
    if not meta:
        return {"error": "index not found"}

    return await analytics_service.get_summary(meta.id, days)
