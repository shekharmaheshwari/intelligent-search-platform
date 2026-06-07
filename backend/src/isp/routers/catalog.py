"""
/v1/catalog — PLP product search, facets, typeahead, and intent detection.

This router is the backend for the T-Mobile Product Listing Page (PLP).  It reads
from ISP-managed Typesense collections (``isp_*``) rather than a shared ``catalog``
collection.  Product type discrimination uses the ``source_type`` field, which is
set to ``"device"`` or ``"accessory"`` during rebuild via ``_infer_isp_type``.

Multi-collection search: all active ``isp_*`` collections are queried in parallel
via Typesense ``multi_search`` and results are merged with Reciprocal Rank Fusion.
"""
from __future__ import annotations
import json
from fastapi import APIRouter, Query
from pydantic import BaseModel
from isp.services import embedding_service
from isp.services import typesense_client as ts

router = APIRouter(tags=["catalog-api"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_isp_collections() -> list[str]:
    """Return all active ISP-managed Typesense collection names (those prefixed ``isp_``)."""
    return [c["name"] for c in ts.list_all_collections() if c["name"].startswith("isp_")]


def _parse_meta(raw_meta) -> dict:
    """Deserialise the ``metadata`` JSON string field from an ISP document."""
    if not raw_meta:
        return {}
    if isinstance(raw_meta, str):
        try:
            return json.loads(raw_meta)
        except Exception:
            return {}
    return raw_meta if isinstance(raw_meta, dict) else {}


def _doc_to_item(doc: dict, score: float = 0.0) -> dict:
    """
    Convert a raw ISP Typesense document to the PLP ``CatalogItem`` response shape.

    ISP-managed docs use ``title`` / ``source_type`` instead of the old catalog
    schema's ``display`` / ``type``.  Rich product fields (images, price, etc.)
    are read from the parsed ``metadata`` JSON blob.
    """
    meta = _parse_meta(doc.get("metadata"))
    display = str(doc.get("title") or meta.get("display") or "")
    doc_type = str(doc.get("source_type") or meta.get("type") or "device")
    is_featured = bool(meta.get("is_featured", doc.get("is_featured", False)))
    return {
        "id":          str(doc.get("id", "")),
        "text":        display,
        "type":        doc_type,
        "score":       round(float(score), 6),
        "is_featured": is_featured,
        "url":         str(doc.get("url") or meta.get("url") or ""),
        "metadata": {
            "price":            meta.get("price"),
            "in_stock":         meta.get("in_stock"),
            "manufacturer":     meta.get("manufacturer"),
            "category":         meta.get("category"),
            "color_options":    meta.get("color_options", []),
            "images":           meta.get("images", []),
            "skus":             meta.get("skus", []),
            "url_name":         meta.get("url_name"),
            "description":      meta.get("description"),
            "operating_system": meta.get("operating_system"),
        },
    }


def _rrf_search(collections: list[str], q: str, vec, filter_by: str, page: int, per_page: int) -> dict:
    """
    Hybrid RRF search across all ISP-managed collections.

    Builds one lexical and one semantic search per collection, issues them all
    via a single ``multi_search`` call, then merges every hit with Reciprocal
    Rank Fusion (lexical weight 0.7, semantic 0.3) before slicing the page.

    When ``vec`` is None, falls back to lexical-only multi-search.
    Fetches ``per_page * 3`` candidates per collection before merging to avoid
    page-boundary artifacts.
    """
    if not collections:
        return {"hits": [], "found": 0}

    candidates = per_page * 3
    searches = []
    for col in collections:
        lex: dict = {
            "collection":          col,
            "q":                   q or "*",
            "query_by":            "title,content",
            "query_by_weights":    "3,1",
            "num_typos":           2,
            "per_page":            str(candidates),
        }
        if filter_by:
            lex["filter_by"] = filter_by
        searches.append(lex)

        if vec:
            vec_str = ",".join(str(v) for v in vec)
            sem: dict = {
                "collection":   col,
                "q":            "*",
                "query_by":     "title",
                "vector_query": f"vec:([{vec_str}], k:{candidates})",
                "per_page":     str(candidates),
            }
            if filter_by:
                sem["filter_by"] = filter_by
            searches.append(sem)

    try:
        resp = ts.get_client().multi_search.perform({"searches": searches}, {})
    except Exception:
        return {"hits": [], "found": 0}

    results = resp.get("results", [])
    K = 60
    scores: dict[str, float] = {}
    all_docs: dict[str, dict] = {}

    if vec:
        # Results alternate: lex, sem, lex, sem, …
        for i in range(0, len(results), 2):
            lex_res = results[i] if i < len(results) else {}
            sem_res = results[i + 1] if i + 1 < len(results) else {}
            for rank, h in enumerate(lex_res.get("hits", [])):
                did = h["document"]["id"]
                scores[did] = scores.get(did, 0) + 0.7 / (K + rank + 1)
                all_docs[did] = h["document"]
            for rank, h in enumerate(sem_res.get("hits", [])):
                did = h["document"]["id"]
                scores[did] = scores.get(did, 0) + 0.3 / (K + rank + 1)
                all_docs[did] = h["document"]
    else:
        for res in results:
            for rank, h in enumerate(res.get("hits", [])):
                did = h["document"]["id"]
                scores[did] = scores.get(did, 0) + 0.7 / (K + rank + 1)
                all_docs[did] = h["document"]

    # Apply editorial boosts so featured/high-popularity items surface before pure
    # relevance scores. is_featured adds a large fixed bonus; popularity scales linearly
    # (100 000 popularity ≈ +0.05, comparable to a top-ranked RRF hit at ~0.011).
    for did, doc in all_docs.items():
        if doc.get("is_featured"):
            scores[did] = scores.get(did, 0) + 0.5
        pop = doc.get("popularity") or 0
        if pop > 0:
            scores[did] = scores.get(did, 0) + min(int(pop), 1_000_000) / 2_000_000

    ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
    start = (page - 1) * per_page
    page_ids = ranked[start:start + per_page]
    return {
        "hits":  [{"document": all_docs[d], "score": scores[d]} for d in page_ids],
        "found": len(ranked),
    }


# ── GET /v1/intent/tab ────────────────────────────────────────────────────────

_ACCESSORY_KEYWORDS = {
    "case", "cover", "protector", "charger", "cable", "cord", "headphone",
    "earphone", "earbud", "airpod", "speaker", "screen", "glass", "film",
    "mount", "stand", "holder", "wallet", "clip", "band", "strap", "dock",
    "hub", "adapter", "power", "battery", "pad", "car", "wireless",
}


@router.get("/intent/tab", summary="Detect which catalog tab a query belongs to")
async def intent_tab(q: str = Query(..., description="Search query")):
    """
    Classifies a search query as belonging to the **Phones** tab (``device``) or
    **Accessories** tab (``accessory``).

    Detection strategy (in order):
    1. **Keyword pre-filter** — accessory-specific terms (e.g. *case*, *charger*) → ``accessory``.
    2. **Semantic vote** — embed the query, find the 10 nearest ISP docs; majority type wins.
    3. **Default** — falls back to ``device`` (confidence 0.6).
    """
    q_stripped = q.strip()
    if not q_stripped:
        return {"tab": "device", "confidence": 1.0, "scores": {"device": 1.0, "accessory": 0.0}}

    q_words = set(q_stripped.lower().split())
    accessory_hits = len(q_words & _ACCESSORY_KEYWORDS)
    if accessory_hits > 0:
        confidence = min(1.0, 0.6 + accessory_hits * 0.15)
        return {
            "tab": "accessory",
            "confidence": round(confidence, 4),
            "scores": {"device": round(1.0 - confidence, 4), "accessory": round(confidence, 4)},
        }

    try:
        vec = await embedding_service.embed_query(q_stripped)
        if vec:
            collections = _get_isp_collections()
            vec_str = ",".join(str(v) for v in vec)
            searches = [
                {
                    "collection":   col,
                    "q":            "*",
                    "query_by":     "title",
                    "vector_query": f"vec:([{vec_str}], k:5)",
                    "per_page":     "5",
                }
                for col in collections
            ]
            if searches:
                resp = ts.get_client().multi_search.perform({"searches": searches}, {})
                type_counts: dict[str, int] = {}
                for res in resp.get("results", []):
                    for h in res.get("hits", []):
                        t = h.get("document", {}).get("source_type", "device")
                        type_counts[t] = type_counts.get(t, 0) + 1
                total = sum(type_counts.values()) or 1
                device_conf = round(type_counts.get("device", 0) / total, 4)
                acc_conf    = round(type_counts.get("accessory", 0) / total, 4)
                if acc_conf > device_conf:
                    return {"tab": "accessory", "confidence": acc_conf, "scores": {"device": device_conf, "accessory": acc_conf}}
                return {"tab": "device", "confidence": device_conf, "scores": {"device": device_conf, "accessory": acc_conf}}
    except Exception:
        pass

    return {"tab": "device", "confidence": 0.6, "scores": {"device": 0.6, "accessory": 0.4}}


# ── POST /v1/catalog ──────────────────────────────────────────────────────────

class CatalogSearchRequest(BaseModel):
    session_id:  str = ""
    q:           str = ""
    type:        str = "device"
    page:        int = 1
    per_page:    int = 24
    category:    str | None = None
    manufacturer: str | None = None
    search_mode: str = "lexical"


@router.post("/catalog", summary="Search the product catalog")
async def search_catalog(req: CatalogSearchRequest):
    """
    Full-text + semantic hybrid product search over all ISP-managed Typesense collections.
    This is the primary endpoint consumed by the PLP (Product Listing Page).

    **Search modes**
    - ``lexical``  — BM25 keyword search on ``title`` and ``content``.
    - ``semantic`` — vector nearest-neighbour via Voyage AI embeddings.
    - ``hybrid``   — Reciprocal Rank Fusion of both (lexical 0.7, semantic 0.3).

    **Filtering**
    - ``type``         — ``device`` or ``accessory``, matched against ``source_type``.
    - ``manufacturer`` — filter by ``manufacturer`` tag stored in ``metadata``.

    Results are merged across all active ISP collections before paging.
    """
    filters = [f"source_type:={req.type}"]
    filter_by = " && ".join(filters)

    vec = None
    if req.search_mode in ("semantic", "hybrid") and req.q.strip():
        vec = await embedding_service.embed_query(req.q)

    collections = _get_isp_collections()
    result = _rrf_search(collections, req.q, vec, filter_by, req.page, req.per_page)

    hits  = result.get("hits", [])
    found = result.get("found", 0)

    items = [_doc_to_item(h["document"], h.get("score", 0.0)) for h in hits]

    # Post-filter by manufacturer (stored in metadata, not a top-level Typesense field)
    if req.manufacturer:
        mfr_lower = req.manufacturer.lower()
        items = [i for i in items if (i["metadata"].get("manufacturer") or "").lower() == mfr_lower]

    # Post-filter by category
    if req.category:
        cat_lower = req.category.lower()
        items = [i for i in items if (i["metadata"].get("category") or "").lower() == cat_lower]

    total_pages = max(1, (found + req.per_page - 1) // req.per_page)

    return {
        "items":                items,
        "total":                found,
        "page":                 req.page,
        "per_page":             req.per_page,
        "total_pages":          total_pages,
        "context_segment":      "all",
        "context_actions_used": ["hybrid_search" if vec else "lexical_search"],
    }


# ── GET /v1/catalog/facets ────────────────────────────────────────────────────

@router.get("/catalog/facets", summary="Get facets for a catalog type")
async def get_facets(type: str = Query(default="device")):
    """
    Aggregate facet counts for the filter sidebar in the PLP.

    Fetches up to 250 documents of the given ``type`` from all ISP collections
    and computes manufacturer, category, and price_range facets from ``metadata``.
    """
    collections = _get_isp_collections()
    if not collections:
        return {"facets": {}}

    searches = [
        {
            "collection": col,
            "q":          "*",
            "query_by":   "title",
            "filter_by":  f"source_type:={type}",
            "per_page":   "250",
            "page":       "1",
        }
        for col in collections
    ]

    try:
        resp = ts.get_client().multi_search.perform({"searches": searches}, {})
    except Exception:
        return {"facets": {}}

    manufacturer_counts: dict[str, int] = {}
    category_counts:     dict[str, int] = {}
    price_bands = {"0-20": 0, "21-50": 0, "51-100": 0, "101-200": 0, "201-500": 0, "500+": 0}

    for res in resp.get("results", []):
        for h in res.get("hits", []):
            meta = _parse_meta(h.get("document", {}).get("metadata"))
            mfr = meta.get("manufacturer", "")
            if mfr:
                manufacturer_counts[mfr] = manufacturer_counts.get(mfr, 0) + 1
            cat = meta.get("category", "")
            if cat:
                category_counts[cat] = category_counts.get(cat, 0) + 1
            price = meta.get("price")
            if price is not None:
                p = float(price)
                if   p <= 20:  price_bands["0-20"]    += 1
                elif p <= 50:  price_bands["21-50"]   += 1
                elif p <= 100: price_bands["51-100"]  += 1
                elif p <= 200: price_bands["101-200"] += 1
                elif p <= 500: price_bands["201-500"] += 1
                else:          price_bands["500+"]    += 1

    def _to_facets(counts: dict) -> list[dict]:
        return sorted([{"value": k, "count": v} for k, v in counts.items()], key=lambda x: -x["count"])

    return {
        "facets": {
            "manufacturer": _to_facets(manufacturer_counts)[:15],
            "category":     _to_facets(category_counts)[:15],
            "price_range":  [{"value": b, "count": c} for b, c in price_bands.items() if c > 0],
        }
    }


# ── GET /v1/suggest ───────────────────────────────────────────────────────────

@router.get("/suggest", summary="Autocomplete suggestions")
async def suggest(
    q:          str = Query(..., description="Partial query (min 2 chars)"),
    session_id: str = Query(default=""),
    limit:      int = Query(default=8, ge=1, le=20),
):
    """
    Typeahead autocomplete for the PLP search bar.

    Searches ``title`` and ``content`` across all ISP collections with
    typo-tolerance, ranked by text match.  Returns at most ``limit`` unique
    suggestions with their product type.
    """
    if not q or len(q) < 2:
        return {"suggestions": [], "query": q}

    collections = _get_isp_collections()
    if not collections:
        return {"suggestions": [], "query": q}

    searches = [
        {
            "collection":       col,
            "q":                q,
            "query_by":         "title,content",
            "query_by_weights": "3,1",
            "num_typos":        1,
            "per_page":         str(limit * 2),
            "page":             "1",
            "exclude_fields":   "vec,metadata",
        }
        for col in collections
    ]

    try:
        resp = ts.get_client().multi_search.perform({"searches": searches}, {})
    except Exception:
        return {"suggestions": [], "query": q}

    seen: set[str] = set()
    suggestions = []
    for res in resp.get("results", []):
        for h in res.get("hits", []):
            doc   = h.get("document", {})
            text  = str(doc.get("title", ""))
            dtype = str(doc.get("source_type", "item"))
            if text and text not in seen:
                seen.add(text)
                suggestions.append({"text": text, "type": dtype, "score": 0.0})
            if len(suggestions) >= limit:
                break
        if len(suggestions) >= limit:
            break

    return {"suggestions": suggestions, "query": q}
