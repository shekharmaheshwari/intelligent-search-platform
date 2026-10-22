# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
/v1/content — Generic public content API for any indexed ISP source.

Any source indexed in ISP is automatically accessible here via a slug derived from
its name (e.g. ``Smart Watches`` → ``smart-watches``).  This allows external consumers
to list and search ISP-indexed content without knowing internal index IDs.

The router delegates actual Typesense calls to the same functions used by the
internal search router, then unwraps the results to a simpler flat shape.
"""
from __future__ import annotations
import re
from fastapi import APIRouter, HTTPException, Query
from isp.services.domain import index_service
from isp.services.infra import embedding_service
from isp.services.infra import typesense_client as ts
from isp.routers.indexes import _wrap_external_results

router = APIRouter(prefix="/content", tags=["content-api"])


def _slug(name: str) -> str:
    """'Smart Watches' → 'smart-watches'"""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


async def _find_by_slug(slug: str):
    """Return the IndexMeta whose name slugifies to the given slug, or None if not found."""
    indexes = await index_service.list_indexes()
    for idx in indexes:
        if _slug(idx.name) == slug:
            return idx
    return None


# ── GET /v1/content ──────────────────────────────────────────────────────────

@router.get("", summary="List all available content APIs")
async def list_content_apis():
    """
    Returns every indexed source with its public API slug and endpoints.
    Any source indexed in ISP is automatically available here.
    """
    indexes = await index_service.list_indexes()
    return [
        {
            "name":        idx.name,
            "description": idx.description,
            "slug":        _slug(idx.name),
            "doc_count":   idx.doc_count,
            "endpoints": {
                "list":   f"/v1/content/{_slug(idx.name)}",
                "search": f"/v1/content/{_slug(idx.name)}/search",
            },
        }
        for idx in indexes
    ]


# ── GET /v1/content/{slug} ───────────────────────────────────────────────────

@router.get("/{slug}", summary="List documents for a source")
async def list_content(
    slug: str,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    """
    Returns paginated documents for the source matching the slug.
    Example: /v1/content/phones, /v1/content/smart-watches
    """
    idx = await _find_by_slug(slug)
    if not idx:
        raise HTTPException(404, f"No source found for slug '{slug}'. "
                                 f"Call GET /v1/content to see available slugs.")

    if idx.is_external:
        raw = ts.list_documents_external(
            idx.external_collection,
            idx.external_filter,
            idx.external_query_by,
            page, per_page,
        )
        result = _wrap_external_results(raw, idx)
    else:
        result = ts.list_documents(idx.id, page, per_page)

    return {
        "source":   idx.name,
        "slug":     slug,
        "page":     page,
        "per_page": per_page,
        "found":    result.get("found", 0),
        "hits":     [h["document"] for h in result.get("hits", [])],
    }


# ── GET /v1/content/{slug}/search ────────────────────────────────────────────

@router.get("/{slug}/search", summary="Search documents within a source")
async def search_content(
    slug: str,
    q: str = Query(..., description="Search query"),
    mode: str = Query(default="hybrid", description="hybrid | lexical | semantic"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    """
    Searches documents within the source matching the slug.
    Example: /v1/content/phones/search?q=iphone&mode=hybrid
    """
    idx = await _find_by_slug(slug)
    if not idx:
        raise HTTPException(404, f"No source found for slug '{slug}'. "
                                 f"Call GET /v1/content to see available slugs.")

    # Use this index's dedicated embedding model, or fall back to the global default.
    effective_model = idx.embed_model or embedding_service.get_current_model()

    if idx.is_external:
        vec = None
        if mode in ("hybrid", "semantic") and q.strip():
            vec = await embedding_service.embed_query(q, model=effective_model)
        raw = ts.search_external(
            idx.external_collection, q,
            idx.external_filter, idx.external_query_by,
            vec, page, per_page,
        )
        result = _wrap_external_results(raw, idx)
    else:
        if mode in ("hybrid", "semantic"):
            vec = await embedding_service.embed_query(q, model=effective_model)
            if vec:
                result = ts.search_with_vec(idx.id, q, vec, page, per_page, embed_model=effective_model)
            else:
                result = ts.search_documents(idx.id, q, "lexical", page, per_page)
        else:
            result = ts.search_documents(idx.id, q, mode, page, per_page)

    return {
        "source":   idx.name,
        "slug":     slug,
        "query":    q,
        "mode":     mode,
        "page":     page,
        "per_page": per_page,
        "found":    result.get("found", 0),
        "hits":     [h["document"] for h in result.get("hits", [])],
    }
