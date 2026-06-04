from __future__ import annotations
import json
from fastapi import APIRouter, Query
from pydantic import BaseModel
from isp.services import embedding_service
from isp.services import typesense_client as ts

router = APIRouter(tags=["catalog-api"])

_CATALOG_COL = "catalog"
_DEVICE_URL_BASE = "https://www.t-mobile.com/cell-phones"
_ACCESSORY_URL_BASE = "https://www.t-mobile.com/accessories"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_meta(raw_meta) -> dict:
    if not raw_meta:
        return {}
    if isinstance(raw_meta, str):
        try:
            return json.loads(raw_meta)
        except Exception:
            return {}
    return raw_meta if isinstance(raw_meta, dict) else {}


def _build_url(doc: dict, meta: dict) -> str:
    url_name = meta.get("url_name", "")
    if not url_name:
        return ""
    doc_type = doc.get("type", "")
    base = _DEVICE_URL_BASE if doc_type == "device" else _ACCESSORY_URL_BASE
    return f"{base}/{url_name}"


def _doc_to_item(doc: dict, score: float = 0.0) -> dict:
    meta = _parse_meta(doc.get("metadata"))
    return {
        "id": str(doc.get("id", "")),
        "text": str(doc.get("display", "")),
        "type": str(doc.get("type", "")),
        "score": round(float(score), 6),
        "is_featured": bool(doc.get("is_featured", False)),
        "url": _build_url(doc, meta),
        "metadata": {
            "price": meta.get("price"),
            "in_stock": meta.get("in_stock"),
            "manufacturer": meta.get("manufacturer"),
            "category": meta.get("category"),
            "color_options": meta.get("color_options", []),
            "images": meta.get("images", []),
            "skus": meta.get("skus", []),
            "url_name": meta.get("url_name"),
            "description": meta.get("description"),
            "operating_system": meta.get("operating_system"),
        },
    }


def _rrf_search(col: str, q: str, vec, filter_by: str, page: int, per_page: int) -> dict:
    """Hybrid RRF search on the catalog collection."""
    base_params: dict = {
        "collection": col,
        "q": q or "*",
        "query_by": "display,aliases",
        "query_by_weights": "3,1",
        "num_typos": 2,
        "per_page": str(per_page * 3),
    }
    if filter_by:
        base_params["filter_by"] = filter_by

    if vec:
        vec_str = ",".join(str(v) for v in vec)
        sem_params = {
            "collection": col,
            "q": "*",
            "query_by": "display",
            "vector_query": f"vec:([{vec_str}], k:{per_page * 3})",
            "per_page": str(per_page * 3),
        }
        if filter_by:
            sem_params["filter_by"] = filter_by

        resp = ts.get_client().multi_search.perform(
            {"searches": [base_params, sem_params]}, {}
        )
        results = resp.get("results", [{}, {}])
        lex_hits = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(results[0].get("hits", []))}
        sem_hits = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(results[1].get("hits", []))}
        K = 60
        scores: dict[str, float] = {}
        for did, (rank, _) in lex_hits.items():
            scores[did] = scores.get(did, 0) + 0.7 / (K + rank + 1)
        for did, (rank, _) in sem_hits.items():
            scores[did] = scores.get(did, 0) + 0.3 / (K + rank + 1)
        all_docs = {**{d: doc for d, (_, doc) in lex_hits.items()},
                    **{d: doc for d, (_, doc) in sem_hits.items()}}
        ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
        start = (page - 1) * per_page
        page_ids = ranked[start:start + per_page]
        return {
            "hits": [{"document": all_docs[d], "score": scores[d]} for d in page_ids],
            "found": len(ranked),
        }

    # Lexical-only
    result = ts.get_client().collections[col].documents.search({
        **base_params,
        "per_page": per_page,
        "page": page,
        "sort_by": "_text_match:desc,popularity:desc" if q.strip() else "popularity:desc",
    })
    return {
        "hits": [{"document": h["document"], "score": h.get("text_match_info", {}).get("score", 0)} for h in result.get("hits", [])],
        "found": result.get("found", 0),
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
    Classifies a search query as belonging to the **Phones** tab (`device`) or
    **Accessories** tab (`accessory`).

    Detection strategy (in order):
    1. **Keyword pre-filter** — if the query contains accessory-specific terms
       (e.g. *case*, *charger*, *screen protector*) the tab is immediately set to `accessory`
       with high confidence.
    2. **Semantic vote** — embed the query with Voyage AI and find the 10 nearest catalog
       items; the majority product type wins.
    3. **Default** — falls back to `device` (confidence 0.6) if embedding is unavailable.

    Used by the PLP to pre-select the correct tab before the user hits Search.
    """
    q_stripped = q.strip()
    if not q_stripped:
        return {"tab": "device", "confidence": 1.0, "scores": {"device": 1.0, "accessory": 0.0}}

    # Fast keyword pre-filter: if accessory-specific terms appear → accessories
    q_words = set(q_stripped.lower().split())
    accessory_keyword_hits = len(q_words & _ACCESSORY_KEYWORDS)
    if accessory_keyword_hits > 0:
        confidence = min(1.0, 0.6 + accessory_keyword_hits * 0.15)
        device_conf = round(1.0 - confidence, 4)
        return {
            "tab": "accessory",
            "confidence": round(confidence, 4),
            "scores": {"device": device_conf, "accessory": round(confidence, 4)},
        }

    # Semantic nearest-neighbor vote: embed query, find top-N catalog items, tally type
    try:
        vec = await embedding_service.embed_query(q_stripped)
        if vec:
            vec_str = ",".join(str(v) for v in vec)
            resp = ts.get_client().collections[_CATALOG_COL].documents.search({
                "q": "*",
                "query_by": "display",
                "vector_query": f"vec:([{vec_str}], k:10)",
                "per_page": 10,
                "exclude_fields": "vec",
            })
            hits = resp.get("hits", [])
            type_counts: dict[str, int] = {}
            for h in hits:
                t = h.get("document", {}).get("type", "device")
                type_counts[t] = type_counts.get(t, 0) + 1
            total_hits = sum(type_counts.values()) or 1
            device_votes = type_counts.get("device", 0)
            accessory_votes = type_counts.get("accessory", 0)
            device_conf = round(device_votes / total_hits, 4)
            accessory_conf = round(accessory_votes / total_hits, 4)
            if device_votes >= accessory_votes:
                return {"tab": "device", "confidence": device_conf, "scores": {"device": device_conf, "accessory": accessory_conf}}
            return {"tab": "accessory", "confidence": accessory_conf, "scores": {"device": device_conf, "accessory": accessory_conf}}
    except Exception:
        pass

    # Default: devices
    return {"tab": "device", "confidence": 0.6, "scores": {"device": 0.6, "accessory": 0.4}}


# ── POST /v1/catalog ──────────────────────────────────────────────────────────

class CatalogSearchRequest(BaseModel):
    session_id: str = ""
    q: str = ""
    type: str = "device"
    page: int = 1
    per_page: int = 24
    category: str | None = None
    manufacturer: str | None = None
    search_mode: str = "lexical"


@router.post("/catalog", summary="Search the product catalog")
async def search_catalog(req: CatalogSearchRequest):
    """
    Full-text + semantic hybrid product search over the unified `catalog` Typesense collection.
    This is the primary endpoint consumed by the PLP (Product Listing Page).

    **Search modes**
    - `lexical` — BM25 keyword search on `display` and `aliases` fields.
    - `semantic` — vector nearest-neighbour via Voyage AI embeddings.
    - `hybrid` — Reciprocal Rank Fusion of both (lexical 0.7, semantic 0.3).

    **Filtering**
    - `type` — `device` (phones/tablets/watches) or `accessory`.
    - `manufacturer` — filter by brand tag (e.g. `Apple`, `Samsung`).
    - `category` — post-filter on metadata `category` field (client-side, not indexed).

    **Response** matches the `CatalogResponse` shape expected by the PLP frontend.
    """
    filters = [f"type:={req.type}"]
    if req.manufacturer:
        # Manufacturer is stored as a lowercase tag
        mfr_lower = req.manufacturer.lower()
        filters.append(f"tags:=[{mfr_lower}]")

    filter_by = " && ".join(filters)

    vec = None
    if req.search_mode in ("semantic", "hybrid") and req.q.strip():
        vec = await embedding_service.embed_query(req.q)

    result = _rrf_search(_CATALOG_COL, req.q, vec, filter_by, req.page, req.per_page)

    hits = result.get("hits", [])
    found = result.get("found", 0)

    items = [_doc_to_item(h["document"], h.get("score", 0.0)) for h in hits]

    # Client-side category filter (metadata not indexed in Typesense)
    if req.category:
        cat_lower = req.category.lower()
        items = [i for i in items if (i["metadata"].get("category") or "").lower() == cat_lower]

    total_pages = max(1, (found + req.per_page - 1) // req.per_page)

    return {
        "items": items,
        "total": found,
        "page": req.page,
        "per_page": req.per_page,
        "total_pages": total_pages,
        "context_segment": "all",
        "context_actions_used": ["hybrid_search" if vec else "lexical_search"],
    }


# ── GET /v1/catalog/facets ────────────────────────────────────────────────────

@router.get("/catalog/facets", summary="Get facets for a catalog type")
async def get_facets(type: str = Query(default="device")):
    """
    Aggregate facet counts for the filter sidebar in the PLP.

    Fetches the top 250 popularity-ranked documents of the given `type`
    (`device` or `accessory`) and computes:
    - **manufacturer** — brand counts (e.g. Apple: 12, Samsung: 10), top 15.
    - **category** — product category counts (e.g. Handset, Tablet), top 15.
    - **price_range** — bucketed price distribution: 0–20, 21–50, 51–100, 101–200, 201–500, 500+.

    All facet values are derived from parsed `metadata` JSON stored on each document.
    """
    try:
        result = ts.get_client().collections[_CATALOG_COL].documents.search({
            "q": "*",
            "query_by": "display",
            "filter_by": f"type:={type}",
            "sort_by": "popularity:desc",
            "per_page": 250,
            "page": 1,
            "include_fields": "*, metadata",
        })
    except Exception:
        return {"facets": {}}

    hits = result.get("hits", [])

    manufacturer_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    price_bands = {
        "0-20": 0, "21-50": 0, "51-100": 0,
        "101-200": 0, "201-500": 0, "500+": 0,
    }

    for h in hits:
        doc = h.get("document", {})
        meta = _parse_meta(doc.get("metadata"))

        mfr = meta.get("manufacturer", "")
        if mfr:
            manufacturer_counts[mfr] = manufacturer_counts.get(mfr, 0) + 1

        cat = meta.get("category", "")
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1

        price = meta.get("price")
        if price is not None:
            p = float(price)
            if p <= 20:      price_bands["0-20"] += 1
            elif p <= 50:    price_bands["21-50"] += 1
            elif p <= 100:   price_bands["51-100"] += 1
            elif p <= 200:   price_bands["101-200"] += 1
            elif p <= 500:   price_bands["201-500"] += 1
            else:            price_bands["500+"] += 1

    def _to_facets(counts: dict[str, int]) -> list[dict]:
        return sorted(
            [{"value": k, "count": v} for k, v in counts.items()],
            key=lambda x: -x["count"],
        )

    price_facets = [
        {"value": band, "count": count}
        for band, count in price_bands.items()
        if count > 0
    ]

    return {
        "facets": {
            "manufacturer": _to_facets(manufacturer_counts)[:15],
            "category": _to_facets(category_counts)[:15],
            "price_range": price_facets,
        }
    }


# ── GET /v1/suggest ───────────────────────────────────────────────────────────

@router.get("/suggest", summary="Autocomplete suggestions")
async def suggest(
    q: str = Query(..., description="Partial query (min 2 chars)"),
    session_id: str = Query(default="", description="Optional session ID for future personalisation"),
    limit: int = Query(default=8, ge=1, le=20, description="Max suggestions to return"),
):
    """
    Typeahead autocomplete for the PLP search bar.

    Searches `display` (weight 3) and `aliases` (weight 1) fields with typo-tolerance,
    ranked by text match score then `popularity`.  Returns at most `limit` unique
    suggestions with their product type (`device` or `accessory`) so the UI can
    show an appropriate icon next to each suggestion.

    Requires at least 2 characters; returns an empty list for shorter input.
    """
    if not q or len(q) < 2:
        return {"suggestions": [], "query": q}

    try:
        result = ts.get_client().collections[_CATALOG_COL].documents.search({
            "q": q,
            "query_by": "display,aliases",
            "query_by_weights": "3,1",
            "num_typos": 1,
            "per_page": limit * 2,
            "page": 1,
            "sort_by": "_text_match:desc,popularity:desc",
            "exclude_fields": "vec",
        })
    except Exception:
        return {"suggestions": [], "query": q}

    seen: set[str] = set()
    suggestions = []
    for h in result.get("hits", []):
        doc = h.get("document", {})
        text = str(doc.get("display", ""))
        doc_type = str(doc.get("type", "item"))
        score = float(doc.get("popularity", 0)) / 1000.0
        if text and text not in seen:
            seen.add(text)
            suggestions.append({"text": text, "type": doc_type, "score": score})
        if len(suggestions) >= limit:
            break

    return {"suggestions": suggestions, "query": q}
