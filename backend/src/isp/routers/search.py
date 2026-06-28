"""
/v1/indexes/{id}/search — Hybrid search endpoint for ISP-managed and external indexes.

For external indexes, delegates to ``ts.search_external`` which applies the same
RRF logic but scoped to the index's filter expression (e.g. ``type:=device``).
For ISP-managed indexes, ``ts.search_with_vec`` handles the RRF merge.

Query embeddings are served from the two-tier cache (LRU → Redis) in
``embedding_service`` so repeated identical queries do not incur API calls.

Accepts both hex ids and human-readable slugs via ``_resolve_index`` from the
indexes router — no URL changes required.
"""
from __future__ import annotations
import json as _json
from fastapi import APIRouter, HTTPException
from isp.models import SearchRequest
from isp.services.domain import index_service
from isp.services.domain.context_service import (
    SearchContext, build_context, context_boost, fetch_preference_vector,
)
from isp.services.domain.settings_service import (
    get_customer_context_enabled,
    get_cc_vector_blending_enabled,
    get_cc_affinity_boost_enabled,
    get_cc_viewed_penalty_enabled,
)
from isp.services.infra import embedding_service
from isp.services.infra import typesense_client as ts
from isp.routers.indexes import _wrap_external_results, _resolve_index

router = APIRouter(prefix="/indexes", tags=["search"])

# Typesense's hard cap per page.
_FILTER_BATCH = 250
# For browse (empty-query) + filter/sort, paginate Typesense up to this many
# pages so the full item set is visible before filter/sort is applied.
# 4 × 250 = 1 000 items — enough for the current catalog (~560 accessories).
_MAX_BROWSE_PAGES = 4


def _slug(v: str) -> str:
    return v.lower().replace(" ", "-").replace("&", "&") if v else ""


def _parse_meta(doc: dict) -> dict:
    try:
        return _json.loads(doc.get("metadata") or "{}")
    except Exception:
        return {}


def _apply_meta_filter(hits: list, manufacturer: str | None, category: str | None) -> list:
    """Post-filter a list of Typesense hits on metadata.manufacturer / metadata.category."""
    if not manufacturer and not category:
        return hits
    out = []
    for hit in hits:
        meta = _parse_meta(hit.get("document", {}))
        if manufacturer and meta.get("manufacturer", "").lower() != manufacturer.lower():
            continue
        if category and _slug(meta.get("category", "")) != category.lower():
            continue
        out.append(hit)
    return out


async def _run_search(idx_id: str, req: SearchRequest, exclude_vec: bool = False) -> dict:
    """Shared search logic used by both /search and /search/public."""
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    mode = req.mode or "hybrid"
    effective_model = meta.embed_model or embedding_service.get_current_model()

    # Build customer context from action history.  The master switch must be on;
    # individual feature flags then gate vector blending, affinity boosts, and
    # the viewed-item penalty independently (useful for A/B testing).
    ctx: SearchContext | None = None
    ctx_hook = None
    if req.action_history and get_customer_context_enabled():
        ctx = build_context([a.model_dump() for a in req.action_history])

        _apply_affinity = get_cc_affinity_boost_enabled()
        _apply_penalty  = get_cc_viewed_penalty_enabled()

        if _apply_affinity or _apply_penalty:
            def ctx_hook(scores: dict, all_docs: dict) -> None:
                for did, doc in all_docs.items():
                    scores[did] = scores.get(did, 0.0) + context_boost(
                        doc, ctx,
                        apply_affinity=_apply_affinity,
                        apply_viewed_penalty=_apply_penalty,
                    )

    has_filter   = bool(req.manufacturer or req.category)
    has_ctx_sort = bool(ctx_hook)
    need_batch   = has_filter or has_ctx_sort

    # For browse (empty query) + filter/sort we must see the full item set
    # before filtering, because Typesense's default sort scatters matching
    # items across all pages.  For keyword searches the relevance ranking
    # concentrates matching items in the first 250, so a single batch suffices.
    is_browse       = not req.q.strip()
    need_full_scan  = need_batch and is_browse and not meta.is_external

    if need_full_scan:
        # Paginate Typesense (250/page) until all items are collected, then
        # apply hook + filter on the complete set before re-paginating.
        all_hits: list = []
        last_raw: dict = {}
        for _p in range(1, _MAX_BROWSE_PAGES + 1):
            _raw = ts.search_documents(meta.id, req.q, "lexical", _p, _FILTER_BATCH,
                                       exclude_vec=exclude_vec)
            last_raw = _raw
            page_hits = _raw.get("hits", [])
            if not page_hits:
                break
            all_hits.extend(page_hits)
            if len(all_hits) >= _raw.get("found", 0):
                break

        # Apply ctx_hook to the combined list using rank-based seed scores.
        if ctx_hook and all_hits:
            K = 60
            _scores: dict[str, float] = {}
            _all_docs: dict[str, dict] = {}
            for _rank, _hit in enumerate(all_hits):
                _doc = _hit.get("document", {})
                _did = _doc.get("id", str(_rank))
                _scores[_did] = 1.0 / (K + _rank + 1)
                _all_docs[_did] = _doc
            ctx_hook(_scores, _all_docs)
            _hit_by_id = {_h["document"]["id"]: _h for _h in all_hits if "document" in _h}
            all_hits = [
                _hit_by_id[_d]
                for _d in sorted(_all_docs.keys(), key=lambda d: _scores[d], reverse=True)
                if _d in _hit_by_id
            ]

        if has_filter:
            all_hits = _apply_meta_filter(all_hits, req.manufacturer, req.category)

        total = len(all_hits)
        start = (req.page - 1) * req.per_page
        return {
            **last_raw,
            "hits":  all_hits[start : start + req.per_page],
            "found": total,
            "page":  req.page,
        }

    # ── Keyword search (or external index) path ───────────────────────────────
    # Relevance ranking keeps matching items near the top, so a single 250-item
    # batch is an adequate candidate pool for both filter and personalisation.
    fetch_page     = 1             if need_batch else req.page
    fetch_per_page = _FILTER_BATCH if need_batch else req.per_page

    if meta.is_external:
        vec = None
        if mode in ("hybrid", "semantic") and req.q.strip():
            vec = await embedding_service.embed_query(req.q, model=effective_model)
            if ctx and ctx.vector_seed_ids and vec and get_cc_vector_blending_enabled():
                vec = await fetch_preference_vector(ctx, [meta.external_collection], vec)
        raw = ts.search_external(
            meta.external_collection,
            req.q,
            meta.external_filter,
            meta.external_query_by,
            vec,
            fetch_page,
            fetch_per_page,
            exclude_vec=exclude_vec,
            post_rrf_hook=ctx_hook,
        )
        raw = _wrap_external_results(raw, meta)
    elif mode in ("hybrid", "semantic"):
        vec = await embedding_service.embed_query(req.q, model=effective_model)
        if vec:
            if ctx and ctx.vector_seed_ids and get_cc_vector_blending_enabled():
                vec = await fetch_preference_vector(ctx, [ts.collection_name(meta.id)], vec)
            raw = ts.search_with_vec(
                meta.id, req.q, vec, fetch_page, fetch_per_page,
                version_boost=meta.version_boost,
                exclude_vec=exclude_vec,
                post_rrf_hook=ctx_hook,
            )
        else:
            raw = ts.search_documents(meta.id, req.q, "lexical", fetch_page, fetch_per_page,
                                      exclude_vec=exclude_vec, post_rrf_hook=ctx_hook)
    else:
        raw = ts.search_documents(meta.id, req.q, "lexical", fetch_page, fetch_per_page,
                                  exclude_vec=exclude_vec, post_rrf_hook=ctx_hook)

    if not need_batch:
        return raw

    hits  = raw.get("hits", [])
    total = raw.get("found", len(hits))
    if has_filter:
        hits  = _apply_meta_filter(hits, req.manufacturer, req.category)
        total = len(hits)

    start = (req.page - 1) * req.per_page
    return {
        **raw,
        "hits":  hits[start : start + req.per_page],
        "found": total,
        "page":  req.page,
    }


@router.post("/{idx_id}/search", summary="Search an index")
async def search(idx_id: str, req: SearchRequest):
    """
    Search documents in an index using the specified mode:

    - **lexical** — BM25 full-text search via Typesense (fast, exact keyword matching).
    - **semantic** — vector nearest-neighbour search using Voyage AI embeddings.
    - **hybrid** — RRF fusion of lexical (weight 0.7) and semantic (weight 0.3) results.

    Returns full documents including the ``vec`` embedding field.
    Use ``/search/public`` when the caller does not need the embedding vector.
    """
    return await _run_search(idx_id, req, exclude_vec=False)


@router.post("/{idx_id}/search/public", summary="Search an index — no embeddings",
             tags=["public"])
async def search_public(idx_id: str, req: SearchRequest):
    """
    Identical to ``/search`` but strips the ``vec`` embedding field from every
    result document before returning.

    Use this endpoint from external applications and integrations to avoid
    transferring 1 024-float arrays (~4 KB per document) that are only needed
    internally for semantic ranking.  The ranking itself is unaffected — vectors
    are used during candidate retrieval inside ISP and discarded before the
    response is serialised.
    """
    return await _run_search(idx_id, req, exclude_vec=True)
