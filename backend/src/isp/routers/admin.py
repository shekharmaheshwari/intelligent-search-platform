"""
/v1/admin — operator-only admin endpoints (JWT required).

Currently: global search suggestion management and manual nightly aggregation trigger.

Never calls database.py directly — all I/O goes through suggestion_service (domain)
and typesense_client (infra).  JWT protection is enforced by AuthMiddleware via the
/v1/* catch-all rule; no auth logic lives here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from isp.services.domain import suggestion_service
from isp.services.infra import typesense_client as ts

router = APIRouter(tags=["admin"])


@router.get("/suggestions", summary="List all suggestions (global)")
async def list_suggestions_global(
    q:        str  = Query(default=""),
    boosted:  bool = Query(default=False, description="If true, return only boost>0 entries"),
    page:     int  = Query(default=1, ge=1),
    per_page: int  = Query(default=20, ge=1, le=100),
):
    """
    Return a paginated list of all suggestions across every category.

    When ``boosted=true`` only entries with ``boost > 0`` are included; useful
    for the "pinned suggestions" management view.  Sorted by boost desc, then
    popularity desc so operator-promoted entries always appear first.
    """
    result = ts.list_suggestions_by_category(None, q, page, per_page)
    hits  = result.get("hits", [])
    found = result.get("found", 0)
    suggestions = []
    for h in hits:
        # list_suggestions_by_category returns flattened document dicts.
        doc = h.get("document", h)
        boost_val = doc.get("boost", 0) or 0
        # When boosted filter is active, skip entries with no positive boost.
        if boosted and boost_val == 0:
            continue
        suggestions.append({
            "id":         doc.get("id", ""),
            "query":      doc.get("query", ""),
            "popularity": doc.get("popularity", 0),
            "boost":      boost_val,
            "type":       doc.get("type", "catalog"),
            "category":   doc.get("category"),
            "boosted":    boost_val > 0,
        })
    return {"suggestions": suggestions, "total": found, "page": page, "per_page": per_page}


@router.post("/suggestions", summary="Create or update a suggestion (global)")
async def upsert_suggestion_global(body: dict):
    """
    Upsert a manually authored suggestion into the global suggestions pool.

    ``query`` is required; ``category``, ``boost`` (default 1), and ``type``
    are optional.  The entry id is derived deterministically from the query +
    category slug so repeated POSTs with the same payload are idempotent.
    """
    import re
    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    def _slug(t: str) -> str:
        """Convert text to URL-safe slug for stable suggestion id construction."""
        s = re.sub(r"[ _]", "-", t.lower())
        s = re.sub(r"[^a-z0-9-]", "", s)
        s = re.sub(r"-{2,}", "-", s)
        return s.strip("-")

    category = body.get("category")
    boost    = int(body.get("boost", 1))
    sid      = f"manual:{_slug(query)}:{_slug(category or 'global')}"
    entry: dict = {
        "id":         sid,
        "query":      query.lower(),
        "popularity": 0,
        "boost":      boost,
        "type":       "query",
    }
    if category:
        entry["category"] = category
    ts.upsert_suggestions([entry])
    return {**entry, "boosted": boost > 0}


@router.delete("/suggestions/{suggestion_id}", summary="Delete a suggestion")
async def delete_suggestion_global(suggestion_id: str):
    """
    Hard-delete a suggestion from the ``search_suggestions`` collection.

    Returns 404 when the suggestion does not exist in Typesense.
    """
    ok = ts.delete_suggestion(suggestion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"deleted": True}


@router.post("/suggestions/rebuild", summary="Trigger nightly aggregation manually")
async def rebuild_suggestions():
    """
    Immediately run the nightly aggregation pipeline that tallies query frequency
    from the last 30 days and refreshes suggestion popularity scores.

    Intended for operator use when bootstrapping a new environment or forcing an
    early refresh after a surge in user traffic.  Returns the count of upserted entries.
    """
    count = await suggestion_service.run_nightly_aggregation()
    return {"upserted": count}
