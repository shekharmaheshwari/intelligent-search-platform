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
from fastapi import APIRouter, HTTPException
from isp.models import SearchRequest
from isp.services.domain import index_service
from isp.services.infra import embedding_service
from isp.services.infra import typesense_client as ts
from isp.routers.indexes import _wrap_external_results, _resolve_index

router = APIRouter(prefix="/indexes", tags=["search"])


async def _run_search(idx_id: str, req: SearchRequest, exclude_vec: bool = False) -> dict:
    """Shared search logic used by both /search and /search/public."""
    meta = await _resolve_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    mode = req.mode or "hybrid"
    effective_model = meta.embed_model or embedding_service.get_current_model()

    if meta.is_external:
        vec = None
        if mode in ("hybrid", "semantic") and req.q.strip():
            vec = await embedding_service.embed_query(req.q, model=effective_model)
        raw = ts.search_external(
            meta.external_collection,
            req.q,
            meta.external_filter,
            meta.external_query_by,
            vec,
            req.page,
            req.per_page,
            exclude_vec=exclude_vec,
        )
        return _wrap_external_results(raw, meta)

    if mode in ("hybrid", "semantic"):
        vec = await embedding_service.embed_query(req.q, model=effective_model)
        if vec:
            return ts.search_with_vec(
                # Use meta.id (hex) — Typesense collection is keyed on hex id, not slug.
                meta.id, req.q, vec, req.page, req.per_page,
                version_boost=meta.version_boost,
                exclude_vec=exclude_vec,
            )

    # Lexical fallback also uses hex id.
    return ts.search_documents(meta.id, req.q, "lexical", req.page, req.per_page,
                               exclude_vec=exclude_vec)


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
