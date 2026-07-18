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
import re
from typing import AsyncIterator

from isp.services.infra import typesense_client as ts
from isp.services.infra.embedding_service import embed_query as _embed_query
from isp.services.infra.chat.providers import anthropic_chat, google_chat, openai_compat

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

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
    r'between\s*\$?(\d+(?:\.\d+)?)\s*(?:and|to|-)\s*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)
_PRICE_LT = re.compile(
    r'(?:less than|under|cheaper than|below)\s*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)
_PRICE_GT = re.compile(
    r'(?:more than|over|above|at least)\s*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)


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
    """All words (>2 chars) from titles of every product shown so far."""
    tokens: set[str] = set()
    for m in messages:
        for p in (m.get("products") or []):
            for w in (p.get("title") or "").lower().split():
                if len(w) > 2:
                    tokens.add(w)
    return tokens


def _is_follow_up_short_query(query: str, messages: list[dict]) -> bool:
    words = query.strip().split()
    if len(words) > 5 or not messages:
        return False
    shown = _shown_product_tokens(messages)
    content_words = [w.lower() for w in words if len(w) > 3]
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
    system = (
        "You are a search query optimizer. "
        "Rewrite the current user query as a single standalone search query.\n"
        "Rules:\n"
        "- Resolve pronouns (it, them, those) to the actual product names from history.\n"
        "- For comparisons ('compare X with Y'), you MUST name BOTH sides using exact "
        "product names from history AND the new product/brand. "
        "Example: 'Samsung Galaxy Buds vs Google Pixel Buds Pro2'.\n"
        "- For new topics unrelated to history, return the query unchanged.\n"
        "Output ONLY the rewritten query. No explanation. No prefix. Max 15 words."
    )
    prompt = f"History:\n{context}\n\nCurrent query: {query}\n\nRewritten query:"
    try:
        result = ""
        async for token in _route_llm_stream(system, [{"role": "user", "content": prompt}]):
            result += token
            if len(result) > 200:
                break
        return result.strip() or query
    except Exception:
        return query


def _price_filter_for_smart(query: str, messages: list[dict]) -> str | None:
    """Price filter for smart mode — hard constraint preserved even without ID filter."""
    return _extract_price_filter(query) or _price_filter_from_history(messages)


async def _resolve_retrieval_params(
    query: str, messages: list[dict],
) -> tuple[str, str | None]:
    """
    Return (search_query, filter_by) for the Typesense call.

    Smart ON : LLM-rewritten query + price-only filter (no ID pinning).
    Smart OFF: original query + heuristic filter (ID + price if follow-up).
    """
    if _smart_followup_enabled:
        search_query = await _llm_rewrite_query(query, messages)
        return search_query, _price_filter_for_smart(query, messages)
    is_fu = _is_follow_up_heuristic(query, messages)
    search_query = query if is_fu else _normalize_query(query)
    return search_query, _resolve_retrieval_filter(query, messages, is_fu)


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
            pass

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
    vec    = await _embed_query(query)
    col    = ts.collection_name(index_id)
    client = ts.get_client()

    searches = _build_search_params(
        query, col, vec, query_by, query_by_weights, facet_query_by, price_filter,
    )
    resp = await asyncio.to_thread(
        lambda: client.multi_search.perform({"searches": searches}, {})
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


# ── LLM streaming ──────────────────────────────────────────────────────────────

def _system_prompt(context: str) -> str:
    return (
        "You are a helpful product expert assistant. "
        "Answer questions using ONLY the structured product data listed below — "
        "do NOT rely on general knowledge about these products. "
        "Each product has labelled attributes; use those exact values when answering "
        "attribute-specific questions. "
        "Be concise — 2 to 3 sentences maximum. "
        "If no product matches the question, say so honestly.\n\n"
        f"Products:\n{context}"
    )


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
    if "rate_limit" in low or "rate limit" in low or "429" in raw:
        return "The AI provider is temporarily rate-limited. Please wait a moment and try again, or switch to a different model in Settings → Chat."
    if "401" in raw or "authentication" in low or "api key" in low or "invalid_api_key" in low:
        return "Invalid API key. Please update your key in Settings → Chat."
    if "503" in raw or "overloaded" in low or "unavailable" in low:
        return "The AI provider is temporarily unavailable. Please try again in a moment."
    if "context_length" in low or "too long" in low or "max_tokens" in low:
        return "The conversation is too long for this model. Please start a new chat."
    return "Something went wrong with the AI provider. Please try again."


# ── Public entry point ─────────────────────────────────────────────────────────

async def chat_stream(
    query: str,
    messages: list[dict],
    index_id: str,
    query_by: str = "title,content",
    query_by_weights: str | None = None,
    facet_query_by: str | None = None,
) -> AsyncIterator[dict]:
    """Yield SSE-ready dicts: products → text tokens → (on error) error."""
    if _chat_provider == "none":
        yield {"type": "error", "content": "Chat is not configured. Go to Settings → Chat to choose a provider."}
        return

    search_query, combined_filter = await _resolve_retrieval_params(query, messages)

    cards, context = await _search_context(
        search_query, index_id, query_by, query_by_weights, facet_query_by,
        price_filter=combined_filter,
    )

    if _smart_followup_enabled:
        cards = _merge_cards(cards, _pinned_cards(messages))

    yield {"type": "products", "products": cards}

    system  = _system_prompt(context)
    history = [{"role": m["role"], "content": m.get("content", "")} for m in messages] + \
              [{"role": "user", "content": query}]

    try:
        async for chunk in _route_llm_stream(system, history):
            yield {"type": "text", "content": chunk}
    except Exception as exc:
        yield {"type": "error", "content": _friendly_error(str(exc))}
