"""
Retrieval — hybrid (lexical + structured + semantic) Typesense search, RRF
merge, and the deterministic sorted-search path for a resolved superlative.

Relevance floors (dropping weak lexical/semantic hits before the RRF merge)
live in search_relevance.py, shared with typesense_client.search_with_vec —
the equivalent merge used by the listing page's search. See that module for
the full rationale and calibration notes.
"""
from __future__ import annotations
import asyncio
import json
import logging

from isp.services.infra import typesense_client as ts
from isp.services.infra import embedding_service
from isp.services.infra.embedding_service import embed_query as _embed_query
from isp.services.domain import search_relevance

log = logging.getLogger(__name__)

_RRF_N = 15


def _build_search_params(
    query: str, col: str, vec: list[float] | None,
    query_by: str, query_by_weights: str | None,
    facet_query_by: str | None, price_filter: str | None,
) -> list[dict]:
    lex: dict = {
        "collection": col, "q": query, "query_by": query_by,
        "per_page": str(_RRF_N), "exclude_fields": "vec",
    }
    if query_by_weights:
        lex["query_by_weights"] = query_by_weights
    if price_filter:
        lex["filter_by"] = price_filter
    searches = [lex]

    if facet_query_by:
        struct: dict = {
            "collection": col, "q": query, "query_by": facet_query_by,
            "num_typos": "0", "per_page": str(_RRF_N), "exclude_fields": "vec",
        }
        if price_filter:
            struct["filter_by"] = price_filter
        searches.append(struct)

    if vec:
        vec_str = ",".join(str(v) for v in vec)
        sem: dict = {
            "collection": col, "q": "*", "query_by": "title",
            "vector_query": f"vec:([{vec_str}], k:{_RRF_N})",
            "per_page": str(_RRF_N), "exclude_fields": "vec",
        }
        if price_filter:
            sem["filter_by"] = price_filter
        searches.append(sem)

    return searches


def _rrf_merge(
    results_list: list[dict], has_struct: bool, has_vec: bool, query: str = "",
    embed_model: str | None = None, boost_rules: list[dict] | None = None,
) -> list[dict]:
    idx = 0
    lex_hits    = results_list[idx].get("hits", []) if idx < len(results_list) else []
    lex_hits    = search_relevance.filter_lexical_hits(lex_hits, query)
    idx += 1
    struct_hits = results_list[idx].get("hits", []) if (has_struct and idx < len(results_list)) else []
    struct_hits = search_relevance.filter_lexical_hits(struct_hits, query)
    if has_struct:
        idx += 1
    sem_hits    = results_list[idx].get("hits", []) if (has_vec and idx < len(results_list)) else []
    sem_hits    = search_relevance.filter_semantic_hits(sem_hits, embed_model)

    if not has_struct and not has_vec:
        if not boost_rules:
            return lex_hits[:5]
        # Same operator-defined boost rules the main search endpoint applies
        # (routers/search.py) — chat runs its own separate RRF merge, so
        # nothing shares that call automatically; this is that same
        # ts.apply_boost_rules(scores, all_docs, rules, query) pattern,
        # applied to this branch's lexical-only candidate set.
        scores = {h["document"]["id"]: 0.0 for h in lex_hits}
        all_docs = {h["document"]["id"]: h["document"] for h in lex_hits}
        ts.apply_boost_rules(scores, all_docs, boost_rules, query=query)
        top5 = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)[:5]
        return [{"document": all_docs[did]} for did in top5]

    K = 60
    lex_map    = {h["document"]["id"]: (r, h["document"]) for r, h in enumerate(lex_hits)}
    struct_map = {h["document"]["id"]: (r, h["document"]) for r, h in enumerate(struct_hits)}
    sem_map    = {h["document"]["id"]: (r, h["document"]) for r, h in enumerate(sem_hits)}

    # struct was 0.6 (dominant) on the assumption it always searches short,
    # genuinely categorical facet values (see the commit that introduced it:
    # "plants which are pet safe" resolving off pet_safety_label) — a clean
    # exact-match win worth trusting over lexical/semantic noise. It's since
    # been repurposed to run over chatContent (a long free-text attribute
    # summary, not a facet value), where num_typos=0 against a single common
    # word (e.g. "airpods") ties every containing document at an identical
    # score; Typesense then breaks that tie by document order, not
    # relevance, and 0.6 let that arbitrary order override everything else —
    # e.g. outranking real AirPods products with a charger merely mentioning
    # AirPods compatibility. sem is the most reliable signal for a bare
    # product-name query — a real match sits far closer in embedding space
    # than an incidental mention does (observed: ~0.44-0.49 cosine distance
    # for genuine AirPods vs. ~0.53+ for chargers/kits on this same query) —
    # so it gets the plurality; lex and struct drop to equal, smaller votes.
    # Verified against 7 queries across two indexes
    # (generic product name, category, device+category, and two genuine
    # short-facet attribute queries on Plants) — identical or better results
    # on every one, including both attribute-facet cases the old 0.6 was
    # tuned for.
    scores: dict[str, float] = {}
    for did, (rank, _) in lex_map.items():
        scores[did] = scores.get(did, 0) + 0.2 / (K + rank + 1)
    for did, (rank, _) in struct_map.items():
        scores[did] = scores.get(did, 0) + 0.2 / (K + rank + 1)
    for did, (rank, _) in sem_map.items():
        scores[did] = scores.get(did, 0) + 0.6 / (K + rank + 1)

    all_docs = {**{d: doc for d, (_, doc) in lex_map.items()},
                **{d: doc for d, (_, doc) in struct_map.items()},
                **{d: doc for d, (_, doc) in sem_map.items()}}

    # Operator-defined boost rules (routers/search.py's ts.apply_boost_rules) —
    # applied post-merge, same as the main search endpoint, so a rule built in
    # Settings affects chat results too instead of only the product-grid search.
    if boost_rules:
        ts.apply_boost_rules(scores, all_docs, boost_rules, query=query)

    top5 = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)[:5]
    return [{"document": all_docs[did]} for did in top5]


def _hit_to_card_and_block(hit: dict, i: int) -> tuple[dict, str]:
    doc  = hit.get("document", {})
    meta: dict = {}
    raw = doc.get("metadata", "")
    if raw:
        try:
            meta = json.loads(raw)
        except Exception:
            log.debug("_hit_to_card_and_block: could not parse metadata JSON for doc id=%s",
                     doc.get("id"), exc_info=True)

    title = doc.get("title", "")
    url   = doc.get("url", "")

    raw_price = meta.get("price", meta.get("Price", ""))
    price = str(raw_price) if raw_price != "" else ""

    images_field = meta.get("images")
    if isinstance(images_field, list):
        image = images_field[0] if images_field else ""
    else:
        image = meta.get("image_url", meta.get("image", meta.get("Image", meta.get("thumbnail", ""))))

    chat_ctx = doc.get("chatContent") or doc.get("content", "")[:300]
    block = f"{i}. {title}\n  {chat_ctx}" if chat_ctx else f"{i}. {title}"
    # Fields beyond the original 4 (id/title/price/url/image) exist so the
    # PLP's chat widget can render the same rich product card used on the
    # main grid (swatches, manufacturer, stock badge) instead of a
    # thinner chat-only card — additive, existing consumers reading only
    # the original fields are unaffected.
    card = {
        "id": doc.get("id", ""), "title": title, "price": price, "url": url, "image": image,
        "manufacturer": meta.get("manufacturer", ""),
        "category": meta.get("category", ""),
        "color_options": meta.get("color_options") or [],
        "in_stock": meta.get("in_stock", True),
        "is_featured": doc.get("is_featured", False),
        # Consistent, display-safe feature/spec blurb built at indexing time
        # by a card_summary_*.js extension (see docs/ — mirrors chatContent's
        # per-index-type pattern, but excludes fields already on this card
        # and strips HTML). "" when no such extension is assigned to this
        # index, same graceful-absence behavior as every other meta.get here.
        "summary": doc.get("cardSummary", ""),
    }
    return card, block


async def search_context(
    query: str, index_id: str,
    query_by: str = "title,content",
    query_by_weights: str | None = None,
    facet_query_by: str | None = None,
    price_filter: str | None = None,
    boost_rules: list[dict] | None = None,
) -> tuple[list[dict], str]:
    log.debug("chat vector embedding request: query=%r", query)
    vec    = await _embed_query(query)
    log.debug("chat vector embedding response: dim=%s", len(vec) if vec else 0)

    col    = ts.collection_name(index_id)
    client = ts.get_client()

    searches = _build_search_params(
        query, col, vec, query_by, query_by_weights, facet_query_by, price_filter,
    )
    log.debug("chat typesense request: collection=%s searches=%s",
              col, json.dumps([{k: v for k, v in s.items() if k != "vector_query"}
                               for s in searches]))
    resp = await asyncio.to_thread(
        lambda: client.multi_search.perform({"searches": searches}, {})
    )
    log.debug(
        "chat typesense response: hit_counts=%s",
        [len(r.get("hits", [])) for r in resp.get("results", [])],
    )
    hits = _rrf_merge(
        resp.get("results", []),
        has_struct=facet_query_by is not None,
        has_vec=vec is not None,
        query=query,
        # _embed_query above was called with no model override, so it always
        # embeds with whatever the global current model is — that's the
        # model these vectors (and thus vector_distance) actually came from.
        embed_model=embedding_service.get_current_model(),
        boost_rules=boost_rules,
    )

    cards, parts = [], []
    for i, hit in enumerate(hits, 1):
        card, block = _hit_to_card_and_block(hit, i)
        cards.append(card)
        parts.append(block)

    return cards, "\n\n".join(parts)


def _ordinal_labels(phrase: str) -> list[str]:
    """
    ("cheapest", "2nd cheapest", "3rd cheapest") for the matched superlative
    phrase — works generically for any matched phrase, including multi-word
    ones ("most expensive" -> "2nd most expensive"), since English forms this
    ordinal the same way regardless of the specific adjective.
    """
    return [phrase, f"2nd {phrase}", f"3rd {phrase}"]


async def search_context_sorted(
    query: str, index_id: str, sort_field: str, sort_direction: str,
    query_by: str = "title,content",
    query_by_weights: str | None = None,
    price_filter: str | None = None,
    superlative_phrase: str | None = None,
) -> tuple[list[dict], str]:
    """
    Deterministic path for a resolved superlative query (see
    docs/superlativePriceDesign.md). A single Typesense search sorted by
    sort_field — RRF blending is skipped entirely, not reweighted, since
    blending in a semantic-similarity signal that has no concept of the
    target field is exactly what causes a superlative query to rank the
    wrong item first. Text narrowing (e.g. "iphone" in "cheapest iphone")
    still applies via query_by; only the ranking is overridden.

    The top 3 hits get an explicit rank label ("(cheapest)", "(2nd cheapest)",
    "(3rd cheapest)") written directly into their context block, not just left
    as positional order — the model only has to copy a label it can already
    see, instead of inferring rank from list position, which is the exact
    inference it's been shown to get wrong even on correctly sorted data.
    """
    col    = ts.collection_name(index_id)
    client = ts.get_client()
    search: dict = {
        "collection": col, "q": query, "query_by": query_by,
        "sort_by": f"{sort_field}:{sort_direction}",
        "per_page": "5", "exclude_fields": "vec",
    }
    if query_by_weights:
        search["query_by_weights"] = query_by_weights
    if price_filter:
        search["filter_by"] = price_filter
    log.debug("chat typesense request (superlative sort): collection=%s search=%s",
              col, json.dumps(search))
    resp = await asyncio.to_thread(
        lambda: client.multi_search.perform({"searches": [search]}, {})
    )
    hits = resp.get("results", [{}])[0].get("hits", [])
    log.debug("chat typesense response (superlative sort): hit_count=%s", len(hits))

    labels = _ordinal_labels(superlative_phrase) if superlative_phrase else []

    cards, parts = [], []
    for i, hit in enumerate(hits, 1):
        card, block = _hit_to_card_and_block(hit, i)
        if i <= len(labels):
            block = block.replace(f"{i}. {card['title']}", f"{i}. {card['title']} ({labels[i - 1]})", 1)
        cards.append(card)
        parts.append(block)

    return cards, "\n\n".join(parts)


def pinned_cards(messages: list[dict]) -> list[dict]:
    """Return product cards from the most recent assistant turn."""
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("products"):
            return m["products"]
    return []


def merge_cards(primary: list[dict], pinned: list[dict]) -> list[dict]:
    """Top 3 from rewritten search + up to 2 from prior context, deduplicated."""
    top3 = primary[:3]
    seen = {c["id"] for c in top3}
    extra = [c for c in pinned if c["id"] not in seen]
    return (top3 + extra)[:5]
