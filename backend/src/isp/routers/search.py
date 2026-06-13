"""
/v1/indexes/{id}/search — Hybrid search endpoint for ISP-managed and external indexes.

For external indexes, delegates to ``ts.search_external`` which applies the same
RRF logic but scoped to the index's filter expression (e.g. ``type:=device``).
For ISP-managed indexes, ``ts.search_with_vec`` handles the RRF merge.

Query embeddings are served from the two-tier cache (LRU → Redis) in
``embedding_service`` so repeated identical queries do not incur API calls.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from isp.models import SearchRequest
from isp.services.domain import index_service
from isp.services.infra import embedding_service
from isp.services.infra import typesense_client as ts
from isp.routers.indexes import _wrap_external_results

router = APIRouter(prefix="/indexes", tags=["search"])


@router.post("/{idx_id}/search", summary="Search an index")
async def search(idx_id: str, req: SearchRequest):
    """
    Search documents in an index using the specified mode:

    - **lexical** — BM25 full-text search via Typesense (fast, exact keyword matching).
    - **semantic** — vector nearest-neighbour search using Voyage AI `voyage-3` embeddings
      (finds conceptually similar results even without exact keyword matches).
    - **hybrid** — RRF fusion of lexical (weight 0.7) and semantic (weight 0.3) results,
      offering the best of both. This is the default mode.

    Query embeddings are served from a two-tier cache (in-memory LRU → Redis) so repeated
    queries do not incur additional Voyage AI API calls.
    """
    meta = await index_service.get_index(idx_id)
    if not meta:
        raise HTTPException(404, "Index not found")

    mode = req.mode or "hybrid"

    if meta.is_external:
        vec = None
        if mode in ("hybrid", "semantic") and req.q.strip():
            vec = await embedding_service.embed_query(req.q)
        raw = ts.search_external(
            meta.external_collection,
            req.q,
            meta.external_filter,
            meta.external_query_by,
            vec,
            req.page,
            req.per_page,
        )
        return _wrap_external_results(raw, meta)

    if mode in ("hybrid", "semantic"):
        vec = await embedding_service.embed_query(req.q)
        if vec:
            return ts.search_with_vec(idx_id, req.q, vec, req.page, req.per_page)

    return ts.search_documents(idx_id, req.q, "lexical", req.page, req.per_page)
