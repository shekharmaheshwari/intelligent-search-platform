# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

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
from isp.services.domain import search_relevance

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
    import logging
    log = logging.getLogger(__name__)
    _new_fields = [
        {"name": "version_score",          "type": "float",    "optional": True},
        {"name": "boost_queries_equal",    "type": "string[]", "optional": True},
        {"name": "boost_queries_contains", "type": "string[]", "optional": True},
    ]
    try:
        client = get_client()
        collections = client.collections.retrieve()
        log.info("migrate_isp_fields: checking %d collections", len(collections))
        for col in collections:
            name = col["name"]
            # Migrate ISP-managed collections and the shared catalog collection.
            if not name.startswith("isp_") and name != "catalog":
                continue
            existing = {f["name"] for f in col.get("fields", [])}
            to_add = [f for f in _new_fields if f["name"] not in existing]
            if to_add:
                client.collections[name].update({"fields": to_add})
                log.info("migrate_isp_fields: added %s to collection=%s",
                          [f["name"] for f in to_add], name)
    except Exception:
        # Swallowed intentionally — a missing field means the feature is simply
        # inactive for that collection until rebuilt. Logged so a startup-time
        # failure (e.g. Typesense unreachable) is still visible somewhere,
        # since it otherwise leaves every isp_* collection silently un-migrated.
        log.error("migrate_isp_fields: migration failed, one or more collections "
                   "may be left un-migrated", exc_info=True)


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


# ── Built-in embedding models ────────────────────────────────────────────────
# Typesense ships ONNX embedding models (``ts/<name>``) that it runs in-process,
# so they need no API key or external server.  ISP uses them as one more
# embedding provider rather than as auto-embedding fields on isp_* collections:
# every consumer of raw vectors (hybrid search, customer-context blending, chat
# category vectors, the Redis cache) keeps working unchanged.
#
# Each model gets one scratch collection whose only job is to run it.  Texts are
# upserted under fixed ids ``0..n-1`` with ``return_doc=true`` so the import
# response carries the generated vector; the next call overwrites the same ids,
# so the collection never grows past one batch.  The ``_isp_embedder_`` prefix
# keeps it out of both ``isp_*`` scans and external-collection discovery.
EMBEDDER_COLLECTION_PREFIX = "_isp_embedder_"
_embedder_ready: set[str] = set()


def _embedder_collection_name(model_name: str) -> str:
    """``ts/all-MiniLM-L12-v2`` → ``_isp_embedder_ts_all_minilm_l12_v2``."""
    slug = "".join(c if c.isalnum() else "_" for c in model_name.lower())
    return EMBEDDER_COLLECTION_PREFIX + slug


def ensure_embedder_collection(model_name: str) -> str:
    """
    Create the scratch collection for ``model_name`` if needed; return its name.

    Creating it is what makes Typesense download (first time only, cached in its
    data dir) and load the model, which can take well over the shared client's
    10 s timeout on a slow link — so this one call uses a long-timeout client.
    """
    import typesense.exceptions
    name = _embedder_collection_name(model_name)
    if name in _embedder_ready:
        return name
    slow_client = typesense.Client({
        "nodes": [{"host": settings.typesense_host, "port": settings.typesense_port, "protocol": "http"}],
        "api_key": settings.typesense_api_key,
        "connection_timeout_seconds": 300,
    })
    try:
        slow_client.collections.create({
            "name": name,
            "fields": [
                {"name": "text", "type": "string"},
                {"name": "vec", "type": "float[]",
                 "embed": {"from": ["text"], "model_config": {"model_name": model_name}}},
            ],
        })
    except typesense.exceptions.ObjectAlreadyExists:
        pass
    _embedder_ready.add(name)
    return name


def embed_with_builtin_model(model_name: str, texts: list[str]) -> list[list[float]]:
    """
    Embed ``texts`` with a Typesense built-in model (e.g. ``ts/all-MiniLM-L12-v2``).

    Returns one L2-normalised vector per text, or ``[]`` for a text Typesense
    could not embed (callers already treat empty vectors as "no embedding").
    Normalising keeps cosine scores identical while matching the unit-length
    vectors the other providers return.  Raises if Typesense is unreachable.
    """
    import logging
    log = logging.getLogger(__name__)
    if not texts:
        return []
    name = ensure_embedder_collection(model_name)
    docs = [{"id": str(i), "text": t} for i, t in enumerate(texts)]
    results = get_client().collections[name].documents.import_(
        docs, {"action": "upsert", "return_doc": "true"},
    )
    vecs: list[list[float]] = []
    for r in results:
        raw = (r.get("document") or {}).get("vec") if r.get("success") else None
        if not raw:
            log.error("Typesense built-in embed failure (%s): %s", model_name, r.get("error"))
            vecs.append([])
            continue
        norm = sum(x * x for x in raw) ** 0.5 or 1.0
        vecs.append([x / norm for x in raw])
    return vecs


# ── Editorial ranking signals ────────────────────────────────────────────────
# All four post-retrieval ranking levers live in apply_editorial_boosts() so the
# lexical, hybrid (search_with_vec) and cross-catalog (catalog._rrf_search) paths
# stay in lock-step. See docs/scoreBoost.md and docs/trainedRescorerDesign.md.
_POP_CAP            = 1_000_000   # popularity is clamped here before any scaling
_POP_BROWSE_DIVISOR = 2_000_000   # empty-query browse: additive, up to +0.5
_POP_QUERY_COEFF   = 0.25         # non-empty query: relevance *= (1 + coeff * pop_norm)


def _is_browse(q: str) -> bool:
    """A blank query or the Typesense ``*`` wildcard means "browse", not search."""
    qs = (q or "").strip()
    return not qs or qs == "*"


def apply_editorial_boosts(scores: dict, all_docs: dict, q: str, *, is_browse: bool) -> None:
    """
    Apply the four editorial ranking signals in place on ``scores``.

    Popularity's role depends on query intent:

    * **Empty-query browse** (``is_browse``): popularity is an additive bonus of
      up to +0.5 (``popularity / 2_000_000``) — the dominant ordering signal,
      since there is no query relevance to respect.
    * **Non-empty query**: popularity is a *bounded multiplicative* adjustment
      only — ``relevance *= (1 + _POP_QUERY_COEFF * pop_norm)`` where ``pop_norm``
      is the doc's popularity relative to the most-popular candidate in the
      result set. With the default coefficient (see ``_POP_QUERY_COEFF``) a
      perfectly popular doc gets a small capped lift, so it can reorder near-ties
      but never overtake a clearly more relevant result. An additive term on the
      RRF scale (max ≈ 0.016) cannot give that guarantee — see
      docs/trainedRescorerDesign.md.

    ``is_featured`` and ``boost_queries_equal``/``boost_queries_contains`` are
    explicit merchandising levers: they stay additive and may outrank pure
    relevance by design. Pinned hits are handled separately by the caller and
    always win.
    """
    q_lower = (q or "").lower().strip()

    pops = {did: min(int(doc.get("popularity") or 0), _POP_CAP)
            for did, doc in all_docs.items()}
    max_pop = max(pops.values(), default=0)

    for did, doc in all_docs.items():
        base = scores.get(did, 0.0)
        pop  = pops[did]

        # popularity — a learned signal; must not override query relevance
        if pop > 0:
            if is_browse:
                base += pop / _POP_BROWSE_DIVISOR
            elif max_pop > 0:
                base *= 1.0 + _POP_QUERY_COEFF * (pop / max_pop)

        # merchandising overrides — additive, intentionally allowed to win
        if doc.get("is_featured"):
            base += 0.5
        boost_eq = doc.get("boost_queries_equal") or []
        if q_lower and isinstance(boost_eq, list) and q_lower in [t.lower() for t in boost_eq if t]:
            base += 0.5
        boost_ct = doc.get("boost_queries_contains") or []
        if q_lower and isinstance(boost_ct, list) and any(t.lower() in q_lower for t in boost_ct if t):
            base += 0.3

        scores[did] = base


def _rerank_flat(raw: dict, q: str, is_browse: bool, hook=None, *,
                 editorial: bool = True, pinned_hits: list[str] | None = None) -> dict:
    """
    Re-rank a flat Typesense hit list by editorial boosts (+ an optional
    affinity hook), so the lexical path applies the same signals as RRF.

    Builds rank-based scores (1 / (K + rank + 1), K=60 — same constant as RRF)
    so boost magnitudes are in the right relative range. The original hit
    objects are preserved; only their order changes.

    ``editorial=False`` skips apply_editorial_boosts — used on the browse path,
    where Typesense has already sorted the whole collection by
    ``is_featured,popularity`` natively, so only the affinity hook needs to run.

    ``pinned_hits`` (in order) are forced back to the top after editorial boosts
    and the affinity hook are applied — mirrors ``search_with_vec`` so an
    editorial signal or affinity boost can never displace an operator-pinned
    result. Only pins
    already present in this page's hits are re-asserted; the caller's own
    ``pinned_hits`` Typesense param is what puts them there. (``search_with_vec``
    additionally fetches a pinned doc missing from the candidate set; the flat
    path relies on Typesense positioning instead.)
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
    if editorial:
        apply_editorial_boosts(scores, all_docs, q, is_browse=is_browse)
    if hook is not None:
        hook(scores, all_docs)
    if pinned_hits:
        pin_base = max(scores.values(), default=0.0) + 10.0
        n = len(pinned_hits)
        for pos, pid in enumerate(pinned_hits):
            if pid in scores:
                scores[pid] = pin_base + (n - pos)
    hit_by_id = {hit["document"]["id"]: hit for hit in hits if "document" in hit}
    ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
    # Stamp text_match from the adjusted score so PLP clients that sort by
    # hit.text_match preserve the backend's ranking rather than reverting to the
    # original Typesense text-match order.
    return {**raw, "hits": [
        {**hit_by_id[d], "text_match": int(scores[d] * 100_000)}
        for d in ranked if d in hit_by_id
    ]}


def _qb_and_weights(qb: str) -> tuple[str, str]:
    """Build the query_by_weights string matching a comma-separated query_by field list."""
    fields = qb.split(",")
    weights = ",".join("3" if f == "aliases" else "2" if f == "title" else "1" for f in fields)
    return qb, weights


def _drop_field(qb: str, field: str) -> str:
    return ",".join(f for f in qb.split(",") if f != field)


def search_documents(index_id: str, q: str, mode: str, page: int, per_page: int,
                     exclude_vec: bool = False, post_rrf_hook=None,
                     filter_by: str | None = None,
                     pinned_hits: list[str] | None = None,
                     query_by: str | None = None,
                     facet_fields: list[str] | None = None) -> dict:
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

    _pin_param: dict = (
        {"pinned_hits": ",".join(f"{d}:{i + 1}" for i, d in enumerate(pinned_hits))}
        if pinned_hits else {}
    )

    if mode == "semantic":
        fb_params: dict = {"filter_by": filter_by} if filter_by else {}
        raw = client.collections[name].documents.search({
            "q": "*", "query_by": "title",
            "vector_query": f"vec:([],  k:{per_page})",
            "page": page, "per_page": per_page, **extra, **fb_params, **_pin_param,
        })
        return _rerank_flat(raw, q, _is_browse(q), post_rrf_hook, pinned_hits=pinned_hits)

    fb_params: dict = {"filter_by": filter_by} if filter_by else {}
    facet_params: dict = {"facet_by": ",".join(facet_fields), "max_facet_values": 100} if facet_fields else {}
    _qb, _qb_weights = _qb_and_weights(query_by or "title,content,aliases")
    base_params: dict = {
        "q": q, "query_by": _qb,
        "query_by_weights": _qb_weights,
        "num_typos": 2,
        "page": page, "per_page": per_page, **extra, **fb_params, **_pin_param, **facet_params,
    }

    browse = _is_browse(q)

    def _search_with_sort(sort_by: str, *, editorial: bool) -> dict:
        params = base_params
        try:
            raw = client.collections[name].documents.search({**params, "sort_by": sort_by})
        except Exception as e:
            # Collections created before `aliases` existed in the schema
            # (not yet rebuilt since it was added) 404 on any query_by that
            # references it — retry once without it rather than repeating
            # an identical failing request.
            if "aliases" in params["query_by"].split(",") and "aliases" in str(e):
                qb, weights = _qb_and_weights(_drop_field(params["query_by"], "aliases"))
                params = {**params, "query_by": qb, "query_by_weights": weights}
            raw = client.collections[name].documents.search({**params, "sort_by": "_text_match:desc"})
        result = _rerank_flat(raw, q, browse, post_rrf_hook, editorial=editorial, pinned_hits=pinned_hits)
        # Promote facet_counts to a normalized dict if Typesense returned them
        if facet_fields and "facet_counts" in result:
            result = {**result, "facet_counts": _parse_facet_counts(result["facet_counts"])}
        return result

    if browse:
        # Empty-query browse: let Typesense order the whole collection by the
        # editorial signals so pagination is correct across every page. The
        # Python pass only runs the affinity hook (editorial=False) — popularity
        # is already baked into the sort, re-adding it would double-count.
        return _search_with_sort("is_featured:desc,popularity:desc,_text_match:desc", editorial=False)

    # Non-empty query: retrieve by pure text relevance, then _rerank_flat applies
    # the query-aware editorial rule (bounded multiplicative popularity, additive
    # merchandising) identically to the RRF path.
    return _search_with_sort("_text_match:desc", editorial=True)


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
    pinned_hits: list[str] | None = None,
    query_by: str | None = None,
    facet_fields: list[str] | None = None,
    embed_model: str | None = None,
) -> dict:
    """
    Hybrid search via Typesense multi_search + Reciprocal Rank Fusion.

    Fetches ``per_page * 3`` candidates from both lexical and semantic passes,
    then merges with RRF weights (lexical 0.7, semantic 0.3) before slicing the
    requested page.  Fetching 3× more candidates before slicing avoids the
    page-boundary problem where RRF reordering would otherwise miss relevant docs.

    ``embed_model`` should be the model that produced ``vec`` (pass the
    caller's already-resolved ``meta.embed_model or
    embedding_service.get_current_model()``) — it only affects which semantic
    relevance floor gets applied, see search_relevance.py.
    """
    name = collection_name(index_id)
    vec_str = ",".join(str(v) for v in vec)
    client = get_client()
    extra: dict = {"exclude_fields": "vec"} if exclude_vec else {}
    fb: dict = {"filter_by": filter_by} if filter_by else {}
    facet_params: dict = {"facet_by": ",".join(facet_fields), "max_facet_values": 100} if facet_fields else {}
    # Typesense hard cap is 250 per page; 3× over-fetch improves RRF recall
    # but must be clamped so we never exceed the limit.
    fetch_n = min(per_page * 3, 250)
    _qb, _qb_weights = _qb_and_weights(query_by or "title,content,aliases")

    def _run_multi_search(qb: str, weights: str) -> dict:
        return client.multi_search.perform({"searches": [
            {
                "collection": name,
                "q": q, "query_by": qb,
                "query_by_weights": weights,
                "per_page": str(fetch_n), **extra, **fb, **facet_params,
            },
            {
                "collection": name,
                "q": "*", "query_by": "title",
                "vector_query": f"vec:([{vec_str}], k:{fetch_n})",
                "per_page": str(fetch_n), **extra, **fb,
            },
        ]}, {})

    resp = _run_multi_search(_qb, _qb_weights)
    results = resp.get("results", [{}, {}])
    lex_result = results[0]
    # multi_search doesn't raise on a per-sub-search failure — a failed
    # lexical pass just comes back as {"code": ..., "error": ...} with no
    # "hits" key, which would otherwise silently degrade this into a
    # semantic-only search (zero lexical/BM25 contribution to RRF) for any
    # collection created before `aliases` existed in the schema. Retry once
    # without it rather than letting that fail silently.
    if "code" in lex_result and "aliases" in str(lex_result.get("error", "")) and "aliases" in _qb.split(","):
        qb, weights = _qb_and_weights(_drop_field(_qb, "aliases"))
        resp = _run_multi_search(qb, weights)
        results = resp.get("results", [{}, {}])
        lex_result = results[0]
    # Relevance floors — see search_relevance.py. Without these, a query with
    # zero genuine matches (a typo-mashed string, an unrelated product name)
    # still fills the result list: the vector leg always returns its
    # k-nearest neighbours regardless of how weak the match actually is, and
    # Typesense's typo-tolerant lexical matching can "hit" on a single
    # incidental word overlap (e.g. a bare number matching a price).
    raw_lex_hits = search_relevance.filter_lexical_hits(lex_result.get("hits", []), q)
    raw_sem_hits = search_relevance.filter_semantic_hits(results[1].get("hits", []), embed_model)
    lex_hits = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(raw_lex_hits)}
    sem_hits = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(raw_sem_hits)}
    # Carry facet counts from the lexical pass (facet_by was only applied there)
    _facet_counts = _parse_facet_counts(lex_result.get("facet_counts", []))
    K = 60
    scores: dict[str, float] = {}
    for did, (rank, _) in lex_hits.items():
        scores[did] = scores.get(did, 0) + 0.7 / (K + rank + 1)
    for did, (rank, _) in sem_hits.items():
        scores[did] = scores.get(did, 0) + 0.3 / (K + rank + 1)
    all_docs = {**{d: doc for d, (_, doc) in lex_hits.items()},
                **{d: doc for d, (_, doc) in sem_hits.items()}}

    # Editorial boosts — shared with the lexical path and catalog._rrf_search.
    apply_editorial_boosts(scores, all_docs, q, is_browse=_is_browse(q))

    # Context hook — caller may pass a callable(scores, all_docs) to apply
    # customer-action-history boosts after all editorial signals are applied.
    # This runs on the full candidate set before page slicing so items near
    # the page boundary can be promoted by personalization signals.
    if post_rrf_hook is not None:
        post_rrf_hook(scores, all_docs)

    # Pinned hits: force specific docs to the top regardless of relevance.
    # Fetch any pinned doc not already in the candidate set (e.g. a doc that is
    # genuinely unrelated to the query and therefore absent from RRF candidates).
    if pinned_hits:
        for _pid in pinned_hits:
            if _pid not in all_docs:
                try:
                    _fetched = client.collections[name].documents[_pid].retrieve()
                    if _fetched:
                        all_docs[_pid] = _fetched
                        scores[_pid] = 0.0
                except Exception:
                    pass
        _pin_base = max(scores.values(), default=0) + 10.0
        for _pos, _pid in enumerate(pinned_hits):
            if _pid in all_docs:
                scores[_pid] = _pin_base + (len(pinned_hits) - _pos)

    ranked = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)
    start = (page - 1) * per_page
    page_ids = ranked[start:start + per_page]
    result: dict = {"hits": [{"document": all_docs[d], "score": scores[d],
                               "text_match": int(scores[d] * 100_000)} for d in page_ids],
                    "found": len(ranked),
                    "page": page}
    if _facet_counts:
        result["facet_counts"] = _facet_counts
    return result


def _parse_facet_counts(raw_counts: list) -> dict:
    """Convert Typesense facet_counts list into {field: [{value, count}]} dict."""
    result: dict = {}
    for fc in raw_counts:
        field = fc.get("field_name")
        if field:
            result[field] = [{"value": c["value"], "count": c["count"]} for c in fc.get("counts", [])]
    return result


def get_all_fields(index_id: str) -> list[dict]:
    """Return all fields from the collection schema with their current properties."""
    import logging
    log = logging.getLogger(__name__)
    _SYSTEM = {"id", "vec", "indexed_at"}
    name = collection_name(index_id)
    try:
        schema = get_client().collections[name].retrieve()
    except Exception:
        # Callers (e.g. update_index_field) treat an empty list the same as
        # "field not in schema", which then surfaces as a misleading 404
        # instead of the real retrieval failure. Log it so that distinction
        # is recoverable from the server logs.
        log.error("get_all_fields: schema retrieve failed for collection=%s", name, exc_info=True)
        return []
    fields = []
    for f in schema.get("fields", []):
        fname = f.get("name", "")
        if fname.startswith("."):
            continue
        ftype = f.get("type", "string")
        fields.append({
            "name":        fname,
            "type":        ftype,
            "facet":       bool(f.get("facet", False)),
            "multi_value": ftype.endswith("[]"),
            "optional":    bool(f.get("optional", False)),
            "system":      fname in _SYSTEM,
        })
    return sorted(fields, key=lambda f: (f["system"], f["name"]))


def update_field_properties(
    index_id: str,
    field_name: str,
    *,
    facet: bool,
    multi_value: bool,
    current_type: str,
) -> None:
    """
    Change facet or multi-value on an existing field via drop + re-add.

    Typesense cannot mutate these properties in-place — the only supported path
    is to drop the field and re-declare it.  Existing document data is preserved
    because Typesense re-indexes it from stored values automatically.

    These two calls are not atomic: if the re-add fails after the drop has
    already succeeded, the field is left missing from the schema until someone
    re-adds it manually. Logged loudly here since that failure mode otherwise
    surfaces later as an unrelated-looking 500 on search.
    """
    import logging
    log = logging.getLogger(__name__)
    name = collection_name(index_id)
    client = get_client()
    # Determine the target type: honour multi_value only for string fields.
    base_type = current_type.rstrip("[]")
    if base_type == "string":
        target_type = "string[]" if multi_value else "string"
    else:
        target_type = current_type  # don't change numeric/bool types

    log.info(
        "Field property update starting: collection=%s field=%s current_type=%s "
        "target_type=%s facet=%s multi_value=%s",
        name, field_name, current_type, target_type, facet, multi_value,
    )
    client.collections[name].update({"fields": [{"name": field_name, "drop": True}]})
    log.info("Field dropped: collection=%s field=%s — re-adding now", name, field_name)
    try:
        client.collections[name].update({"fields": [{
            "name":     field_name,
            "type":     target_type,
            "optional": True,
            "facet":    facet,
        }]})
    except Exception:
        log.error(
            "Field re-add FAILED after drop — field '%s' is now MISSING from "
            "collection '%s' schema and all searches against it will fail until "
            "it is manually re-added (target_type=%s, facet=%s).",
            field_name, name, target_type, facet, exc_info=True,
        )
        raise
    log.info("Field property update complete: collection=%s field=%s", name, field_name)


def list_synonyms(index_id: str) -> list[dict]:
    """Return all synonym sets defined for a collection."""
    import logging
    log = logging.getLogger(__name__)
    name = collection_name(index_id)
    try:
        result = get_client().collections[name].synonyms.retrieve()
        return result.get("synonyms", [])
    except Exception:
        log.error("list_synonyms failed: collection=%s", name, exc_info=True)
        return []


def upsert_synonym(index_id: str, synonym_id: str, synonyms: list[str], root: str | None = None) -> dict:
    """
    Create or update a synonym set.

    ``root`` unset means multi-way — every listed word is mutually interchangeable
    (e.g. wire = cable = cord). ``root`` set means one-way — searching the root
    also matches the synonyms, but not vice versa (e.g. root "tv" -> synonyms
    ["television", "telly"]).
    """
    name = collection_name(index_id)
    body: dict = {"synonyms": synonyms}
    if root:
        body["root"] = root
    return get_client().collections[name].synonyms.upsert(synonym_id, body)


def delete_synonym(index_id: str, synonym_id: str) -> bool:
    """Delete a synonym set. Returns False if it didn't exist or the delete failed."""
    import logging
    log = logging.getLogger(__name__)
    name = collection_name(index_id)
    try:
        get_client().collections[name].synonyms[synonym_id].delete()
        return True
    except Exception:
        log.error("delete_synonym failed: collection=%s synonym_id=%s", name, synonym_id, exc_info=True)
        return False


def get_facet_fields(index_id: str) -> list[str]:
    """Return names of all fields marked facet=True in the collection schema."""
    name = collection_name(index_id)
    try:
        schema = get_client().collections[name].retrieve()
        return [f["name"] for f in schema.get("fields", []) if f.get("facet")]
    except Exception:
        return []


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


def get_field_occurrence_ratio(index_id: str, filter_by: str, facet_field: str) -> tuple[int, float]:
    """
    Among documents matching `filter_by`, what fraction have `facet_field`
    populated -- e.g. "of this category's products, how many actually name a
    compatible device?" Returns (matched_doc_count, ratio) with ratio in
    [0, 1]; (0, 0.0) if the filter matches nothing or the request fails.

    Counts each matching document once, by fetching up to 250 of them
    (Typesense's per-page cap) with only `facet_field` included and checking
    presence directly, rather than summing facet_counts[].counts[].count --
    that sum counts (document, value) pairs, not documents, and badly
    OVERcounts a field where populated documents hold several values each
    (20 of 100 products each listing 5 compatible devices sums to 100,
    reading as 100% coverage via that method when the true, per-document
    figure is 20%). Typesense has no native "field is non-empty" filter to
    push this count entirely server-side (confirmed: compatible_devices:!=[]
    and :!=null both silently match every document, filter ignored) -- for
    a category with more than 250 documents this is a sample over the first
    page rather than an exact count, an accepted trade-off for staying one
    cheap request instead of paginating the whole category.
    """
    name = collection_name(index_id)
    try:
        result = get_client().collections[name].documents.search({
            "q": "*",
            "filter_by": filter_by,
            "per_page": 250,
            "include_fields": f"id,{facet_field}",
        })
    except Exception:
        return 0, 0.0
    found = result.get("found", 0)
    hits = result.get("hits", [])
    if not found or not hits:
        return found, 0.0
    populated = sum(1 for h in hits if h.get("document", {}).get(facet_field))
    return found, populated / len(hits)


def get_boost_rule_fields(index_id: str) -> list[str]:
    """
    Return a filtered list of field names from the Typesense collection schema
    suitable for use in boost rule criteria.

    Strips internal/system fields that have no merchandising meaning.
    """
    _SKIP = {
        "id", "vec", "indexed_at", "metadata", "content", "url",
        "is_featured", "popularity", "version_score", "in_stock", "price",
        "boost_queries_equal", "boost_queries_contains",
        "images", "skus", "color_options", "aliases", "tags",
    }
    name = collection_name(index_id)
    try:
        schema = get_client().collections[name].retrieve()
    except Exception:
        return []
    fields = []
    for f in schema.get("fields", []):
        fname = f.get("name", "")
        ftype = f.get("type", "")
        if fname in _SKIP or fname.startswith(".") or ftype.startswith("float"):
            continue
        fields.append(fname)
    return sorted(fields)


_SEARCH_TERM_FIELD = "__search_term__"


def _check_criterion(c: dict, doc: dict, doc_meta: dict, query: str) -> bool:
    """Return True when a single criterion matches the document or query."""
    import re as _re
    field    = c.get("field", "")
    operator = c.get("operator", "equals")
    value    = c.get("value", "")

    if field == _SEARCH_TERM_FIELD:
        raw = query
    else:
        raw = doc.get(field)
        if raw is None:
            raw = doc_meta.get(field)
        if raw is None:
            return False

    doc_val = str(raw).lower()
    val     = value.lower()
    if operator == "equals":
        return doc_val == val
    elif operator == "contains":
        return val in doc_val
    elif operator == "regex":
        try:
            return bool(_re.search(value, str(raw), _re.IGNORECASE))
        except _re.error:
            return False
    return False


def _parse_meta(doc: dict) -> dict:
    import json as _json
    raw = doc.get("metadata")
    if isinstance(raw, str):
        try:
            return _json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


def apply_boost_rules(scores: dict, all_docs: dict, rules: list[dict], query: str = "") -> None:
    """
    Apply operator-defined boost rules to the scored candidate set.

    Each rule has two sections:
    - ``trigger``: ALL criteria must match (AND). May include ``__search_term__``.
    - ``targets``: ANY criterion must match (OR). Selects which documents get boosted.

    Rules whose trigger contains ``__search_term__`` only fire when there is an
    active search query; rules without it only fire during browse (empty query).

    If ``targets`` is empty, all documents passing the trigger receive the boost.
    Boost values are clamped to +1.00 regardless of the stored value.
    """
    is_search = bool(query.strip())
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        boost   = min(float(rule.get("boost", 0.0)), 1.0)
        trigger = rule.get("trigger", [])
        targets = rule.get("targets", [])
        if not trigger or boost == 0.0:
            continue
        has_query_criterion = any(c.get("field") == _SEARCH_TERM_FIELD for c in trigger)
        # Search-term rules fire on search only; field-only rules fire on browse only.
        if has_query_criterion != is_search:
            continue
        for did, doc in all_docs.items():
            meta = _parse_meta(doc)
            # All trigger criteria must match (AND).
            if not all(_check_criterion(c, doc, meta, query) for c in trigger):
                continue
            # At least one target must match (OR); empty targets = boost everything.
            if targets and not any(_check_criterion(c, doc, meta, query) for c in targets):
                continue
            scores[did] = scores.get(did, 0.0) + boost


def _full_doc_field_params(exclude_vec: bool) -> dict:
    """
    Search params that return every stored field (optionally minus ``vec``).

    ``include_fields="*"`` is only a wildcard on newer Typesense releases; on 0.25.x
    it silently returns just the schema-declared fields, dropping every
    dynamically-indexed field.  Omitting ``include_fields`` returns all fields on
    every version.
    """
    return {"exclude_fields": "vec"} if exclude_vec else {}


def list_documents(index_id: str, page: int, per_page: int,
                   exclude_vec: bool = False) -> dict:
    """Paginated document listing for an ISP-managed collection, sorted by most recently indexed."""
    return get_client().collections[collection_name(index_id)].documents.search({
        "q": "*", "query_by": "title",
        "sort_by": "indexed_at:desc",
        "page": page, "per_page": per_page,
        **_full_doc_field_params(exclude_vec),
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
    params: dict = {
        "q": "*",
        "query_by": query_by or "display",
        "sort_by": "popularity:desc",
        "page": page,
        "per_page": per_page,
        **_full_doc_field_params(exclude_vec),
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
    field_params = _full_doc_field_params(exclude_vec)
    params: dict = {
        "q": q or "*",
        "query_by": qb,
        "num_typos": 2,
        "page": page,
        "per_page": per_page,
        **field_params,
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
            **field_params,
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
        return {"hits": [{"document": d, "score": scores[d],
                          "text_match": int(scores[d] * 100_000)} for d in page_docs],
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
        import logging
        logging.getLogger(__name__).error(
            "update_document_fields failed: collection=%s doc_id=%s fields=%s",
            col_name, doc_id, list(fields.keys()), exc_info=True,
        )
        return False


def retrieve_document(col_name: str, doc_id: str) -> dict | None:
    """Fetch a single document by id from a named collection. Returns None if not found."""
    try:
        return get_client().collections[col_name].documents[doc_id].retrieve()
    except Exception as e:
        import logging
        import typesense.exceptions
        log = logging.getLogger(__name__)
        if isinstance(e, typesense.exceptions.ObjectNotFound):
            log.debug("retrieve_document: not found: collection=%s doc_id=%s", col_name, doc_id)
        else:
            # Callers treat None as "not found" — a non-404 failure here (auth,
            # connectivity, bad collection name) is otherwise indistinguishable
            # from a genuinely missing document.
            log.error("retrieve_document failed: collection=%s doc_id=%s", col_name, doc_id, exc_info=True)
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
        import logging
        logging.getLogger(__name__).error(
            # A caller cannot distinguish this from "filter matched 0 documents",
            # so a delete can silently no-op while looking like it succeeded.
            "delete_from_collection failed: collection=%s filter_by=%s", col_name, filter_by, exc_info=True,
        )
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
        import logging
        logging.getLogger(__name__).error(
            "count_external failed: collection=%s filter_by=%s", col, filter_by, exc_info=True,
        )
        return 0


# ── Search suggestions collection ────────────────────────────────────────────

def ensure_suggestions_collection() -> None:
    """
    Create the ``search_suggestions`` Typesense collection if it does not already exist.

    Called once at startup.  Idempotent — swallows ObjectAlreadyExists so repeated
    restarts are safe.  ``popularity`` is the default sort so the most-searched terms
    surface first before any boost overrides are applied.
    """
    import typesense.exceptions
    try:
        get_client().collections.create({
            "name": "search_suggestions",
            "fields": [
                {"name": "id",             "type": "string"},
                {"name": "query",          "type": "string"},
                {"name": "popularity",     "type": "int32"},
                {"name": "boost",          "type": "int32"},
                {"name": "type",           "type": "string"},
                {"name": "category",       "type": "string", "optional": True},
                # Link-mode fields — all optional so existing documents are unaffected.
                {"name": "mode",           "type": "string", "optional": True},
                {"name": "url",            "type": "string", "optional": True},
                {"name": "featured_doc_ids","type": "string[]", "optional": True},
            ],
            "default_sorting_field": "popularity",
        })
    except typesense.exceptions.ObjectAlreadyExists:
        pass


def upsert_suggestions(entries: list[dict]) -> int:
    """
    Batch-upsert suggestion dicts into the ``search_suggestions`` collection.

    Each entry must include ``id``, ``query``, ``popularity`` (int), ``boost`` (int),
    ``type`` (str), and optionally ``category`` (str).  Follows the same upsert pattern
    as ``upsert_to_collection`` — full replacement on conflict keyed by ``id``.
    Returns the count of entries reported as successful by Typesense.
    """
    if not entries:
        return 0
    results = get_client().collections["search_suggestions"].documents.import_(
        entries, {"action": "upsert"}
    )
    return sum(1 for r in results if r.get("success"))


def get_suggestions(q: str, category: str | None, limit: int) -> list[dict]:
    """
    Prefix-search the ``search_suggestions`` collection and return shaped results.

    ``category`` triggers a tie-breaking eval expression so category-matched entries
    float above generic results while still respecting boost and popularity signals.
    Returns a list of dicts with ``text``, ``type``, ``popularity``, ``boosted``,
    and optionally ``category``.
    """
    if category:
        sort_by = f"boost:desc,_eval(category:={category}):desc,popularity:desc"
        # Hard-filter to this category's catalog suggestions OR global query-frequency
        # entries (type=query have no category). Without this, suggestions seeded from
        # other indexes (e.g. account, devices) bleed through on shared prefix matches.
        filter_by = f"category:={category} || type:=query"
    else:
        sort_by = "boost:desc,popularity:desc"
        filter_by = None

    params: dict = {
        "q": q,
        "query_by": "query",
        "prefix": "true",
        "num_typos": "1",
        "per_page": str(limit),
        "sort_by": sort_by,
        # id is included (no exclude_fields) so callers can use it for update calls.
    }
    if filter_by:
        params["filter_by"] = filter_by
    try:
        raw = get_client().collections["search_suggestions"].documents.search(params)
    except Exception:
        return []

    out = []
    for hit in raw.get("hits", []):
        doc = hit.get("document", {})
        entry: dict = {
            "id":         doc.get("id", ""),
            "text":       doc.get("query", ""),
            "type":       doc.get("type", ""),
            "popularity": doc.get("popularity", 0),
            # boost > 0 means an operator manually promoted this suggestion
            "boosted":    (doc.get("boost") or 0) > 0,
        }
        if doc.get("category"):
            entry["category"] = doc["category"]
        # Forward link-mode fields when present — omitted keys mean "not set".
        if doc.get("mode"):            entry["mode"] = doc["mode"]
        if doc.get("url"):             entry["url"] = doc["url"]
        if doc.get("featured_doc_ids"): entry["featured_doc_ids"] = doc["featured_doc_ids"]
        out.append(entry)
    return out


def get_featured_doc_ids_for_query(q: str) -> list[str]:
    """
    Return featured_doc_ids for an exact query match in search_suggestions.

    Used by the search endpoint to auto-pin docs when the user's search term
    matches a suggestion that has featured_doc_ids configured.  Returns [] when
    no match is found or the collection is unavailable.
    """
    try:
        raw = get_client().collections["search_suggestions"].documents.search({
            "q": q.lower().strip(),
            "query_by": "query",
            "num_typos": "0",
            "per_page": "1",
        })
        hits = raw.get("hits", [])
        if hits:
            doc = hits[0].get("document", {})
            if doc.get("query", "").strip().lower() == q.lower().strip():
                return doc.get("featured_doc_ids") or []
    except Exception:
        pass
    return []


def list_suggestions_by_category(category: str | None, q: str, page: int, per_page: int) -> dict:
    """
    Paginated suggestion listing for the admin UI.

    Filters by category when provided; returns all suggestions otherwise.
    Sorted by boost then popularity so high-priority entries always appear first.
    Returns ``{"hits": [...], "found": int}``.
    """
    params: dict = {
        "q": q or "*",
        "query_by": "query",
        "sort_by": "boost:desc,popularity:desc",
        "page": page,
        "per_page": per_page,
    }
    if category:
        params["filter_by"] = f"category:={category}"
    try:
        raw = get_client().collections["search_suggestions"].documents.search(params)
    except Exception:
        return {"hits": [], "found": 0}
    # Flatten to plain document dicts — all fields (including mode, url,
    # featured_doc_id) pass through automatically since they live on the document.
    return {"hits": [h.get("document", {}) for h in raw.get("hits", [])],
            "found": raw.get("found", 0)}


def fetch_catalog_boost_map(category: str) -> dict[str, int]:
    """
    Return {suggestion_id: boost} for every type=catalog entry in a category.

    Called by the seeder before it deletes and re-seeds catalog suggestions so that
    operator boost values survive the refresh cycle.  Paginates until all entries
    are collected (Typesense hard cap is 250 per page).
    """
    result: dict[str, int] = {}
    page = 1
    while True:
        try:
            raw = get_client().collections["search_suggestions"].documents.search({
                "q": "*",
                "query_by": "query",
                "filter_by": f"type:=catalog && category:={category}",
                "per_page": 250,
                "page": page,
                "include_fields": "id,boost",
            })
        except Exception:
            break
        hits = raw.get("hits", [])
        for hit in hits:
            doc = hit.get("document", {})
            if doc.get("id"):
                result[doc["id"]] = int(doc.get("boost") or 0)
        if len(hits) < 250:
            break
        page += 1
    return result


def update_suggestion(suggestion_id: str, fields: dict) -> bool:
    """
    Partially update arbitrary fields on a single suggestion document.

    ``fields`` may include any combination of boost, mode, url, featured_doc_id.
    Typesense partial-update semantics mean omitted keys are left unchanged.
    Returns True on success, False if Typesense raises (e.g. document not found).
    """
    try:
        get_client().collections["search_suggestions"].documents[suggestion_id].update(fields)
        return True
    except Exception:
        import logging
        logging.getLogger(__name__).error(
            "update_suggestion failed: suggestion_id=%s fields=%s", suggestion_id, fields, exc_info=True,
        )
        return False


def update_suggestion_boost(suggestion_id: str, boost: int) -> bool:
    """
    Thin backwards-compatible wrapper around ``update_suggestion`` for boost-only updates.

    Kept so existing callers that only need to change boost do not need to change.
    """
    return update_suggestion(suggestion_id, {"boost": boost})


def delete_suggestion(suggestion_id: str) -> bool:
    """Hard-delete a suggestion by id. Returns True on success, False on exception."""
    try:
        get_client().collections["search_suggestions"].documents[suggestion_id].delete()
        return True
    except Exception:
        return False
