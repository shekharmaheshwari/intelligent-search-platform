from __future__ import annotations
import typesense
from isp.config import settings

_VEC_DIM = 1024
_client: typesense.Client | None = None


def get_client() -> typesense.Client:
    global _client
    if _client is None:
        _client = typesense.Client({
            "nodes": [{"host": settings.typesense_host, "port": settings.typesense_port, "protocol": "http"}],
            "api_key": settings.typesense_api_key,
            "connection_timeout_seconds": 10,
        })
    return _client


def collection_name(index_id: str) -> str:
    return f"isp_{index_id}"


def create_collection(index_id: str) -> None:
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
            {"name": "vec",         "type": "float[]", "num_dim": _VEC_DIM, "optional": True},
        ],
    })


def drop_collection(index_id: str) -> None:
    try:
        get_client().collections[collection_name(index_id)].delete()
    except Exception:
        pass


def upsert_documents(index_id: str, docs: list[dict]) -> int:
    if not docs:
        return 0
    name = collection_name(index_id)
    client = get_client()
    results = client.collections[name].documents.import_(docs, {"action": "upsert"})
    ok = sum(1 for r in results if r.get("success"))
    return ok


def count_documents(index_id: str) -> int:
    try:
        col = get_client().collections[collection_name(index_id)].retrieve()
        return col.get("num_documents", 0)
    except Exception:
        return 0


def search_documents(index_id: str, q: str, mode: str, page: int, per_page: int) -> dict:
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
    try:
        params: dict = {"q": "*", "query_by": "display", "per_page": 1}
        if filter_by:
            params["filter_by"] = filter_by
        return get_client().collections[col].documents.search(params).get("found", 0)
    except Exception:
        return 0
