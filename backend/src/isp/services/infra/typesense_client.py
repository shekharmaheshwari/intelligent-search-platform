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


def create_collection(index_id: str) -> None:
    """
    Provision a new ISP-managed Typesense collection.

    Idempotent — does nothing if the collection already exists.
    The ``vec`` field is optional so documents without embeddings can still
    be stored (e.g. when VOYAGE_API_KEY is not set).
    """
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
            # Editorial signals — top-level so Typesense can sort and filter on them.
            {"name": "popularity",  "type": "int32",   "optional": True, "sort": True},
            {"name": "is_featured", "type": "bool",    "optional": True},
            {"name": "vec",         "type": "float[]", "num_dim": _VEC_DIM, "optional": True},
        ],
    })


def drop_collection(index_id: str) -> None:
    """Delete an ISP-managed collection. Silently ignores missing collections."""
    try:
        get_client().collections[collection_name(index_id)].delete()
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


def search_documents(index_id: str, q: str, mode: str, page: int, per_page: int) -> dict:
    """
    Single-pass lexical search on an ISP-managed collection.

    Hybrid mode falls back to lexical here; the caller (``search_with_vec``) handles
    the actual RRF merge when a query vector is available.
    """
    name = collection_name(index_id)
    client = get_client()

    if mode == "semantic":
        return client.collections[name].documents.search({
            "q": "*", "query_by": "title",
            "vector_query": f"vec:([],  k:{per_page})",   # placeholder; caller injects vec
            "page": page, "per_page": per_page,
        })

    if mode == "lexical":
        return client.collections[name].documents.search({
            "q": q, "query_by": "title,content",
            "query_by_weights": "2,1",
            "num_typos": 2,
            "sort_by": "_text_match:desc",
            "page": page, "per_page": per_page,
        })

    # hybrid — default
    return client.collections[name].documents.search({
        "q": q, "query_by": "title,content",
        "query_by_weights": "2,1",
        "num_typos": 2,
        "sort_by": "_text_match:desc",
        "page": page, "per_page": per_page,
    })


def search_with_vec(index_id: str, q: str, vec: list[float], page: int, per_page: int) -> dict:
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
    resp = client.multi_search.perform({"searches": [
        {
            "collection": name,
            "q": q, "query_by": "title,content",
            "query_by_weights": "2,1",
            "per_page": str(per_page * 3),
        },
        {
            "collection": name,
            "q": "*", "query_by": "title",
            "vector_query": f"vec:([{vec_str}], k:{per_page * 3})",
            "per_page": str(per_page * 3),
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
    ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
    start = (page - 1) * per_page
    page_docs = [all_docs[d] for d in ranked[start:start + per_page]]
    return {"hits": [{"document": d} for d in page_docs],
            "found": len(ranked),
            "page": page}


def list_documents(index_id: str, page: int, per_page: int) -> dict:
    """Paginated document listing for an ISP-managed collection, sorted by most recently indexed."""
    return get_client().collections[collection_name(index_id)].documents.search({
        "q": "*", "query_by": "title",
        "sort_by": "indexed_at:desc",
        "page": page, "per_page": per_page,
        "include_fields": "id, *, vec, metadata",
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
) -> dict:
    """Paginated document listing from an external (non-ISP) Typesense collection."""
    params: dict = {
        "q": "*",
        "query_by": query_by or "display",
        "sort_by": "popularity:desc",
        "page": page,
        "per_page": per_page,
        "include_fields": "id, *, vec, metadata",
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
) -> dict:
    """
    Search an external Typesense collection with optional hybrid RRF.

    If ``vec`` is provided the function issues a ``multi_search`` (lexical + semantic)
    and merges via RRF.  Without a vector it falls back to a single lexical search.
    ``filter_by`` is applied to both passes so external source isolation is preserved.
    """
    qb = query_by or "display"
    params: dict = {
        "q": q or "*",
        "query_by": qb,
        "num_typos": 2,
        "page": page,
        "per_page": per_page,
        "include_fields": "id, *, vec, metadata",
    }
    if filter_by:
        params["filter_by"] = filter_by
    if vec:
        vec_str = ",".join(str(v) for v in vec)
        lex_params = {**params, "per_page": str(per_page * 3)}
        sem_params = {
            "collection": col,
            "q": "*",
            "query_by": qb,
            "vector_query": f"vec:([{vec_str}], k:{per_page * 3})",
            "per_page": str(per_page * 3),
            "include_fields": "id, *, vec, metadata",
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
        ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
        start = (page - 1) * per_page
        page_docs = [all_docs[d] for d in ranked[start:start + per_page]]
        return {"hits": [{"document": d} for d in page_docs], "found": len(ranked), "page": page}
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
