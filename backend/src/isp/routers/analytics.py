"""
/v1/analytics — search event ingestion, aggregation, and query health metrics.

Three endpoints:

POST /v1/analytics/event
    Accepts one search impression or click event from the PLP or management UI.
    Returns 204 No Content — fire-and-forget from the frontend.

POST /v1/analytics/aggregate?index_id=<slug-or-id>
    Reads the last 30 days of click events for an index, computes
    position-discounted CTR, and writes the resulting popularity scores
    back to Typesense.  No re-embedding, no rebuild.

GET  /v1/analytics/summary?index_id=<slug-or-id>&days=30
    Returns query health metrics: zero-result rate, top queries, low-CTR queries.
"""
from __future__ import annotations
from fastapi import APIRouter, Query
from pydantic import BaseModel
from starlette.responses import Response

router = APIRouter(tags=["analytics"])


class AnalyticsEventBody(BaseModel):
    index_id:   str
    session_id: str  = ""
    query:      str  = ""
    doc_id:     str
    rank:       int  = 0
    clicked:    bool = False
    source:     str  = "plp"   # "plp" | "mgmt_ui"


@router.post(
    "/analytics/event",
    status_code=204,
    summary="Log a search impression or click",
)
async def log_event(event: AnalyticsEventBody):
    """
    Accepts one search event (impression or click) from any ISP consumer.

    ``index_id`` accepts either a slug (e.g. ``tablets``) or a hex ID.
    Unknown indexes are accepted silently — the event is dropped rather than
    erroring so a misconfigured client never breaks the user's search flow.
    """
    from isp.routers.indexes import _resolve_index
    from isp.services.domain import analytics_service

    meta = await _resolve_index(event.index_id)
    if not meta:
        return Response(status_code=204)

    await analytics_service.log_event(
        index_id=meta.id,
        session_id=event.session_id,
        query=event.query,
        doc_id=event.doc_id,
        rank=event.rank,
        clicked=event.clicked,
        source=event.source,
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
