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
