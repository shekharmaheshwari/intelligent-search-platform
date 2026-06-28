"""
/v1/catalog — PLP product search, facets, typeahead, and intent detection.

This router is the backend for the Product Listing Page (PLP).  It reads
from ISP-managed Typesense collections (``isp_*``) rather than a shared ``catalog``
collection.  Product type discrimination uses the ``source_type`` field, which is
set to ``"device"`` or ``"accessory"`` during rebuild via ``_infer_isp_type``.

Multi-collection search: all active ``isp_*`` collections are queried in parallel
via Typesense ``multi_search`` and results are merged with Reciprocal Rank Fusion.
"""
from __future__ import annotations
import json
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from isp.models.search import CustomerAction
from isp.services.infra import embedding_service
from isp.services.infra import typesense_client as ts
from isp.services.domain.context_service import (
    SearchContext, build_context, context_boost, fetch_preference_vector,
)
from isp.services.domain.settings_service import (
    get_customer_context_enabled,
    get_cc_vector_blending_enabled,
    get_cc_affinity_boost_enabled,
    get_cc_viewed_penalty_enabled,
)

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


def _rrf_search(
    collections: list[str],
    q: str,
    vec,
    filter_by: str,
    page: int,
    per_page: int,
    post_rrf_hook=None,
) -> dict:
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

    # Editorial boosts applied after RRF merge.
    # is_featured: large fixed bonus that overrides pure relevance order.
    # popularity: linear scale (1 000 000 pop ≈ +0.5, same magnitude as featured).
    # boost_queries_equal: +0.5 when the full query exactly matches a merchandising term.
    # boost_queries_contains: +0.3 when the query contains a merchandising term.
    q_lower = (q or "").lower().strip()
    for did, doc in all_docs.items():
        if doc.get("is_featured"):
            scores[did] = scores.get(did, 0) + 0.5
        pop = doc.get("popularity") or 0
        if pop > 0:
            scores[did] = scores.get(did, 0) + min(int(pop), 1_000_000) / 2_000_000
        boost_eq = doc.get("boost_queries_equal") or []
        if isinstance(boost_eq, list) and q_lower in [t.lower() for t in boost_eq if t]:
            scores[did] = scores.get(did, 0) + 0.5
        boost_ct = doc.get("boost_queries_contains") or []
        if isinstance(boost_ct, list) and any(t.lower() in q_lower for t in boost_ct if t):
            scores[did] = scores.get(did, 0) + 0.3

    # Context hook — applied after all editorial boosts so customer signals
    # combine with rather than override merchandising rules.
    if post_rrf_hook is not None:
        post_rrf_hook(scores, all_docs)

    ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
    start = (page - 1) * per_page
    page_ids = ranked[start:start + per_page]
    return {
        "hits":  [{"document": all_docs[d], "score": scores[d]} for d in page_ids],
        "found": len(ranked),
    }


# ── GET /v1/intent/tab ────────────────────────────────────────────────────────

@router.get("/intent/tab", summary="Detect which catalog tab a query belongs to")
async def intent_tab(q: str = Query(..., description="Search query")):
    """
    Classifies a search query into the tab whose index best matches it semantically.

    Detection strategy:
    1. Fetch all indexes from the database that have ``intent_label`` and a
       ``collection_name`` set — no hardcoded collection IDs or keywords.
    2. Embed the query with the configured embedding model.
    3. Run a k-NN vector search against each labeled collection in parallel.
    4. Group hit counts by ``intent_label``; the label with the most hits wins.
    5. Fall back to ``device`` when embedding fails or no labels are configured.
    """
    from isp.services.domain.index_service import list_indexes

    q_stripped = q.strip()
    if not q_stripped:
        return {"tab": None, "confidence": 0.0, "scores": {}}

    # Build collection → (intent_label, doc_count) map from DB — no hardcoded IDs.
    all_indexes = await list_indexes()
    col_meta: dict[str, tuple[str, int]] = {
        m.collection_name: (m.intent_label, max(m.doc_count, 1))
        for m in all_indexes
        if m.collection_name and m.intent_label
    }

    if not col_meta:
        return {"tab": None, "confidence": 0.0, "scores": {}}

    cols = list(col_meta.keys())
    global_model = embedding_service.get_current_model()
    # Identify which collections share the global embedding model
    # (embed_model="" means "use global").
    col_embed: dict[str, str] = {m.collection_name: m.embed_model for m in all_indexes if m.collection_name and m.intent_label}

    try:
        vec = await embedding_service.embed_query(q_stripped)
        vec_str = ",".join(str(v) for v in vec) if vec else None

        # Run vector search (for same-model collections) + lexical (for all collections).
        # Vector search gives semantic similarity; for collections ingested with a different
        # model we fall back to coverage ratio (found / doc_count) — a reliable discriminator
        # because small, focused collections score high coverage for on-topic queries.
        searches = []
        for col in cols:
            if vec_str and not col_embed.get(col):  # empty embed_model means global model
                searches.append({
                    "collection":   col,
                    "q":            "*",
                    "query_by":     "title",
                    "vector_query": f"vec:([{vec_str}], k:5)",
                    "per_page":     "5",
                })
            # Always include a lexical search for coverage ratio
            searches.append({
                "collection":       col,
                "q":                q_stripped,
                "query_by":         "title,content",
                "query_by_weights": "3,1",
                "num_typos":        1,
                "per_page":         "5",
            })

        resp = ts.get_client().multi_search.perform({"searches": searches}, {})
        results = resp.get("results", [])

        label_score: dict[str, float] = {}
        result_idx = 0
        for col in cols:
            label, doc_count = col_meta[col]
            uses_global = not col_embed.get(col)

            vec_res: dict = {}
            if vec_str and uses_global:
                vec_res = results[result_idx] if result_idx < len(results) else {}
                result_idx += 1
            lex_res = results[result_idx] if result_idx < len(results) else {}
            result_idx += 1

            # Vector similarity takes priority when available (same-model collections only).
            vec_hits = vec_res.get("hits", []) if not vec_res.get("error") else []
            if vec_hits:
                avg_dist = sum(h.get("vector_distance", 1.0) for h in vec_hits) / len(vec_hits)
                score = 1.0 - avg_dist / 2.0  # [0, 1], higher is better
            else:
                # Coverage ratio: fraction of collection matching the query.
                # Small, focused collections (e.g. 8/15 tablets) score high; large
                # diverse collections (e.g. 15/560 accessories) score low.
                # Multiply by 1.5 so that high coverage (>50%) beats typical vector
                # similarity scores (0.65–0.75), which cluster around the same range.
                found = lex_res.get("found", 0)
                score = min(found / doc_count * 1.5, 1.0)

            label_score[label] = max(label_score.get(label, 0.0), round(score, 6))

        if any(v > 0 for v in label_score.values()):
            best = max(label_score, key=lambda t: label_score[t])
            total = sum(label_score.values()) or 1
            confidence = round(label_score[best] / total, 4)
            return {"tab": best, "confidence": confidence, "scores": {k: round(v, 4) for k, v in label_score.items()}}
    except Exception:
        pass

    return {"tab": None, "confidence": 0.0, "scores": {}}


# ── POST /v1/catalog ──────────────────────────────────────────────────────────

class CatalogSearchRequest(BaseModel):
    session_id:    str = ""
    q:             str = ""
    type:          str = "device"
    page:          int = 1
    per_page:      int = 24
    category:      str | None = None
    manufacturer:  str | None = None
    search_mode:   str = "lexical"
    action_history: list[CustomerAction] = Field(
        default_factory=list,
        description=(
            "Recent customer actions used to personalise ranking. "
            "Viewed-item vectors are blended into the query embedding; "
            "category and manufacturer signals boost matching documents after RRF."
        ),
    )


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

    collections = _get_isp_collections()

    # Build customer context from action history — each feature can be toggled
    # independently for A/B testing via Settings → Customer Context.
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

    vec = None
    if req.search_mode in ("semantic", "hybrid") and req.q.strip():
        vec = await embedding_service.embed_query(req.q)
        if ctx and ctx.vector_seed_ids and vec and get_cc_vector_blending_enabled():
            vec = await fetch_preference_vector(ctx, collections, vec)

    result = _rrf_search(collections, req.q, vec, filter_by, req.page, req.per_page,
                         post_rrf_hook=ctx_hook)

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

    context_actions = ["hybrid_search" if vec else "lexical_search"]
    if ctx:
        if ctx.vector_seed_ids:
            context_actions.append("preference_vector_blend")
        if ctx.category_affinity or ctx.manufacturer_affinity:
            context_actions.append("affinity_boost")

    return {
        "items":                items,
        "total":                found,
        "page":                 req.page,
        "per_page":             req.per_page,
        "total_pages":          total_pages,
        "context_segment":      "all",
        "context_actions_used": context_actions,
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
