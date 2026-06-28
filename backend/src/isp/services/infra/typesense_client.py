"""
Thin wrapper around the Typesense Python SDK.

All Typesense I/O goes through this module.  The client is a lazy singleton
so only one HTTP connection pool is created per process.

ISP-managed collections are named ``isp_{index_id}``.
External collections (e.g. ``catalog``) are pre-existing and written to
via the write-back path during rebuild.
"""
from __future__ import annotations
import typesense
from isp.config import settings

# Vector dimension for newly created ISP collections.
# Updated at runtime by settings_service.set_embed_model() when the operator
# changes the embedding model.  Existing collections retain the dimension they
# were created with; only new collections (or collections recreated after a
# full rebuild) pick up the new value.
_VEC_DIM = 1024
_client: typesense.Client | None = None


def set_vec_dim(dim: int) -> None:
    """
    Update the vector dimension used when provisioning new ISP collections.

    Called by ``settings_service.set_embed_model()`` whenever the operator
    switches to a model with a different output dimension.  The change is
    immediately effective for any ``create_collection`` calls made afterward.
    """
    global _VEC_DIM
    _VEC_DIM = dim


def get_vec_dim() -> int:
    """Return the currently configured vector dimension for new ISP collections."""
    return _VEC_DIM


def get_client() -> typesense.Client:
    """Return (and lazily create) the shared Typesense client."""
    global _client
    if _client is None:
        _client = typesense.Client({
            "nodes": [{"host": settings.typesense_host, "port": settings.typesense_port, "protocol": "http"}],
            "api_key": settings.typesense_api_key,
            "connection_timeout_seconds": 10,
        })
    return _client


def collection_name(index_id: str) -> str:
    """Derive the Typesense collection name for an ISP-managed index."""
    return f"isp_{index_id}"


def create_collection(index_id: str, vec_dim: int | None = None) -> None:
    """
    Provision a new ISP-managed Typesense collection.

    Idempotent — does nothing if the collection already exists.
    The ``vec`` field is optional so documents without embeddings can still
    be stored (e.g. when VOYAGE_API_KEY is not set).

    ``vec_dim`` allows the caller to specify the vector dimension for a
    per-index embedding model; falls back to the global ``_VEC_DIM`` when omitted.
    """
    dim = vec_dim if vec_dim is not None else _VEC_DIM
    name = collection_name(index_id)
    client = get_client()
    try:
        client.collections[name].retrieve()
        return  # already exists
    except Exception:
        pass
    client.collections.create({
        "name": name,
        "fields": [
            {"name": "title",       "type": "string"},
            {"name": "content",     "type": "string"},
            {"name": "url",         "type": "string", "optional": True},
            {"name": "source_type", "type": "string", "facet": True},
            {"name": "indexed_at",  "type": "int64"},
            # metadata stores a JSON string so rich catalog data (images, price,
            # manufacturer, etc.) survives into ISP-managed collections intact.
            {"name": "metadata",    "type": "string", "optional": True, "index": False},
            # Filterable catalog signals — top-level so Typesense can apply
            # filter_by natively without scanning the metadata JSON string.
            {"name": "category",     "type": "string", "optional": True, "facet": True},
            {"name": "manufacturer", "type": "string", "optional": True, "facet": True},
            # Editorial signals — top-level so Typesense can sort and filter on them.
            {"name": "popularity",            "type": "int32",    "optional": True, "sort": True},
            {"name": "is_featured",           "type": "bool",     "optional": True},
            {"name": "version_score",         "type": "float",    "optional": True},
            {"name": "boost_queries_equal",   "type": "string[]", "optional": True},
            {"name": "boost_queries_contains","type": "string[]", "optional": True},
            {"name": "vec",                   "type": "float[]",  "num_dim": dim, "optional": True},
            # Wildcard — accepts any extra top-level string field promoted via
            # SitemapConnectorConfig.meta_fields without requiring schema changes.
            {"name": ".*", "type": "auto"},
        ],
    })


def drop_collection(index_id: str) -> None:
    """Delete an ISP-managed collection. Silently ignores missing collections."""
    try:
        get_client().collections[collection_name(index_id)].delete()
    except Exception:
        pass


def migrate_isp_fields() -> None:
    """
    Idempotent schema migration: add new fields to every existing ``isp_*``
    collection that does not yet have them.

    Called once at startup so collections created before a schema change are
    updated without requiring a full rebuild.  Errors are swallowed — a missing
    field means the feature is simply inactive for that collection until rebuilt.
    """
    _new_fields = [
        {"name": "version_score",          "type": "float",    "optional": True},
        {"name": "boost_queries_equal",    "type": "string[]", "optional": True},
        {"name": "boost_queries_contains", "type": "string[]", "optional": True},
        {"name": "category",               "type": "string",   "optional": True, "facet": True},
        {"name": "manufacturer",           "type": "string",   "optional": True, "facet": True},
    ]
    try:
        client = get_client()
        for col in client.collections.retrieve():
            name = col["name"]
            # Migrate ISP-managed collections and the shared catalog collection.
            if not name.startswith("isp_") and name != "catalog":
                continue
            existing = {f["name"] for f in col.get("fields", [])}
            to_add = [f for f in _new_fields if f["name"] not in existing]
            if to_add:
                client.collections[name].update({"fields": to_add})
    except Exception:
        pass


def upsert_documents(index_id: str, docs: list[dict]) -> int:
    """Upsert documents into an ISP-managed collection. Returns number of successful upserts."""
    if not docs:
        return 0
    import logging
    log = logging.getLogger(__name__)
    name = collection_name(index_id)
    client = get_client()
    results = client.collections[name].documents.import_(docs, {"action": "upsert"})
    ok = 0
    for r in results:
        if r.get("success"):
            ok += 1
        else:
            log.error("Typesense upsert failure: %s", r)
    if ok == 0 and results:
        log.error("All %d upserts failed. First doc sample: %s",
                  len(results), {k: v for k, v in list(docs[0].items())[:6] if k != "vec"})
    return ok


def count_documents(index_id: str) -> int:
    """Return the document count for an ISP-managed collection (0 on error)."""
    try:
        col = get_client().collections[collection_name(index_id)].retrieve()
        return col.get("num_documents", 0)
    except Exception:
        return 0


def _apply_lexical_hook(raw: dict, hook) -> dict:
    """
    Apply a post-retrieval scoring hook to a flat Typesense hit list and re-sort.

    Builds rank-based scores (1 / (K + rank + 1), K=60 — same constant as RRF)
    so the hook's affinity boost values are in the correct relative magnitude.
    The original hit objects are preserved; only their order changes.
    """
    hits = raw.get("hits", [])
    if not hits:
        return raw
    K = 60
    scores: dict[str, float] = {}
    all_docs: dict[str, dict] = {}
    for rank, hit in enumerate(hits):
        doc = hit.get("document", {})
        did = doc.get("id", str(rank))
        scores[did] = 1.0 / (K + rank + 1)
        all_docs[did] = doc
    hook(scores, all_docs)
    hit_by_id = {hit["document"]["id"]: hit for hit in hits if "document" in hit}
    ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
    return {**raw, "hits": [hit_by_id[d] for d in ranked if d in hit_by_id]}


def search_documents(index_id: str, q: str, mode: str, page: int, per_page: int,
                     exclude_vec: bool = False, post_rrf_hook=None,
                     filter_by: str | None = None) -> dict:
    """
    Single-pass lexical search on an ISP-managed collection.

    Hybrid mode falls back to lexical here; the caller (``search_with_vec``) handles
    the actual RRF merge when a query vector is available.

    ``exclude_vec`` tells Typesense not to return the ``vec`` field at all so the
    large embedding array is never transferred — useful for public-facing endpoints.

    ``post_rrf_hook`` is an optional callable(scores, all_docs) applied after
    retrieval to reorder results by customer affinity — the same hook used by
    ``search_with_vec``.  This enables personalisation on empty-query browse where
    no query vector is available for blending.
    """
    name = collection_name(index_id)
    client = get_client()
    extra: dict = {"exclude_fields": "vec"} if exclude_vec else {}

    if mode == "semantic":
        fb_params: dict = {"filter_by": filter_by} if filter_by else {}
        raw = client.collections[name].documents.search({
            "q": "*", "query_by": "title",
            "vector_query": f"vec:([],  k:{per_page})",
            "page": page, "per_page": per_page, **extra, **fb_params,
        })
        return _apply_lexical_hook(raw, post_rrf_hook) if post_rrf_hook else raw

    fb_params: dict = {"filter_by": filter_by} if filter_by else {}
    base_params: dict = {
        "q": q, "query_by": "title,content",
        "query_by_weights": "2,1",
        "num_typos": 2,
        "page": page, "per_page": per_page, **extra, **fb_params,
    }

    def _search_with_sort(sort_by: str) -> dict:
        try:
            raw = client.collections[name].documents.search({**base_params, "sort_by": sort_by})
        except Exception:
            raw = client.collections[name].documents.search({**base_params, "sort_by": "_text_match:desc"})
        return _apply_lexical_hook(raw, post_rrf_hook) if post_rrf_hook else raw

    if mode == "lexical":
        return _search_with_sort("is_featured:desc,_text_match:desc")

    # hybrid — default
    return _search_with_sort("is_featured:desc,_text_match:desc")


def search_with_vec(
    index_id: str,
    q: str,
    vec: list[float],
    page: int,
    per_page: int,
    version_boost: bool = False,
    exclude_vec: bool = False,
    post_rrf_hook=None,
    filter_by: str | None = None,
) -> dict:
    """
    Hybrid search via Typesense multi_search + Reciprocal Rank Fusion.

    Fetches ``per_page * 3`` candidates from both lexical and semantic passes,
    then merges with RRF weights (lexical 0.7, semantic 0.3) before slicing the
    requested page.  Fetching 3× more candidates before slicing avoids the
    page-boundary problem where RRF reordering would otherwise miss relevant docs.
    """
    name = collection_name(index_id)
    vec_str = ",".join(str(v) for v in vec)
    client = get_client()
    extra: dict = {"exclude_fields": "vec"} if exclude_vec else {}
    fb: dict = {"filter_by": filter_by} if filter_by else {}
    # Typesense hard cap is 250 per page; 3× over-fetch improves RRF recall
    # but must be clamped so we never exceed the limit.
    fetch_n = min(per_page * 3, 250)
    resp = client.multi_search.perform({"searches": [
        {
            "collection": name,
            "q": q, "query_by": "title,content",
            "query_by_weights": "2,1",
            "per_page": str(fetch_n), **extra, **fb,
        },
        {
            "collection": name,
            "q": "*", "query_by": "title",
            "vector_query": f"vec:([{vec_str}], k:{fetch_n})",
            "per_page": str(fetch_n), **extra, **fb,
        },
    ]}, {})
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

    # Editorial boosts — mirror the same logic in catalog.py _rrf_search.
    # is_featured: large fixed bonus that overrides pure relevance order.
    # popularity: linear scale (1 000 000 pop ≈ +0.5, same magnitude as featured).
    # boost_queries_equal: +0.5 when the full query exactly matches a merchandising term.
    # boost_queries_contains: +0.3 when the query contains a merchandising term.
    q_lower = q.lower().strip()
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

    # Context hook — caller may pass a callable(scores, all_docs) to apply
    # customer-action-history boosts after all editorial signals are applied.
    # This runs on the full candidate set before page slicing so items near
    # the page boundary can be promoted by personalization signals.
    if post_rrf_hook is not None:
        post_rrf_hook(scores, all_docs)

    ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
    start = (page - 1) * per_page
    page_ids = ranked[start:start + per_page]
    return {"hits": [{"document": all_docs[d], "score": scores[d]} for d in page_ids],
            "found": len(ranked),
            "page": page}


def get_facets(index_id: str, facet_fields: list[str]) -> dict:
    """
    Return facet value counts for an ISP-managed collection.

    Uses per_page=0 so Typesense computes counts without returning documents.
    Returns {field: [{value, count}, ...]} sorted by count descending.
    """
    name = collection_name(index_id)
    try:
        result = get_client().collections[name].documents.search({
            "q": "*",
            "query_by": "title",
            "facet_by": ",".join(facet_fields),
            "per_page": 0,
            "max_facet_values": 100,
        })
    except Exception:
        return {}
    facets: dict = {}
    for fc in result.get("facet_counts", []):
        field = fc.get("field_name")
        if field:
            facets[field] = [
                {"value": c["value"], "count": c["count"]}
                for c in fc.get("counts", [])
            ]
    return facets


def list_documents(index_id: str, page: int, per_page: int,
                   exclude_vec: bool = False) -> dict:
    """Paginated document listing for an ISP-managed collection, sorted by most recently indexed."""
    fields = "id, *, metadata" if exclude_vec else "id, *, vec, metadata"
    return get_client().collections[collection_name(index_id)].documents.search({
        "q": "*", "query_by": "title",
        "sort_by": "indexed_at:desc",
        "page": page, "per_page": per_page,
        "include_fields": fields,
    })


# ── External collection support ───────────────────────────────────────────────

def list_all_collections() -> list[dict]:
    """Return all Typesense collections with name + num_documents."""
    try:
        return get_client().collections.retrieve()
    except Exception:
        return []


def list_documents_external(
    col: str,
    filter_by: str,
    query_by: str,
    page: int,
    per_page: int,
    exclude_vec: bool = False,
) -> dict:
    """Paginated document listing from an external (non-ISP) Typesense collection."""
    fields = "id, *, metadata" if exclude_vec else "id, *, vec, metadata"
    params: dict = {
        "q": "*",
        "query_by": query_by or "display",
        "sort_by": "popularity:desc",
        "page": page,
        "per_page": per_page,
        "include_fields": fields,
    }
    if filter_by:
        params["filter_by"] = filter_by
    return get_client().collections[col].documents.search(params)


def search_external(
    col: str,
    q: str,
    filter_by: str,
    query_by: str,
    vec: list[float] | None,
    page: int,
    per_page: int,
    exclude_vec: bool = False,
    post_rrf_hook=None,
) -> dict:
    """
    Search an external Typesense collection with optional hybrid RRF.

    If ``vec`` is provided the function issues a ``multi_search`` (lexical + semantic)
    and merges via RRF.  Without a vector it falls back to a single lexical search.
    ``filter_by`` is applied to both passes so external source isolation is preserved.
    """
    qb = query_by or "display"
    fields = "id, *, metadata" if exclude_vec else "id, *, vec, metadata"
    params: dict = {
        "q": q or "*",
        "query_by": qb,
        "num_typos": 2,
        "page": page,
        "per_page": per_page,
        "include_fields": fields,
    }
    if filter_by:
        params["filter_by"] = filter_by
    if vec:
        vec_str = ",".join(str(v) for v in vec)
        fetch_n = min(per_page * 3, 250)
        lex_params = {**params, "per_page": str(fetch_n)}
        sem_params = {
            "collection": col,
            "q": "*",
            "query_by": qb,
            "vector_query": f"vec:([{vec_str}], k:{fetch_n})",
            "per_page": str(fetch_n),
            "include_fields": fields,
        }
        if filter_by:
            sem_params["filter_by"] = filter_by
        resp = get_client().multi_search.perform({"searches": [
            {"collection": col, **lex_params},
            sem_params,
        ]}, {})
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
        if post_rrf_hook is not None:
            post_rrf_hook(scores, all_docs)
        ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
        start = (page - 1) * per_page
        page_docs = [all_docs[d] for d in ranked[start:start + per_page]]
        return {"hits": [{"document": d, "score": scores[d]} for d in page_docs],
                "found": len(ranked), "page": page}
    return get_client().collections[col].documents.search(params)


def update_document_fields(col_name: str, doc_id: str, fields: dict) -> bool:
    """
    Partially update specific fields on a document without touching the rest.

    Uses Typesense ``action=update`` import which only overwrites the supplied keys.
    Returns True if the update was acknowledged successfully.
    """
    try:
        results = get_client().collections[col_name].documents.import_(
            [{**fields, "id": doc_id}],
            {"action": "update"},
        )
        return bool(results and results[0].get("success"))
    except Exception:
        return False


def retrieve_document(col_name: str, doc_id: str) -> dict | None:
    """Fetch a single document by id from a named collection. Returns None if not found."""
    try:
        return get_client().collections[col_name].documents[doc_id].retrieve()
    except Exception:
        return None


def delete_from_collection(col_name: str, filter_by: str) -> int:
    """Delete documents matching filter_by from any named collection."""
    try:
        params: dict = {}
        if filter_by:
            params["filter_by"] = filter_by
        result = get_client().collections[col_name].documents.delete(params)
        return result.get("num_deleted", 0)
    except Exception:
        return 0


def upsert_to_collection(col_name: str, docs: list[dict]) -> int:
    """Upsert documents into any named Typesense collection."""
    if not docs:
        return 0
    results = get_client().collections[col_name].documents.import_(docs, {"action": "upsert"})
    return sum(1 for r in results if r.get("success"))


def count_external(col: str, filter_by: str) -> int:
    """Return the number of documents in an external collection matching the filter (0 on error)."""
    try:
        params: dict = {"q": "*", "query_by": "display", "per_page": 1}
        if filter_by:
            params["filter_by"] = filter_by
        return get_client().collections[col].documents.search(params).get("found", 0)
    except Exception:
        return 0
