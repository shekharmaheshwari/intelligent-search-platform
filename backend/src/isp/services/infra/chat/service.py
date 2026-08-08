"""
Chat service — dispatcher + RAG context assembly.

Provider routing:
  "none"        → yield error, return
  "anthropic"   → providers/anthropic.py
  "google"      → providers/google.py
  "openai"      → providers/openai_compat.py (base_url=None)
  "groq"        → providers/openai_compat.py (base_url=Groq endpoint)
  "self_hosted" → providers/openai_compat.py (base_url=user endpoint)

Follow-up detection:
  OFF (default) — T1 regex → T2 regex → context-aware short-query guard
                  short-query guard checks shown product titles, not a brand list —
                  works for any domain (plants, phones, furniture, …) without changes.
  ON            — LLM rewrites the query using full conversation context;
                  no ID filter applied; price filter is kept as a hard constraint.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import AsyncIterator

log = logging.getLogger(__name__)

from isp.services.infra import typesense_client as ts
from isp.services.infra.embedding_service import embed_query as _embed_query
from isp.services.infra.chat.providers import anthropic_chat, google_chat, openai_compat
from isp.services.domain import index_service

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# All LLM-facing prompt text and user-facing chat copy lives in prompts.json,
# not inline in code — keeps wording changes a data edit, not a code change,
# and is the basis for making it operator-configurable per index later.
_PROMPTS: dict = json.loads((Path(__file__).with_name("prompts.json")).read_text(encoding="utf-8"))

# Defaults applied whenever an index's chat_persona_config doesn't set a given key —
# {} behaves identically to the fixed prompt this replaced. Imported by routers/indexes.py
# so the GET /chat-persona endpoint returns the same effective values chat_stream uses.
CHAT_PERSONA_DEFAULTS: dict = {
    "item_noun": "product",
    "enable_comparison": True,
    "additional_instructions": "",
}

_chat_provider:          str  = "none"
_chat_model:             str  = ""
_chat_api_key:           str  = ""
_chat_endpoint:          str  = ""
_smart_followup_enabled: bool = False

_FILLER_RE = re.compile(
    r"^\s*(show me|find me|can you (show|find)|give me|what are|i('m| am) looking for"
    r"|search for|look for|find|show|i want|i need|tell me about|do you have)\s+",
    re.IGNORECASE,
)

def _normalize_query(q: str) -> str:
    """Strip leading conversational filler so BM25 only sees product terms."""
    return _FILLER_RE.sub("", q).strip() or q


def get_provider() -> str:
    return _chat_provider


def set_chat_config(
    *, provider: str, model: str, api_key: str, endpoint: str,
    smart_followup: bool = False,
) -> None:
    global _chat_provider, _chat_model, _chat_api_key, _chat_endpoint, _smart_followup_enabled
    _chat_provider          = provider
    _chat_model             = model
    _chat_api_key           = api_key
    _chat_endpoint          = endpoint
    _smart_followup_enabled = smart_followup
    if provider == "anthropic":
        anthropic_chat.configure(api_key)
    elif provider == "google":
        google_chat.configure(api_key)


# ── Price filter ───────────────────────────────────────────────────────────────

_PRICE_BETWEEN = re.compile(
    r'between[\s.,]*\$?(\d+(?:\.\d+)?)\s*(?:and|to|-)[\s.,]*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)
_PRICE_LT = re.compile(
    r'(?:less than|under|cheaper than|below)[\s.,]*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)
_PRICE_GT = re.compile(
    r'(?:more than|over|above|at least)[\s.,]*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)


def _extract_price_filter(query: str) -> str | None:
    m = _PRICE_BETWEEN.search(query)
    if m:
        return f"price:>={m.group(1)} && price:<={m.group(2)}"
    m = _PRICE_LT.search(query)
    if m:
        return f"price:<{m.group(1)}"
    m = _PRICE_GT.search(query)
    if m:
        return f"price:>{m.group(1)}"
    return None


_RELATIVE_TERM_RE = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in _PROMPTS["vague_preference_terms"]) + r')\b',
    re.IGNORECASE,
)

_HAS_NUMBER_RE = re.compile(r'\d')


def _is_vague_preference_query(query: str) -> bool:
    """
    True when the query expresses a relative/comparative preference — not just
    price ("cheap", "premium"), but any vague quality dimension ("fast", "good
    camera", "durable") — without any concrete number attached (a price, a spec
    value, a model number). A single closed class of comparative/relative
    adjectives covers this regardless of which attribute they modify, so one
    regex generalizes across every dimension rather than needing a separate
    hardcoded check per attribute. Signals the LLM to answer with its best guess
    but flag that it's inferring the user's preference, rather than silently
    presenting a guess as certain.
    """
    return bool(_RELATIVE_TERM_RE.search(query)) and not _HAS_NUMBER_RE.search(query)


async def _index_has_price_field(index_id: str) -> bool:
    """
    True when this index's Typesense schema has a `price` field. Lets the
    vague-preference note invite a budget only for catalogs that actually have
    one to filter on — inviting "share a budget" for an index with no price
    field (documentation, listings, anything non-commerce) would be a mismatch.
    """
    fields = await asyncio.to_thread(ts.get_all_fields, index_id)
    return any(f.get("name") == "price" for f in fields)


# ── Superlative queries ("cheapest", "most expensive", "newest") ───────────────
#
# See docs/superlativePriceDesign.md for the full design and why. Three layers,
# each owned by the part of the system that actually knows the answer:
#   1. Detection  — generic, linguistic (any superlative construction).
#   2. Concept    — a small fixed table, because English fixes "cheapest" = price
#                    regardless of catalog; NOT resolved via LLM or embeddings.
#   3. Field      — schema-dependent per index, resolved deterministically
#                    (exact name match, then numeric/date name-pattern match),
#                    write-through cached on indexes.superlative_field_map so
#                    repeat queries don't re-hit Typesense's schema endpoint.
# A superlative with no fixed concept ("best", "nicest") or no resolvable field
# on this index falls through to the existing vague-preference path rather
# than guessing — caching a wrong guess would just make it wrong consistently.

_SUPERLATIVE_RE = re.compile(r'\b(\w+est|most\s+\w+|least\s+\w+)\b', re.IGNORECASE)

# phrase -> (concept, sort direction)
_SUPERLATIVE_CONCEPT_MAP: dict[str, tuple[str, str]] = {
    "cheapest":        ("price", "asc"),
    "lowest price":    ("price", "asc"),
    "lowest priced":   ("price", "asc"),
    "least expensive": ("price", "asc"),
    "most affordable": ("price", "asc"),
    "lowest cost":     ("price", "asc"),
    "most expensive":  ("price", "desc"),
    "highest price":   ("price", "desc"),
    "highest priced":  ("price", "desc"),
    "priciest":        ("price", "desc"),
    "most popular":    ("popularity", "desc"),
    "best selling":    ("popularity", "desc"),
    "bestselling":     ("popularity", "desc"),
    "newest":          ("recency", "desc"),
    "latest":          ("recency", "desc"),
    "biggest":         ("size", "desc"),
    "largest":         ("size", "desc"),
    "smallest":        ("size", "asc"),
    "top rated":       ("rating", "desc"),
    "highest rated":   ("rating", "desc"),
}

# concept -> candidate field-name substrings, tried in order; exact match wins first
_CONCEPT_FIELD_CANDIDATES: dict[str, list[str]] = {
    "price":               ["price", "cost", "rate", "fee"],
    "popularity":          ["popularity", "popular"],
    "recency":             ["release_date", "created_at", "date", "launched"],
    "size":                ["size", "screen_size", "capacity"],
    "rating":              ["rating", "review_score", "stars"],
    "device_compatibility": ["compatible_devices", "compatible_with", "device_compatibility"],
}

_SORTABLE_TYPES = {"int32", "int64", "float"}


def _detect_superlative_concept(query: str) -> tuple[str, str, str] | None:
    """
    Return (matched_phrase, concept, direction) for a recognized superlative
    phrase, or None. Runs the generic superlative regex first purely as a
    cheap short-circuit — the actual decision comes from the fixed concept
    table, not the regex. The matched phrase is returned (not just the
    concept) so the caller can strip it from the text sent to Typesense —
    see _strip_structural_phrases.
    """
    if not _SUPERLATIVE_RE.search(query):
        return None
    low = query.lower()
    for phrase, (concept, direction) in _SUPERLATIVE_CONCEPT_MAP.items():
        if phrase in low:
            return phrase, concept, direction
    return None


def _strip_structural_phrases(query: str, superlative_phrase: str) -> str:
    """
    Remove the matched superlative phrase and any price-range phrase from the
    query text before it's used as Typesense's lexical `q`. Both are already
    captured structurally (sort_by / filter_by) — leaving them in the text
    only adds tokens that don't appear in any product's text, which degrades
    or entirely breaks Typesense's default lexical matching as more such
    "noise" tokens pile up (verified directly: "cheapest iphone" matches 8
    documents; "cheapest iphone under $800" matches 0, purely from extra
    unmatchable tokens). Falls back to the original query if stripping would
    leave nothing (e.g. the query was just "cheapest" with no subject).
    """
    text = re.sub(re.escape(superlative_phrase), '', query, flags=re.IGNORECASE)
    text = _PRICE_BETWEEN.sub('', text)
    text = _PRICE_LT.sub('', text)
    text = _PRICE_GT.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text or "*"


def _match_field(fields: list[dict], candidates: list[str]) -> str | None:
    """Exact name match first (any type); then a numeric/date field whose name
    contains a candidate substring, preferring the shortest (most generic) name."""
    names = {f.get("name"): f for f in fields}
    for cand in candidates:
        if cand in names:
            return cand
    substring_matches = [
        f["name"] for f in fields
        if f.get("type") in _SORTABLE_TYPES
        and any(cand in (f.get("name") or "").lower() for cand in candidates)
    ]
    return min(substring_matches, key=len) if substring_matches else None


async def _resolve_concept_field(
    index_id: str, concept: str, cached_map: dict,
) -> str | None:
    """
    Resolve a concept (e.g. "price", "device_compatibility") to an actual
    field name on this index. Checks the write-through cache first; on a
    miss, resolves live against the schema and persists the result for next
    time (merged, not overwritten, so concurrent requests resolving different
    concepts don't clobber each other). Shared by both the superlative-price
    feature and device-variant matching — same cache
    (indexes.superlative_field_map), same resolution strategy, different
    concepts. See docs/deviceCompatibilityMatchingDesign.md for the naming
    note on reusing that column name for a non-superlative concept.
    """
    if concept in cached_map:
        return cached_map[concept] or None
    fields = await asyncio.to_thread(ts.get_all_fields, index_id)
    field = _match_field(fields, _CONCEPT_FIELD_CANDIDATES.get(concept, [concept]))
    try:
        await index_service.merge_superlative_field_map(index_id, concept, field or "")
    except Exception:
        log.warning("failed to cache superlative_field_map for index=%s concept=%s",
                   index_id, concept, exc_info=True)
    return field


def _combine_filters(*filters: str | None) -> str | None:
    parts = [f for f in filters if f]
    return " && ".join(parts) if parts else None


async def _resolve_device_filter(query: str, index_id: str, field_name: str) -> str | None:
    """
    Two-step device-variant resolution (see
    docs/deviceCompatibilityMatchingDesign.md). Step 1: let Typesense's own
    typo tolerance resolve messy input ("ihpone17 promax") against the real
    compatible_devices values on this index — no new fuzzy-matching code of
    our own. Step 2: use the top hit's actual value, not the user's raw text,
    to build a precise exact-match filter, so a query naming one specific
    variant doesn't get blended with lexically-similar sibling variants
    ("iPhone 17" vs "iPhone 17 Pro Max"). Returns None when there's no
    confident match (e.g. a generic query that doesn't name a device), so the
    caller falls through to normal, unfiltered retrieval rather than forcing
    a wrong narrowing.
    """
    col    = ts.collection_name(index_id)
    client = ts.get_client()
    resp = await asyncio.to_thread(
        lambda: client.multi_search.perform({"searches": [{
            "collection": col, "q": query, "query_by": field_name,
            "per_page": "1", "exclude_fields": "vec",
        }]}, {})
    )
    hits = resp.get("results", [{}])[0].get("hits", [])
    if not hits:
        return None
    resolved = hits[0]["document"].get(field_name)
    values = resolved if isinstance(resolved, list) else [resolved]
    values = [v for v in values if v]
    if not values:
        return None
    # The top hit may be a multi-compatible product (e.g. a case listing both
    # "iPhone 17" and "iPhone 16 Pro"). Taking the whole array unconditionally
    # would widen the filter to admit items compatible ONLY with a co-listed
    # device the query never asked about. When the query itself names a model
    # number, narrow to values that share it — falls back to the full list if
    # nothing shares a number (query didn't specify one, or nothing matches).
    query_digits = set(re.findall(r'\d+', query))
    if query_digits and len(values) > 1:
        narrowed = [v for v in values if set(re.findall(r'\d+', v)) & query_digits]
        if narrowed:
            values = narrowed
    quoted = ",".join(f"`{v}`" for v in values)
    return f"{field_name}:=[{quoted}]"


# ── Follow-up detection — Tier 1 (unambiguous pronoun / intent phrases) ───────

_T1_RE = re.compile(
    r'\b('
    r'these|those|them|they|the ones?|that one|this one'
    r'|the first one|the second one|the third one|the last one'
    r'|number (one|two|three)|option \d|#\d'
    r'|give me details?|more details?|tell me more|more info|show me more'
    r'|what else|anything else|say more|tell me about (it|them)'
    r'|side by side|pros and cons|help me decide|help me choose'
    r'|which one should i (get|buy)|which do you recommend|what would you recommend'
    r'|which is (better|best|cheaper|more expensive)|which one is (better|best)'
    r'|compare|versus|vs\b|the best value'
    r'|does it have|does it support|compatible with'
    r'|what colou?rs does it|what size does it|what comes in the box|what.s included'
    r'|is it waterproof|is it wireless|is it compatible'
    r'|is it in stock|is it available|back in stock|when will it be back'
    r'|how much is it|how much does it cost|how much are they'
    r'|and the price|and the cost'
    r"|i'll take it|i want that one|how do i order|how do i buy"
    r'|what you showed|what you mentioned|the ones you listed'
    r'|the results|the options above|the items above|listed above|mentioned above'
    r')\b',
    re.IGNORECASE,
)


def _is_follow_up_tier1(query: str) -> bool:
    return bool(_T1_RE.search(query))


# ── Follow-up detection — Tier 2 (anchored refinement phrases) ────────────────

_T2_RE = re.compile(
    r'\b('
    r"i('m| am) interested in|i prefer|not that one|not those"
    r'|something else|other options?|show me alternatives?'
    r'|do you have it in|any in|from .+ brand|by .+ brand'
    r')\b',
    re.IGNORECASE,
)


def _is_follow_up_tier2(query: str) -> bool:
    return bool(_T2_RE.search(query))


# ── Follow-up detection — Tier 3 (context-aware short-query guard) ────────────
#
# Domain-agnostic: no hardcoded brand/category lists.
# Checks whether query tokens already appeared in previously shown product titles.
# Works identically for phones, plants, furniture, cars — any catalog.

def _shown_product_tokens(messages: list[dict]) -> set[str]:
    """
    Words (>2 chars) from the titles of the products shown in the MOST RECENT
    assistant turn only — not accumulated across the entire conversation.
    Matches the same "most recent turn only" scoping _ids_from_history and
    _pinned_cards already use elsewhere in this file.

    Accumulating across all history let unrelated, non-adjacent topics bleed
    into this guard: "iphone adapter" was wrongly classified as a follow-up
    and ID-pinned to a *different* prior turn's Samsung adapters, because
    "iphone" appeared in an even-earlier "iPhone cases" turn and "adapter"
    appeared in the (unrelated) Samsung-adapter turn — neither together, and
    neither in the actually-relevant most recent turn, but the accumulated
    set couldn't tell the difference.
    """
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("products"):
            return {
                w.lower()
                for p in m["products"]
                for w in (p.get("title") or "").split()
                if len(w) > 2
            }
    return set()


def _is_follow_up_short_query(query: str, messages: list[dict]) -> bool:
    words = query.strip().split()
    if len(words) > 5 or not messages:
        return False
    shown = _shown_product_tokens(messages)
    # A token is "content" if it's long enough to be meaningful on its own, OR
    # contains a digit — model/generation numbers ("16" vs "17", "S24" vs "S25",
    # "4G" vs "5G") are almost never filler, and are often exactly the token
    # that distinguishes a genuinely new query from the previous turn's topic,
    # even though they're short. Dropping them let e.g. "iphone 17 case" be
    # misread as a follow-up on an already-shown iPhone 16 case, because "17"
    # never got compared against what was actually shown.
    content_words = [w.lower() for w in words if len(w) > 3 or any(c.isdigit() for c in w)]
    if not content_words:
        return True  # "ok" / "yes" / "and?" — almost certainly a follow-up
    new_terms = [w for w in content_words if w not in shown]
    return len(new_terms) == 0  # every meaningful word is familiar → follow-up


def _is_follow_up_heuristic(query: str, messages: list[dict]) -> bool:
    return (
        _is_follow_up_tier1(query)
        or _is_follow_up_tier2(query)
        or _is_follow_up_short_query(query, messages)
    )


# ── History helpers ────────────────────────────────────────────────────────────

def _price_filter_from_history(messages: list[dict]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            f = _extract_price_filter(msg.get("content", ""))
            if f:
                return f
    return None


def _ids_from_history(messages: list[dict]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            ids = [p["id"] for p in msg.get("products", []) if p.get("id")]
            if ids:
                return "id:[" + ",".join(ids) + "]"
    return None


def _scoped_history(messages: list[dict]) -> list[dict]:
    """
    Trim history to the current follow-up chain, so the LLM doesn't see turns
    from an earlier, unrelated topic.

    Walks backward from the most recent turn. A turn stays in scope as long as
    it was itself a follow-up relative to what preceded it — re-testing each
    historical query with the same heuristic used for the live query. The walk
    stops at (and keeps) the first turn, going backward, that was NOT a
    follow-up: that's the turn that actually started the current topic, so
    everything before it belongs to a different conversation and is dropped.
    """
    user_idxs = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not user_idxs:
        return messages
    boundary = user_idxs[0]  # no topic boundary found → the whole thing is one chain
    for i in reversed(user_idxs):
        prior = messages[:i]
        if not _is_follow_up_heuristic(messages[i].get("content", ""), prior):
            boundary = i
            break
    return messages[boundary:]


def _resolve_retrieval_filter(
    query: str, messages: list[dict], is_follow_up: bool,
) -> str | None:
    price_filter = _extract_price_filter(query)
    if price_filter is None and is_follow_up:
        price_filter = _price_filter_from_history(messages)
    id_filter = _ids_from_history(messages) if is_follow_up else None
    if id_filter and price_filter:
        return f"{id_filter} && {price_filter}"
    return id_filter or price_filter


# ── Smart follow-up: LLM query rewriting ──────────────────────────────────────
#
# When _smart_followup_enabled is True the LLM sees the full conversation —
# prior queries, product titles shown, assistant summaries — and rewrites the
# current query into a standalone search string.
#
# "compare it with samsung"  →  "Samsung Galaxy Buds vs Google Pixel Buds Pro2"
# "which blooms in winter"   →  "Monstera Fern winter blooming plant"
# "samsung buds" (new topic) →  "samsung buds"  (returned unchanged)
#
# The rewritten string is used for Typesense retrieval only.
# The original query is still what the LLM sees in the chat history.
# No ID filter is applied in smart mode; price filter is preserved.

def _build_rewrite_context(messages: list[dict]) -> str:
    """Concise context from last 6 messages including shown product titles."""
    parts = []
    for m in messages[-6:]:
        role    = m.get("role", "")
        content = (m.get("content") or "")[:120]
        prods   = m.get("products") or []
        if role == "user" and content:
            parts.append(f"User: {content}")
        elif role == "assistant":
            if prods:
                titles = ", ".join(p.get("title", "") for p in prods[:5])
                parts.append(f"Shown: {titles}")
            if content:
                parts.append(f"Assistant: {content[:80]}")
    return "\n".join(parts)


async def _llm_rewrite_query(query: str, messages: list[dict]) -> str:
    """Return a standalone search query enriched with conversation context."""
    context = _build_rewrite_context(messages)
    if not context:
        return _normalize_query(query)
    system = _PROMPTS["query_rewrite_system"]
    prompt = f"History:\n{context}\n\nCurrent query: {query}\n\nRewritten query:"
    try:
        result = ""
        async for token in _route_llm_stream(system, [{"role": "user", "content": prompt}]):
            result += token
            if len(result) > 200:
                break
        return result.strip() or query
    except Exception:
        log.warning("_llm_rewrite_query: rewrite failed, using original query=%r", query, exc_info=True)
        return query


def _price_filter_for_smart(query: str, messages: list[dict]) -> str | None:
    """Price filter for smart mode — hard constraint preserved even without ID filter."""
    return _extract_price_filter(query) or _price_filter_from_history(messages)


async def _resolve_retrieval_params(
    query: str, messages: list[dict],
) -> tuple[str, str | None, list[dict]]:
    """
    Return (search_query, filter_by, history) for the Typesense call and the LLM prompt.

    Smart ON : LLM-rewritten query + price-only filter (no ID pinning) + full history —
               smart mode's own rewrite step already reasons over the whole
               conversation, so the chat history mirrors that.
    Smart OFF: original query + heuristic filter (ID + price if follow-up) + history
               scoped to the current follow-up chain — a new topic gets no prior
               turns; a follow-up gets back to (and including) whichever turn
               actually started the topic it's continuing.
    """
    if _smart_followup_enabled:
        search_query = await _llm_rewrite_query(query, messages)
        return search_query, _price_filter_for_smart(query, messages), messages
    is_fu = _is_follow_up_heuristic(query, messages)
    search_query = query if is_fu else _normalize_query(query)
    history = _scoped_history(messages) if is_fu else []
    return search_query, _resolve_retrieval_filter(query, messages, is_fu), history


def _pinned_cards(messages: list[dict]) -> list[dict]:
    """Return product cards from the most recent assistant turn."""
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("products"):
            return m["products"]
    return []


def _merge_cards(primary: list[dict], pinned: list[dict]) -> list[dict]:
    """Top 3 from rewritten search + up to 2 from prior context, deduplicated."""
    top3 = primary[:3]
    seen = {c["id"] for c in top3}
    extra = [c for c in pinned if c["id"] not in seen]
    return (top3 + extra)[:5]


# ── Typesense search ───────────────────────────────────────────────────────────

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
    results_list: list[dict], has_struct: bool, has_vec: bool,
) -> list[dict]:
    idx = 0
    lex_hits    = results_list[idx].get("hits", []) if idx < len(results_list) else []
    idx += 1
    struct_hits = results_list[idx].get("hits", []) if (has_struct and idx < len(results_list)) else []
    if has_struct:
        idx += 1
    sem_hits    = results_list[idx].get("hits", []) if (has_vec and idx < len(results_list)) else []

    if not has_struct and not has_vec:
        return lex_hits[:5]

    K = 60
    lex_map    = {h["document"]["id"]: (r, h["document"]) for r, h in enumerate(lex_hits)}
    struct_map = {h["document"]["id"]: (r, h["document"]) for r, h in enumerate(struct_hits)}
    sem_map    = {h["document"]["id"]: (r, h["document"]) for r, h in enumerate(sem_hits)}

    scores: dict[str, float] = {}
    for did, (rank, _) in lex_map.items():
        scores[did] = scores.get(did, 0) + 0.3 / (K + rank + 1)
    for did, (rank, _) in struct_map.items():
        scores[did] = scores.get(did, 0) + 0.6 / (K + rank + 1)
    for did, (rank, _) in sem_map.items():
        scores[did] = scores.get(did, 0) + 0.1 / (K + rank + 1)

    all_docs = {**{d: doc for d, (_, doc) in lex_map.items()},
                **{d: doc for d, (_, doc) in struct_map.items()},
                **{d: doc for d, (_, doc) in sem_map.items()}}
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
    card  = {"id": doc.get("id", ""), "title": title, "price": price, "url": url, "image": image}
    return card, block


async def _search_context(
    query: str, index_id: str,
    query_by: str = "title,content",
    query_by_weights: str | None = None,
    facet_query_by: str | None = None,
    price_filter: str | None = None,
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


async def _search_context_sorted(
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


# ── LLM streaming ──────────────────────────────────────────────────────────────

def _filter_cards_by_response(cards: list[dict], response_text: str) -> list[dict]:
    """
    Keep only cards whose title is actually mentioned in the LLM's finished answer.

    Plain substring containment would let a shorter title falsely match inside a
    longer, different one (e.g. "Pixel 9" is a substring of "Pixel 9a"; "Galaxy S26"
    of "Galaxy S26+"). \b can't be used for this: it anchors on a \w/\W transition,
    which breaks down whenever a title ends in punctuation ("Galaxy S26+") — the
    character after "+" is a space, both non-word, so \b never matches there and
    the title fails to match even its own literal, correct mention. Anchoring on
    "not adjacent to an alphanumeric character" instead (regardless of what
    character sits at the title's own edge) fixes that self-match, but titles can
    still contain one another as substrings ("Galaxy S26" inside "Galaxy S26+").
    So titles are checked longest-first, and each match claims its text span —
    a shorter title only counts if it has a real standalone occurrence outside any
    span a longer title already claimed. If nothing matches (e.g. the model
    paraphrased every name, or named no product at all), fall back to the original
    unfiltered list rather than showing an empty carousel.
    """
    if not response_text:
        return cards
    low = response_text.lower()
    claimed = bytearray(len(low))
    used_ids: set[int] = set()
    for c in sorted(cards, key=lambda c: -len(c.get("title") or "")):
        title = c.get("title")
        if not title:
            continue
        pattern = r'(?<![A-Za-z0-9])' + re.escape(title.lower()) + r'(?![A-Za-z0-9])'
        for m in re.finditer(pattern, low):
            if not any(claimed[m.start():m.end()]):
                used_ids.add(id(c))
                claimed[m.start():m.end()] = b'\x01' * (m.end() - m.start())
                break
    used = [c for c in cards if id(c) in used_ids]
    return used or cards


def _system_prompt(
    context: str,
    note_vague_preference: bool = False,
    has_price_field: bool = True,
    persona: dict | None = None,
    sorted_field: str | None = None,
    sorted_direction: str = "",
) -> str:
    cfg = {**CHAT_PERSONA_DEFAULTS, **(persona or {})}
    item_noun = (cfg["item_noun"] or CHAT_PERSONA_DEFAULTS["item_noun"]).strip()

    prompt = _PROMPTS["system_prompt"]["base"].format(item_noun=item_noun)
    if cfg["enable_comparison"]:
        prompt += _PROMPTS["system_prompt"]["comparison_note"]
    if sorted_field:
        direction_word = "ascending" if sorted_direction == "asc" else "descending"
        prompt += _PROMPTS["system_prompt"]["superlative_sorted_note"].format(
            field=sorted_field, direction=direction_word)
    if note_vague_preference:
        key = "vague_preference_note" if has_price_field else "vague_preference_note_no_price"
        prompt += _PROMPTS["system_prompt"][key]
    extra = (cfg["additional_instructions"] or "").strip()
    if extra:
        prompt += extra + " "
    return prompt + f"\n\n{item_noun.capitalize()} data:\n{context}"


def _route_llm_stream(system: str, history: list[dict]):
    if _chat_provider == "anthropic":
        return anthropic_chat.stream(system, history, _chat_model)
    if _chat_provider == "google":
        return google_chat.stream(system, history, _chat_model)
    if _chat_provider == "groq":
        return openai_compat.stream(system, history, _chat_model, _chat_api_key,
                                    base_url=_GROQ_BASE_URL)
    if _chat_provider == "self_hosted":
        return openai_compat.stream(system, history, _chat_model, _chat_api_key,
                                    base_url=_chat_endpoint)
    return openai_compat.stream(system, history, _chat_model, _chat_api_key)


# ── Error formatting ──────────────────────────────────────────────────────────

def _friendly_error(raw: str) -> str:
    low = raw.lower()
    errors = _PROMPTS["errors"]
    if "rate_limit" in low or "rate limit" in low or "429" in raw:
        return errors["rate_limited"]
    if "401" in raw or "authentication" in low or "api key" in low or "invalid_api_key" in low:
        return errors["invalid_api_key"]
    if "503" in raw or "overloaded" in low or "unavailable" in low:
        return errors["unavailable"]
    if "context_length" in low or "too long" in low or "max_tokens" in low:
        return errors["context_too_long"]
    return errors["generic"]


# ── Public entry point ─────────────────────────────────────────────────────────

async def chat_stream(
    query: str,
    messages: list[dict],
    index_id: str,
    query_by: str = "title,content",
    query_by_weights: str | None = None,
    facet_query_by: str | None = None,
    persona_config: dict | None = None,
    superlative_field_map: dict | None = None,
) -> AsyncIterator[dict]:
    """Yield SSE-ready dicts: products → text tokens → (on error) error."""
    log.debug("chat request: index_id=%s query=%r messages=%s", index_id, query, messages)

    if _chat_provider == "none":
        yield {"type": "error", "content": _PROMPTS["errors"]["not_configured"]}
        return

    search_query, combined_filter, hist_messages = await _resolve_retrieval_params(query, messages)

    # Cheap pre-check (free regex, no I/O) before attempting device-variant
    # resolution at all — most queries either have no digit or hit an index
    # with no compatible_devices-equivalent field, so this keeps the added
    # cost near zero for the common case. See
    # docs/deviceCompatibilityMatchingDesign.md.
    device_filter: str | None = None
    if _HAS_NUMBER_RE.search(search_query):
        device_field = await _resolve_concept_field(
            index_id, "device_compatibility", superlative_field_map or {},
        )
        if device_field:
            device_filter = await _resolve_device_filter(search_query, index_id, device_field)
            if device_filter:
                log.debug("chat device filter resolved: index_id=%s field=%s filter=%s",
                          index_id, device_field, device_filter)
    combined_filter = _combine_filters(combined_filter, device_filter)

    superlative = _detect_superlative_concept(search_query)
    resolved_field: str | None = None
    resolved_direction = ""
    if superlative:
        matched_phrase, concept, resolved_direction = superlative
        resolved_field = await _resolve_concept_field(
            index_id, concept, superlative_field_map or {},
        )

    if resolved_field:
        stripped_query = _strip_structural_phrases(search_query, matched_phrase)
        log.debug("chat superlative resolved: index_id=%s concept=%s field=%s direction=%s "
                  "query=%r -> stripped=%r",
                  index_id, concept, resolved_field, resolved_direction, search_query, stripped_query)
        cards, context = await _search_context_sorted(
            stripped_query, index_id, resolved_field, resolved_direction,
            query_by, query_by_weights, price_filter=combined_filter,
            superlative_phrase=matched_phrase,
        )
    else:
        cards, context = await _search_context(
            search_query, index_id, query_by, query_by_weights, facet_query_by,
            price_filter=combined_filter,
        )

    if _smart_followup_enabled:
        cards = _merge_cards(cards, _pinned_cards(messages))

    # A resolved superlative already has a precise, deterministic answer —
    # skip the vague-preference disclosure for this turn even if the query
    # also happens to contain a word from vague_preference_terms (e.g.
    # "most popular" matches both "popular" and the superlative concept map).
    is_vague = _is_vague_preference_query(query) and not resolved_field
    has_price = await _index_has_price_field(index_id) if is_vague else True
    system  = _system_prompt(context, note_vague_preference=is_vague, has_price_field=has_price,
                              persona=persona_config,
                              sorted_field=resolved_field, sorted_direction=resolved_direction)
    history = [{"role": m["role"], "content": m.get("content", "")} for m in hist_messages] + \
              [{"role": "user", "content": query}]

    log.debug("chat LLM request: provider=%s model=%s system=%r history=%s",
              _chat_provider, _chat_model, system, history)

    try:
        full_response = ""
        async for chunk in _route_llm_stream(system, history):
            full_response += chunk
            yield {"type": "text", "content": chunk}
        log.debug("chat LLM response: index_id=%s response=%r", index_id, full_response)
    except Exception as exc:
        log.error("chat LLM request failed: index_id=%s", index_id, exc_info=True)
        yield {"type": "products", "products": cards}
        yield {"type": "error", "content": _friendly_error(str(exc))}
        return

    used_cards = _filter_cards_by_response(cards, full_response)
    log.debug("chat response products: kept %d of %d based on LLM text: %s",
              len(used_cards), len(cards),
              [{"id": c.get("id"), "title": c.get("title")} for c in used_cards])
    yield {"type": "products", "products": used_cards}
