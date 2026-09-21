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
from isp.services.infra import embedding_service
from isp.services.infra.embedding_service import embed_query as _embed_query
from isp.services.infra.chat.providers import anthropic_chat, google_chat, openai_compat
from isp.services.domain import index_service
from isp.services.domain import chat_intent_split
from isp.services.domain import search_relevance

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# All LLM-facing prompt text and user-facing chat copy lives in prompts.json,
# not inline in code — keeps wording changes a data edit, not a code change,
# and is the basis for making it operator-configurable per index later.
_PROMPTS: dict = json.loads((Path(__file__).with_name("prompts.json")).read_text(encoding="utf-8"))

# concept -> candidate field-name substrings (tried in order; exact match wins
# first) for _resolve_concept_field's heuristic fallback — same
# externalize-as-data reasoning as _PROMPTS above. This is the FALLBACK path
# only: an index that has actually been configured for a concept declares it
# explicitly in superlative_field_map (checked first, see
# _resolve_concept_field), so a differently-named field on some other index
# just needs that one declaration, not an edit here. See
# plans_transformer.js for where "customer_group" itself comes from on the
# plans index, and docs/superlativePriceDesign.md for the full design.
_CONCEPT_FIELD_CANDIDATES: dict[str, list[str]] = json.loads(
    (Path(__file__).with_name("concept_fields.json")).read_text(encoding="utf-8")
)

# Defaults applied whenever an index's chat_persona_config doesn't set a given key —
# {} behaves identically to the fixed prompt this replaced. Imported by routers/indexes.py
# so the GET /chat-persona endpoint returns the same effective values chat_stream uses.
# Lives in prompts.json ("persona_defaults") alongside the rest of the
# operator-tunable chat wording/vocabulary, not inline here.
CHAT_PERSONA_DEFAULTS: dict = _PROMPTS["persona_defaults"]

_chat_provider:          str  = "none"
_chat_model:             str  = ""
_chat_api_key:           str  = ""
_chat_endpoint:          str  = ""
_smart_followup_enabled: bool = False
# See docs/multiIntentConjunctionSearchDesign.md. Off by default — opt-in,
# same convention as smart_followup.
_multi_intent_split_enabled: bool = False

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
    multi_intent_split: bool = False,
) -> None:
    global _chat_provider, _chat_model, _chat_api_key, _chat_endpoint
    global _smart_followup_enabled, _multi_intent_split_enabled
    _chat_provider              = provider
    _chat_model                 = model
    _chat_api_key               = api_key
    _chat_endpoint              = endpoint
    _smart_followup_enabled     = smart_followup
    _multi_intent_split_enabled = multi_intent_split
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


def _remove_price_phrase(text: str) -> str:
    """
    Unconditionally remove a matched price-range/comparison phrase and
    collapse the resulting whitespace — no fallback here; callers decide
    what an empty result should become (see _strip_price_phrase and
    _strip_structural_phrases, which want different fallbacks and would
    fight each other if this helper picked one itself).
    """
    text = _PRICE_BETWEEN.sub('', text)
    text = _PRICE_LT.sub('', text)
    text = _PRICE_GT.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def _strip_price_phrase(text: str) -> str:
    """
    Remove a matched price-range/comparison phrase from text before it's
    used as Typesense's lexical `q` — the phrase is already captured
    structurally by `_extract_price_filter` as a `filter_by` clause, so
    leaving it in the search text only adds tokens ("under", "$1000") that
    don't appear in any product's actual text. Verified directly: "cheapest
    iphone" matches 8 documents; "cheapest iphone under $800" matches 0,
    purely from the extra unmatchable tokens (see _strip_structural_phrases,
    which shares _remove_price_phrase with this function).

    Falls back to the original text if stripping would leave nothing (e.g.
    the query was just "under $1000" with no subject) — an empty string
    passed as `q` is worse than the unstripped original.
    """
    return _remove_price_phrase(text) or text


def _strip_matched_phrase(text: str, phrase: str) -> str:
    """
    Remove one already-matched literal phrase from lexical search text —
    generic counterpart to _strip_price_phrase for filters whose match is a
    single known substring rather than a regex pattern (e.g. the customer-
    eligibility trigger word already captured as a filter_by clause). Falls
    back to the original text if stripping would leave nothing, same
    self-preserving reasoning as _strip_price_phrase.
    """
    stripped = re.sub(re.escape(phrase), '', text, flags=re.IGNORECASE)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    return stripped or text


_RELATIVE_TERM_RE = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in _PROMPTS["vague_preference_terms"]) + r')\b',
    re.IGNORECASE,
)

_HAS_NUMBER_RE = re.compile(r'\d')

# Customer-eligibility-group values, per index — mirrors _category_vectors_cache
# just below (same rationale: a small, closed, per-index vocabulary, cached for
# the process lifetime rather than re-fetched every request). Deliberately NOT
# a hardcoded English word list: whatever values this index's data actually
# holds for the resolved field ARE the only vocabulary ever checked, so a
# differently-worded index (or one relabelled after a reindex, once the
# process restarts) is picked up automatically — no code change here.
_ELIGIBILITY_MAX_VALUES = 20
_eligibility_values_cache: dict[str, list[str]] = {}


async def _eligibility_values_for_index(index_id: str, field_name: str) -> list[str]:
    """Real, distinct values `field_name` holds on this index — cached per index."""
    cache_key = f"{index_id}:{field_name}"
    if cache_key in _eligibility_values_cache:
        return _eligibility_values_cache[cache_key]
    facets = await asyncio.to_thread(ts.get_facets, index_id, [field_name])
    values = [c["value"] for c in facets.get(field_name, []) if c.get("value")][:_ELIGIBILITY_MAX_VALUES]
    _eligibility_values_cache[cache_key] = values
    return values


def _match_eligibility_value(query: str, values: list[str]) -> tuple[str, str] | None:
    """
    Find one of `values` mentioned literally in `query`. Returns
    (matched_text, real_value) — matched_text is what to strip from the
    lexical search text, real_value is what to filter on (they differ only
    when a value like "first_responder" is checked against its natural
    written form "first responder"). No synonym guessing beyond that
    underscore/space normalization — a value the query names some other way
    entirely (e.g. "senior" for a value actually stored as "55+") is a
    genuine miss, same as an unrecognized device name falls through to
    unfiltered retrieval rather than a wrong guess.

    Candidates are tried longest-first across ALL values, not in `values`'s
    own order (that order comes straight from Typesense facet_counts, i.e.
    count-descending — so the broader value is typically listed first). A
    query like "consumer plus plan" against values ["consumer", "consumer
    plus"] must resolve to "consumer plus": the word-boundary check after
    "consumer" is satisfied by the following space, so checking values in
    facet order would match the broader "consumer" first and never even try
    the more specific value the query actually names.
    """
    low = query.lower()
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for v in values:
        for candidate in (v.lower(), v.lower().replace('_', ' ')):
            if candidate not in seen:
                seen.add(candidate)
                candidates.append((candidate, v))
    candidates.sort(key=lambda pair: -len(pair[0]))
    for candidate, v in candidates:
        m = re.search(r'(?<![A-Za-z0-9])' + re.escape(candidate) + r'(?![A-Za-z0-9])', low)
        if m:
            return query[m.start():m.end()], v
    return None


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


# ── Multi-intent conjunction splitting ──────────────────────────────────────
# See docs/multiIntentConjunctionSearchDesign.md. The actual pipeline lives in
# the decoupled isp.services.domain.chat_intent_split module; everything here
# is just the I/O this chat service injects into it (Typesense facets, the
# embedding model, the configured LLM provider).
#
# V1 (§15.1 of the design doc): the category field is detected at request
# time (a faceted field literally named "category" with a small, bounded
# value count) rather than the persisted, auto-discovered, per-index config
# the fuller design describes (§8/§8.1) — a smaller first cut behind one
# global toggle. Category-value embeddings are cached in memory per index,
# computed lazily on first use (a documented simplification of the design's
# precompute-only lifecycle — acceptable cold-start cost for a first version).

_CATEGORY_FIELD_NAME = "category"
_CATEGORY_MAX_VALUES = 20
# A catch-all bucket has no coherent semantic content of its own, so its
# embedding sits ambiguously close to many unrelated segments (observed:
# "others" out-scored "cases-&-covers" for the segment "cases"). Excluding
# it from the classification vocabulary removes that noise; it was never a
# category a customer could ask for by name anyway. Lives in prompts.json
# ("generic_category_values") alongside vague_preference_terms — the same
# kind of curated English vocabulary list, not schema-dependent.
_GENERIC_CATEGORY_VALUES = set(_PROMPTS["generic_category_values"])

_category_vectors_cache: dict[str, tuple[dict[str, list[float]], dict[str, str]]] = {}


def _display_label(value: str) -> str:
    return value.replace("-", " ").replace("&", "and").title()


async def _category_vectors_for_index(index_id: str) -> tuple[dict[str, list[float]], dict[str, str]] | None:
    """
    Returns (category -> embedding, category -> display label) for this
    index's category-like field, or None if it doesn't have one shaped like
    a small, closed vocabulary. Cached per index for the process lifetime.
    """
    if index_id in _category_vectors_cache:
        return _category_vectors_cache[index_id]

    facet_fields = await asyncio.to_thread(ts.get_facet_fields, index_id)
    if _CATEGORY_FIELD_NAME not in facet_fields:
        return None
    facets = await asyncio.to_thread(ts.get_facets, index_id, [_CATEGORY_FIELD_NAME])
    all_values = [c["value"] for c in facets.get(_CATEGORY_FIELD_NAME, []) if c.get("value")]
    if not (1 < len(all_values) <= _CATEGORY_MAX_VALUES):
        return None
    values = [v for v in all_values if v.strip().lower() not in _GENERIC_CATEGORY_VALUES]
    if len(values) < 2:
        return None

    vectors: dict[str, list[float]] = {}
    for v in values:
        # Reuse _display_label's cleaning (not a second, slightly different
        # version of the same replace chain) — the two had drifted apart:
        # this one used to replace "&" with " and " (spaces on both sides),
        # which doubled up with the space already left by the "-" replace
        # just before it ("cases-&-covers" -> "cases  and  covers"), a
        # measurable embedding-quality hit versus the single-spaced text
        # _display_label already produces correctly.
        vec = await _embed_query(_display_label(v).lower())
        if vec:
            vectors[v] = vec
    if len(vectors) < 2:
        return None

    labels = {v: _display_label(v) for v in values}
    result = (vectors, labels)
    _category_vectors_cache[index_id] = result
    return result


def invalidate_index_caches(index_id: str) -> None:
    """
    Drop this index's cached chat-retrieval resources — called by
    index_service.set_status() whenever an ingest/rebuild completes.

    _category_vectors_cache and _eligibility_values_cache are both populated
    once and kept for the process lifetime (see their own comments above) —
    without this, a rebuild that adds a category, drops one, or changes
    which customer-eligibility values exist leaves chat working off the
    pre-rebuild snapshot until the process happens to restart: a genuinely
    new eligibility group ("veteran") would never resolve, and a removed
    category would keep splitting into a bucket that no longer exists.
    superlative_field_map's write-through cache needs no clearing here — it
    lives in Postgres, keyed by concept -> field name, not by the catalog's
    contents, so a rebuild doesn't invalidate it.
    """
    _category_vectors_cache.pop(index_id, None)
    for key in [k for k in _eligibility_values_cache if k.startswith(f"{index_id}:")]:
        del _eligibility_values_cache[key]


def clear_all_category_vectors() -> None:
    """
    Drop every index's cached category-classification vectors — called by
    embedding_service.set_current_model() whenever the active embedding
    model actually changes.

    _category_vectors_for_index embeds each category's display label with
    whatever model is globally active at the time it's first resolved, with
    no per-request model override, and is cached per index for the process
    lifetime (keyed by index id only, not by model). Left uncleared across a
    model switch, a post-switch query's segment vector (new model) would get
    compared, via cosine similarity in chat_intent_split.classify_segments,
    against a stale category vector embedded by the OLD model —
    differing-dimension pairs are silently truncated by zip() inside that
    cosine calculation rather than raising, and even two models with the
    same output dimension don't share a vector space, so the comparison is
    meaningless either way. Global (not scoped to one index) because every
    cached entry, across every index, shares this same "embedded with
    whatever was the global model at cache time" exposure.
    """
    _category_vectors_cache.clear()


async def _confirm_intents(system: str, prompt: str) -> str:
    """One-shot (non-streaming) LLM call for the confirmation step, §7.
    Reuses the already-configured chat provider — no separate model/key."""
    result = ""
    async for token in _route_llm_stream(system, [{"role": "user", "content": prompt}]):
        result += token
        if len(result) > 500:
            break
    return result


async def _try_multi_intent_context(
    search_query: str, index_id: str,
    query_by: str, query_by_weights: str | None, facet_query_by: str | None,
    global_filter: str | None, device_filter: str | None, device_field: str | None = None,
) -> tuple[list[dict], str] | None:
    """
    Attempt the multi-intent split (§3-§11). Returns None on any gate miss
    or failure — the caller falls back to the single _search_context call
    exactly as it did before this feature existed; nothing here can make a
    chat response worse than the pre-existing behavior, only better.

    `global_filter` is ANDed onto every plan's own filter unconditionally —
    reserve it for constraints that are genuinely index-wide (e.g. the
    customer-eligibility filter), never for a price/quantity extracted from
    the whole message, which belongs to whichever single segment it was
    actually written in (see _run's own comment on this).

    Note-worthy v1 simplification: `category` is observed to be a
    single-value string field on every index checked (phones, accessories,
    plants), never a multi-value array, so a product cannot land in two
    category buckets at once — the design's `deduped_empty` bucket state
    doesn't arise for this shape and isn't implemented here.
    """
    if not _multi_intent_split_enabled:
        log.debug("chat multi-intent split: skipped — toggle disabled")
        return None
    resources = await _category_vectors_for_index(index_id)
    if resources is None:
        log.debug("chat multi-intent split: skipped — no category vectors for index_id=%s", index_id)
        return None
    category_vectors, labels = resources
    log.debug("chat multi-intent split: category_vectors=%s query=%r", list(category_vectors.keys()), search_query)

    # device_filter is resolved once against the *whole* message before this
    # function is ever called (see chat_stream). That single-shot resolution
    # has a real gap: Typesense's own token-dropping is positional — a real
    # device name ("iphone 17") sitting mid-sentence in a long, multi-category
    # message can fail to resolve at all even though the same text resolves
    # cleanly on its own (observed: "headphones , iphone 17 case and screen
    # protector" -> 0 hits against compatible_devices; "iphone 17 case and
    # screen protector" alone -> resolves fine). Segments are short enough
    # that this positional failure doesn't reproduce, so when the
    # whole-message resolution came back empty, retry per segment before
    # giving up on device context entirely.
    #
    # NOTE: this whole-message-resolved device_filter is used below ONLY to
    # derive strip_phrases (classification-time text dilution) — it must
    # never become the actual per-plan retrieval filter. A single device name
    # resolved from (or first found in) the whole message is not necessarily
    # every plan's device: "iPhone 17 case and Galaxy S25 screen protector"
    # would otherwise search both categories against whichever device won
    # this one resolution. Each plan resolves its own device filter from its
    # own plan.query below instead (which already carries the right device
    # text — either the segment's own mention, or a genuinely shared span
    # validated by resolve_span — see build_search_plans).
    if device_filter is None and device_field is not None:
        for seg in chat_intent_split.split_segments(search_query):
            seg_filter = await _resolve_field_value_filter(seg, index_id, device_field)
            if seg_filter:
                device_filter = seg_filter
                log.debug("chat multi-intent split: device filter resolved per-segment "
                          "(whole-message resolution failed): segment=%r filter=%s", seg, seg_filter)
                break

    # A resolved device name (e.g. "iPhone 17") sitting inside a segment
    # measurably dilutes its classification embedding away from the actual
    # category concept — "iphone 17 case" scores far weaker against
    # "cases and covers" than bare "case" does. Reuse whichever device name
    # was resolved above rather than re-detecting it per segment again here.
    # Stripped for classification only; build_search_plans still uses the
    # untouched segment text, so the final per-category query stays
    # device-specific.
    strip_phrases = re.findall(r'`([^`]+)`', device_filter) if device_filter else None

    async def _resolve_span(span: str) -> bool:
        return await _resolve_span_for_index(index_id, device_field, span)

    plans = await chat_intent_split.maybe_build_plans(
        search_query,
        category_vectors=category_vectors,
        label_by_category=labels,
        embed=_embed_query,
        confirm=_confirm_intents,
        constraint_extractor=_extract_price_filter,
        strip_phrases=strip_phrases,
        resolve_span=_resolve_span,
    )
    log.debug("chat multi-intent split: plans=%s", plans)
    if not plans:
        return None

    async def _run(plan: chat_intent_split.SearchPlan):
        # global_filter is whatever legitimately applies to every intent
        # regardless of category (currently: customer-eligibility — a
        # military/consumer/etc. plan restriction is index-wide, not a
        # per-segment modifier). The whole-message PRICE filter is
        # deliberately excluded here: plan.filter_by already carries this
        # plan's own price constraint, extracted from its own segment text
        # by build_search_plans's constraint_extractor — never from the
        # whole message. AND-ing a second, whole-message-derived price
        # filter on top double-applies it, and for "cases under $20 and
        # headphones over $100" would give the headphones plan both
        # price:<20 (from the whole message, matching the OTHER plan's
        # constraint) and its own price:>100 — a guaranteed-empty
        # contradiction. This mirrors chat_intent_split's own shared_span
        # rule: a numeric/price value is never treated as a legitimate
        # cross-segment modifier (see parse_and_validate_confirmation).
        base_filter = _combine_filters(global_filter, plan.filter_by)

        # Device filter is resolved fresh per plan from plan.query (not the
        # single whole-message device_filter above) — see the NOTE where
        # that whole-message value is computed. plan.query already carries
        # the right device text for this specific intent.
        plan_device_filter = (
            await _resolve_field_value_filter(plan.query, index_id, device_field)
            if device_field else None
        )
        filter_by = _combine_filters(base_filter, plan_device_filter)
        try:
            cards, context = await _search_context(
                plan.query, index_id, query_by, query_by_weights, facet_query_by,
                price_filter=filter_by,
            )
            if not cards and plan_device_filter:
                # This plan's own device filter applies well to
                # device-specific categories (cases, screen protectors), but
                # a category like headphones is often sold as a universal
                # accessory with no per-device tagging at all — AND-ing the
                # filter in then wrongly zeroes it out. Retry without it —
                # but only when this category is actually untagged by
                # device in this catalog. Retrying unconditionally would
                # turn a genuine incompatibility (a real Galaxy S25 case
                # search that legitimately has zero matches) into generic,
                # possibly wrong-brand results silently presented as if
                # they fit — worse than reporting no match at all.
                if not await _category_has_device_values(index_id, f"category:=[{plan.category}]", device_field):
                    cards, context = await _search_context(
                        plan.query, index_id, query_by, query_by_weights, facet_query_by,
                        price_filter=base_filter,
                    )
            return plan, cards, context, None
        except Exception as exc:
            return plan, [], "", exc

    results = await asyncio.gather(*(_run(p) for p in plans))

    all_cards: list[dict] = []
    sections: list[str] = []
    for plan, cards, context, err in results:
        capped = cards[:plan.top_n]
        # Stamped here, not in _hit_to_card_and_block — a card's bucket label
        # is a property of which plan searched for it, not of the document
        # itself. Lets the frontend group cards into one carousel per
        # confirmed intent instead of one flat list (§10 of the multi-intent
        # design doc) without parsing the prose "## Label" headers.
        for card in capped:
            card["label"] = plan.label
        all_cards.extend(capped)
        if err is not None:
            sections.append(
                f"## {plan.label}\n"
                f"(The search for {plan.label} could not be completed right now — "
                f"mention this as a temporary issue, never as a lack of matching products.)"
            )
        elif not capped:
            sections.append(
                f"## {plan.label}\n"
                f"(No matching {plan.label} found for this request — say you couldn't "
                f"find a match; never imply the catalog doesn't carry {plan.label}.)"
            )
        else:
            block_texts = context.split("\n\n")[:plan.top_n]
            sections.append(f"## {plan.label}\n" + "\n\n".join(block_texts))

    log.debug("chat multi-intent split: index_id=%s query=%r categories=%s",
              index_id, search_query, [p.category for p in plans])
    # No combined-total cap here — each bucket already capped itself
    # independently at plan.top_n (== GLOBAL_CARD_CAP) above. Re-slicing the
    # merged list to :5 would silently claw back the per-bucket budget this
    # function just gave each intent (e.g. 3 confirmed intents at 5 each
    # would collapse back to 5 total, the exact shared-total behavior this
    # was changed away from).
    return all_cards, "\n\n".join(sections)


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

# phrase -> [concept, sort direction] — English fixes this vocabulary, not any
# particular catalog, so it's shared across every index; externalized for the
# same reason as _PROMPTS/_CONCEPT_FIELD_CANDIDATES above (a wording/vocab
# change is a data edit, not a code change).
_SUPERLATIVE_CONCEPT_MAP: dict[str, list[str]] = json.loads(
    (Path(__file__).with_name("superlative_phrases.json")).read_text(encoding="utf-8")
)

_SORTABLE_TYPES = {"int32", "int64", "float"}

# Concepts driven by a superlative phrase ("cheapest", "most popular", ...)
# need a numeric field to sort by — derived from _SUPERLATIVE_CONCEPT_MAP's
# own concept column rather than a second, hand-maintained list, so a new
# superlative concept added there automatically counts as sortable-shaped
# here too. Every other known concept (device_compatibility,
# customer_eligibility) is a filter concept instead: it needs a faceted
# string field holding real values to match against, not a number to sort
# by. Used by routers/indexes.py to validate a concept mapping against the
# field's actual shape — mirrors FieldsTab.svelte's client-side
# SORT_CONCEPTS/candidateFieldsFor split, enforced here too since a direct
# API call (or a later, unrelated PATCH that just flips a field's facet or
# type) isn't gated by what the UI happens to offer.
_SORT_CONCEPTS = {concept for concept, _direction in _SUPERLATIVE_CONCEPT_MAP.values()}
_FILTER_CONCEPT_VALUE_TYPES = {"string", "string[]"}


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
    captured structurally (sort_by / filter_by) — see _remove_price_phrase
    for the price half and the measured effect of leaving such phrases in.
    Falls back to "*" (browse-all) if stripping would leave nothing (e.g.
    the query was just "cheapest" with no subject) — unlike
    _strip_price_phrase's own fallback, an empty superlative query has no
    real subject worth falling back to, so this uses the unconditional
    _remove_price_phrase rather than _strip_price_phrase's self-preserving
    one (composing the two would let a price-only remainder like "under
    $800" survive instead of correctly falling through to "*").
    """
    text = re.sub(re.escape(superlative_phrase), '', query, flags=re.IGNORECASE)
    text = _remove_price_phrase(text)
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


async def _resolve_field_value_filter(query: str, index_id: str, field_name: str) -> str | None:
    """
    Two-step value resolution against an arbitrary field on this index —
    originally built for device-variant matching (see
    docs/deviceCompatibilityMatchingDesign.md) but not device-specific: takes
    whichever field_name the caller resolved for its own concept (compatible
    devices, customer eligibility group, ...) and never hardcodes what values
    that field can hold, so it needs no per-index, per-concept synonym table.

    Step 1: let Typesense's own typo tolerance resolve messy input
    ("ihpone17 promax", "milatary") against the REAL values this field
    actually holds on THIS index — no new fuzzy-matching code of our own, and
    no assumption about what those values look like. Step 2: use the top
    hit's actual value, not the user's raw text, to build a precise
    exact-match filter, so a query naming one specific variant doesn't get
    blended with lexically-similar sibling variants ("iPhone 17" vs "iPhone
    17 Pro Max"). Returns None when there's no confident match (e.g. a
    generic query that doesn't name a value this field has), so the caller
    falls through to normal, unfiltered retrieval rather than forcing a
    wrong narrowing.
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


async def _category_has_device_values(index_id: str, category_filter: str, device_field: str) -> bool:
    """
    True if any document matching `category_filter` (a Typesense filter_by
    expression, e.g. "category:=[cases]") has a non-empty `device_field`.

    Used to decide whether a plan's zero-result device-filtered search is a
    real "we don't carry a compatible one" (this category IS device-scoped —
    every real case/screen-protector names its compatible device) versus a
    false negative from AND-ing a device filter onto a category this catalog
    just doesn't tag by device at all (headphones, chargers sold as
    universal accessories). No hardcoded category list either way — whether
    THIS catalog actually populates the field for THIS category is the only
    signal used, the same "ask the data, never guess the vocabulary" pattern
    as _eligibility_values_for_index and _category_vectors_for_index.
    """
    col    = ts.collection_name(index_id)
    client = ts.get_client()
    try:
        resp = await asyncio.to_thread(
            lambda: client.multi_search.perform({"searches": [{
                "collection": col, "q": "*", "query_by": "title",
                "filter_by": category_filter, "facet_by": device_field,
                "per_page": "0", "max_facet_values": "1",
            }]}, {})
        )
    except Exception:
        # Fail closed: if we can't tell whether this category is
        # device-scoped, don't gamble on it being universal — the retry
        # this gates is what would surface a possibly-incompatible product.
        return True
    facet_counts = resp.get("results", [{}])[0].get("facet_counts", [])
    return any(fc.get("counts") for fc in facet_counts if fc.get("field_name") == device_field)


async def _resolve_span_for_index(index_id: str, device_field: str | None, span: str) -> bool:
    """
    `chat_intent_split`'s `resolve_span` injection (see
    docs/multiIntentConjunctionSearchDesign.md §7's Tier 2 validation) —
    answers "does `span` name something this index actually recognizes?"
    for a candidate `shared_span`, independent of whatever the confirmation
    call claims.

    Reuses the same device-compatibility field already resolved once per
    request for the primary device filter (`device_field`, from
    `_resolve_concept_field`) and the same Typesense-backed resolver
    (`_resolve_field_value_filter`) — no new matching logic, no new extension.

    An index with no resolvable device-compatibility field at all
    (`device_field is None`) has no vocabulary to check a claim against, so
    a shared_span is rejected unconditionally there — this function never
    even calls Typesense in that case.
    """
    if device_field is None:
        return False
    return bool(await _resolve_field_value_filter(span, index_id, device_field))


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
        search_query = _strip_price_phrase(await _llm_rewrite_query(query, messages))
        return search_query, _price_filter_for_smart(query, messages), messages
    is_fu = _is_follow_up_heuristic(query, messages)
    search_query = _strip_price_phrase(query if is_fu else _normalize_query(query))
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


# Relevance floors (dropping weak lexical/semantic hits before RRF merge) live
# in search_relevance.py, shared with typesense_client.search_with_vec — the
# equivalent merge used by the listing page's search. See that module for the
# full rationale and calibration notes.
def _rrf_merge(
    results_list: list[dict], has_struct: bool, has_vec: bool, query: str = "",
    embed_model: str | None = None,
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
        return lex_hits[:5]

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
    }
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
        query=query,
        # _embed_query above was called with no model override, so it always
        # embeds with whatever the global current model is — that's the
        # model these vectors (and thus vector_distance) actually came from.
        embed_model=embedding_service.get_current_model(),
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

_TRAILING_LINE_COUNT_RE = re.compile(r'\s*\(\d+(?:[–-]\d+)?\s+lines?\)\s*$', re.IGNORECASE)


def _matchable_title(title: str) -> str:
    """
    Strip a trailing "(N lines)"/"(N–M lines)" annotation before matching a
    card's title against the LLM's answer text. plans_transformer.js appends
    that range to the CARD's title so the carousel shows it, but a natural-
    language answer never reproduces it verbatim ("...is the Essentials 4
    Line Offer 2.0, which costs $125/mo for 5 lines" — not "...Offer 2.0 (4–6
    lines), which..."). Reproduced live: every plans card's full title failed
    to match for every query tried, so _filter_cards_by_response's
    "no match found" fallback fired every time, silently showing all 5
    unfiltered candidates regardless of which one the model actually
    discussed. A no-op for any other index — no other title has this shape.
    """
    return _TRAILING_LINE_COUNT_RE.sub('', title)


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

    That fallback runs independently PER multi-intent bucket, not once over the
    whole list. _try_multi_intent_context stamps card["label"] = plan.label on
    every card (cards outside multi-intent have no "label" key — they all share
    the one None group here, which reproduces the original single-list
    behavior exactly). Filtering the merged list as one unit let a match in one
    bucket license dropping an entire OTHER bucket: the answer might name one
    headphones product by exact title while only describing the case results in
    prose, and the old single "any match at all -> don't fall back" rule then
    discarded every case card even though the case section of the answer was
    perfectly legitimate, just not verbatim.
    """
    if not response_text:
        return cards
    low = response_text.lower()

    groups: dict[object, list[dict]] = {}
    order: list[object] = []
    for c in cards:
        key = c.get("label")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    result: list[dict] = []
    for key in order:
        group = groups[key]
        claimed = bytearray(len(low))
        used_ids: set[int] = set()
        for c in sorted(group, key=lambda c: -len(_matchable_title(c.get("title") or ""))):
            title = _matchable_title(c.get("title") or "")
            if not title:
                continue
            pattern = r'(?<![A-Za-z0-9])' + re.escape(title.lower()) + r'(?![A-Za-z0-9])'
            for m in re.finditer(pattern, low):
                if not any(claimed[m.start():m.end()]):
                    used_ids.add(id(c))
                    claimed[m.start():m.end()] = b'\x01' * (m.end() - m.start())
                    break
        used = [c for c in group if id(c) in used_ids]
        result.extend(used or group)
    return result


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
    # Applies whenever real context was retrieved, independent of the
    # multi-intent split toggle (docs/multiIntentConjunctionSearchDesign.md
    # §2) — every miss/fallback in that feature still returns to this same
    # prompt, so the guardrail against confident false-unavailability claims
    # has to live here, not only in the split path.
    #
    # Gated on non-empty context on purpose: this note tells the model the
    # sample below isn't exhaustive, which is only true when a sample was
    # actually retrieved. With truly empty context (every candidate was
    # correctly filtered out as irrelevant — see _rrf_merge's relevance
    # floors), the note instead licensed the model to avoid saying "we don't
    # have that" by fabricating specific plausible-sounding products from
    # general knowledge — a worse failure than the one it was added to fix.
    # The base prompt's own "if no {item_noun} matches, say so" instruction
    # is the correct behavior for a genuinely empty context — but that alone
    # was not a strong enough deterrent in practice: with the data section
    # blank, the model would still fabricate specific, plausible-sounding
    # products from general knowledge rather than say it found nothing.
    # empty_context_note makes the "don't invent one" instruction explicit
    # for exactly this case.
    if context:
        prompt += _PROMPTS["system_prompt"]["context_not_exhaustive_note"]
    else:
        prompt += _PROMPTS["system_prompt"]["empty_context_note"].format(item_noun=item_noun)
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


async def validate_chat_model(provider: str, model: str, api_key: str, endpoint: str = "") -> None:
    """
    Check a chat-model choice against the provider's own live catalog
    before it's saved, so a deprecated/retired model is caught at Settings
    save time instead of failing silently in front of a customer at chat
    time — see docs/training.html for the incident this closes (Groq and
    Google both retired the previously-configured model out from under a
    live deployment).

    Raises ValueError with a message suitable for a 400 response when the
    model isn't in the provider's current catalog. "none" and "self_hosted"
    are not validated: "none" has no model to check, and self-hosted
    endpoints (Ollama / vLLM / LocalAI) vary too much in whether they even
    implement a models-list route to make this a hard requirement — a
    self-hosted misconfiguration still surfaces at chat time, same as
    before this change.
    """
    if provider in ("none", "self_hosted"):
        return
    try:
        if provider == "groq":
            available = await openai_compat.list_models(api_key, base_url=_GROQ_BASE_URL)
        elif provider == "openai":
            available = await openai_compat.list_models(api_key)
        elif provider == "google":
            available = await google_chat.list_models(api_key)
        elif provider == "anthropic":
            available = await anthropic_chat.list_models(api_key)
        else:
            return
    except Exception as exc:
        # Can't reach the provider's catalog (bad key, network issue, provider
        # outage) — don't block the save on an availability check that isn't
        # itself available. The actual key/model still gets exercised for
        # real on the first chat request either way.
        log.warning("chat model validation skipped for provider=%s: %s", provider, exc)
        return
    if model not in available:
        raise ValueError(
            f"Model '{model}' was not found in {provider}'s current catalog for this API key. "
            f"It may have been deprecated or retired. Available models include: "
            f"{', '.join(available[:8]) or '(none returned)'}"
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

    # Customer-eligibility-group filter — e.g. "cheapest consumer plan" should
    # never surface a military-only plan just because it's cheaper overall.
    # Field name: explicit indexing-time declaration first (superlative_field_map),
    # heuristic name-match fallback otherwise — same as every other concept.
    # Value: matched directly against this index's real, cached facet values
    # (_eligibility_values_for_index) — no hardcoded vocabulary, so a
    # differently-worded index just works. Both lookups are cached after the
    # first call (see _resolve_concept_field, _eligibility_values_for_index),
    # so — like the multi-intent category-vectors cache this mirrors — there's
    # no separate cheap-regex gate needed before attempting this on every query.
    eligibility_filter: str | None = None
    eligibility_field = await _resolve_concept_field(
        index_id, "customer_eligibility", superlative_field_map or {},
    )
    if eligibility_field:
        values = await _eligibility_values_for_index(index_id, eligibility_field)
        matched = _match_eligibility_value(search_query, values) if values else None
        if matched:
            matched_text, real_value = matched
            eligibility_filter = f"{eligibility_field}:=[`{real_value}`]"
            log.debug("chat eligibility filter resolved: index_id=%s field=%s filter=%s",
                      index_id, eligibility_field, eligibility_filter)
            search_query = _strip_matched_phrase(search_query, matched_text)
    combined_filter = _combine_filters(combined_filter, eligibility_filter)

    # Cheap pre-check (free regex, no I/O) before attempting device-variant
    # resolution at all — most queries either have no digit or hit an index
    # with no compatible_devices-equivalent field, so this keeps the added
    # cost near zero for the common case. See
    # docs/deviceCompatibilityMatchingDesign.md.
    device_filter: str | None = None
    device_field: str | None = None
    if _HAS_NUMBER_RE.search(search_query):
        device_field = await _resolve_concept_field(
            index_id, "device_compatibility", superlative_field_map or {},
        )
        if device_field:
            device_filter = await _resolve_field_value_filter(search_query, index_id, device_field)
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

    is_multi_intent = False
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
        multi = await _try_multi_intent_context(
            search_query, index_id, query_by, query_by_weights, facet_query_by,
            eligibility_filter, device_filter, device_field,
        )
        if multi is not None:
            cards, context = multi
            is_multi_intent = True
        else:
            cards, context = await _search_context(
                search_query, index_id, query_by, query_by_weights, facet_query_by,
                price_filter=combined_filter,
            )

    # _merge_cards assumes one flat, already-small (<=5) list — it caps the
    # fresh results to 3 and the combined total to 5, which is exactly the
    # shared-total behavior a multi-intent reply just moved away from (each
    # bucket now independently caps at GLOBAL_CARD_CAP). It would also mix
    # unlabeled pinned cards into a per-category carousel display with
    # nothing to group them under. Pinned-card continuity stays a
    # single-list feature; skip it for a multi-intent reply rather than
    # silently reintroducing the cap this change removed.
    if _smart_followup_enabled and not is_multi_intent:
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
