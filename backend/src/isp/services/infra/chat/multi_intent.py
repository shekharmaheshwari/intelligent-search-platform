"""
Multi-intent conjunction splitting — the service-layer glue between
chat/service.py and the decoupled, catalog-agnostic
isp.services.domain.chat_intent_split module. Everything here is the I/O
that module needs injected (Typesense facets, category-vector embeddings and
their cache) plus running the resulting per-category search plans.

See docs/multiIntentConjunctionSearchDesign.md.

`embed` and `confirm` are accepted as parameters (not imported module-level
functions) rather than this module reaching for chat/service.py's embedding
call or its configured LLM provider directly — the same dependency-injection
pattern chat_intent_split.maybe_build_plans already uses for its own
`embed`/`confirm`/`constraint_extractor`/`resolve_span` arguments, extended
one level up. That keeps this module free of any chat-provider coupling
(chat/service.py owns provider state; this module never needs to).

V1 (§15.1 of the design doc): the category field is detected at request
time (a faceted field literally named "category" with a small, bounded
value count) rather than the persisted, auto-discovered, per-index config
the fuller design describes (§8/§8.1) — a smaller first cut behind one
global toggle. Category-value embeddings are cached in memory per index,
computed lazily on first use (a documented simplification of the design's
precompute-only lifecycle — acceptable cold-start cost for a first version).

Note-worthy v1 simplification: `category` is observed to be a single-value
string field on every index checked (phones, accessories, plants), never a
multi-value array, so a product cannot land in two category buckets at once
— the design's `deduped_empty` bucket state doesn't arise for this shape and
isn't implemented here.
"""
from __future__ import annotations
import asyncio
import logging
import re

from isp.services.infra import typesense_client as ts
from isp.services.domain import chat_intent_split
from isp.services.domain.chat_intent_split import EmbedFn, ConfirmFn
from isp.services.infra.chat import retrieval
from isp.services.infra.chat.query_filters import (
    resolve_field_value_filter, category_has_device_values, combine_filters,
    extract_price_filter, strip_price_phrase,
)
from isp.services.infra.chat.concept_resolution import resolve_span_for_index

log = logging.getLogger(__name__)

# Brand words this catalog's own compatible_devices field config already
# treats as noise (see the Accessories index schema's `stopwords` list) —
# reused here, not invented, to decide whether a resolve_field_value_filter
# result is a genuine device mention or an incidental match on a bare brand
# word alone (the original "apple airpods" bug: resolves to "Apple iPhone
# ..." off "apple" alone, no device actually named). See
# _device_confirmed_in_text.
_BRAND_WORDS = {"apple", "samsung", "google", "motorola", "galaxy"}


def _device_confirmed_in_text(text: str, resolved_filter: str) -> bool:
    """
    True only if every device value resolve_field_value_filter found has at
    least one of its own significant (non-brand) words actually present in
    `text` — e.g. "17" or "iPhone" for "Apple iPhone 17", "S25" for "Samsung
    Galaxy S25". Guards a resolved value against being trusted as a genuine
    mention when the whole-value Typesense match was really just Typesense's
    typo tolerance latching onto a brand word shared with unrelated text
    ("apple airpods" resolving to an Apple iPhone value with no phone model
    named anywhere).
    """
    names = re.findall(r"`([^`]+)`", resolved_filter)
    if not names:
        return False
    text_lower = text.lower()
    for name in names:
        significant = [w for w in re.findall(r"[A-Za-z0-9]+", name) if w.lower() not in _BRAND_WORDS]
        if not significant:
            return False
        if not any(re.search(rf"\b{re.escape(w.lower())}\b", text_lower) for w in significant):
            return False
    return True

# ── Device-fit category gate ─────────────────────────────────────────────
#
# Whether a category's products are meaningfully tied to a specific device
# (a case or screen protector is; headphones or a charger usually isn't) —
# derived from the live catalog's own data, not a hardcoded category-name
# list. Replaces an earlier telecom_nlp/FlashText-vocabulary approach that
# hardcoded both device names and category words for one phone-accessory
# catalog and couldn't see devices (Pixel, Motorola, a new iPhone
# generation) it hadn't been told about by hand. See
# docs/deviceCompatibilityMatchingDesign.md.
_DEVICE_FIT_THRESHOLD = 0.5
_device_fit_cache: dict[str, dict[str, bool]] = {}


async def _is_device_fit_category(index_id: str, category: str, device_field: str) -> bool:
    """
    Whether a plan in this category should have a device filter attempted at
    all — the fraction of THIS category's own documents that actually
    populate device_field, same "ask the data, never guess the vocabulary"
    pattern as query_filters.category_has_device_values and this module's
    own category-vector classification. A category at or under threshold is
    left unfiltered entirely, the same as a catalog with no device concept
    at all (plants, furniture) already is today.

    Cached per (index, category) for the process lifetime; invalidated
    alongside category vectors on every completed ingest/rebuild (see
    clear_device_fit_cache).
    """
    cached = _device_fit_cache.get(index_id, {}).get(category)
    if cached is not None:
        return cached
    _, ratio = await asyncio.to_thread(
        ts.get_field_occurrence_ratio, index_id, f"category:={category}", device_field,
    )
    is_fit = ratio > _DEVICE_FIT_THRESHOLD
    _device_fit_cache.setdefault(index_id, {})[category] = is_fit
    return is_fit


def clear_device_fit_cache(index_id: str) -> None:
    """Drop this index's cached device-fit-category answers — see
    chat/service.py's invalidate_index_caches, called on every completed
    ingest/rebuild alongside clear_category_vectors and
    query_filters.clear_eligibility_values."""
    _device_fit_cache.pop(index_id, None)


_CATEGORY_FIELD_NAME = "category"
_CATEGORY_MAX_VALUES = 20
_category_vectors_cache: dict[str, tuple[dict[str, list[float]], dict[str, str]]] = {}


def _display_label(value: str) -> str:
    return value.replace("-", " ").replace("&", "and").title()


async def _category_vectors_for_index(
    index_id: str, embed: EmbedFn, generic_category_values: set[str],
) -> tuple[dict[str, list[float]], dict[str, str]] | None:
    """
    Returns (category -> embedding, category -> display label) for this
    index's category-like field, or None if it doesn't have one shaped like
    a small, closed vocabulary. Cached per index for the process lifetime.

    `generic_category_values` (from chat/service.py's _PROMPTS
    ["generic_category_values"]) excludes catch-all buckets — one has no
    coherent semantic content of its own, so its embedding sits ambiguously
    close to many unrelated segments (observed: "others" out-scored
    "cases-&-covers" for the segment "cases"). Excluding it from the
    classification vocabulary removes that noise; it was never a category a
    customer could ask for by name anyway.
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
    values = [v for v in all_values if v.strip().lower() not in generic_category_values]
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
        vec = await embed(_display_label(v).lower())
        if vec:
            vectors[v] = vec
    if len(vectors) < 2:
        return None

    labels = {v: _display_label(v) for v in values}
    result = (vectors, labels)
    _category_vectors_cache[index_id] = result
    return result


def clear_category_vectors(index_id: str) -> None:
    """Drop this index's cached category-classification vectors — see
    chat/service.py's invalidate_index_caches, which calls this alongside
    query_filters.clear_eligibility_values on every completed ingest/rebuild."""
    _category_vectors_cache.pop(index_id, None)


def clear_all_category_vectors() -> None:
    """
    Drop every index's cached category-classification vectors — called by
    embedding_service.set_current_model() (via chat/service.py's
    clear_all_category_vectors coordinator) whenever the active embedding
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


async def try_multi_intent_context(
    search_query: str, index_id: str,
    query_by: str, query_by_weights: str | None, facet_query_by: str | None,
    global_filter: str | None, device_filter: str | None, device_field: str | None,
    embed: EmbedFn, confirm: ConfirmFn | None,
    generic_category_values: set[str], multi_intent_split_enabled: bool,
    boost_rules: list[dict] | None = None,
) -> tuple[list[dict], str] | None:
    """
    Attempt the multi-intent split (§3-§11). Returns None on any gate miss
    or failure — the caller falls back to the single retrieval.search_context
    call exactly as it did before this feature existed; nothing here can
    make a chat response worse than the pre-existing behavior, only better.

    `global_filter` is ANDed onto every plan's own filter unconditionally —
    reserve it for constraints that are genuinely index-wide (e.g. the
    customer-eligibility filter), never for a price/quantity extracted from
    the whole message, which belongs to whichever single segment it was
    actually written in (see _run's own comment on this).
    """
    if not multi_intent_split_enabled:
        log.debug("chat multi-intent split: skipped — toggle disabled")
        return None
    resources = await _category_vectors_for_index(index_id, embed, generic_category_values)
    if resources is None:
        log.debug("chat multi-intent split: skipped — no category vectors for index_id=%s", index_id)
        return None
    category_vectors, labels = resources
    log.debug("chat multi-intent split: category_vectors=%s query=%r", list(category_vectors.keys()), search_query)

    # device_filter is resolved once against the *whole* message before this
    # function is ever called (see chat/service.py's chat_stream). That
    # single-shot resolution has a real gap: Typesense's own token-dropping
    # is positional — a real device name ("iphone 17") sitting mid-sentence
    # in a long, multi-category message can fail to resolve at all even
    # though the same text resolves cleanly on its own (observed:
    # "headphones , iphone 17 case and screen protector" -> 0 hits against
    # compatible_devices; "iphone 17 case and screen protector" alone ->
    # resolves fine). Segments are short enough that this positional failure
    # doesn't reproduce, so this is re-resolved per segment below regardless
    # of whether the whole-message attempt already succeeded.
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
    # validated by resolve_span — see build_search_plans), gated by whether
    # its category is device-fit at all (_is_device_fit_category) and
    # backfilled from a sibling segment's device when exactly one distinct
    # device resolved across this message's device-fit plans (see _run).
    # Resolve every segment's own device mention — not just the first, since
    # a multi-item message can name a different device per segment
    # ("iPhone 17 case and Galaxy S25 screen protector"). Needed both to
    # seed device_filter when the whole-message resolution above failed, and
    # to strip every segment's own device name from classification text
    # below (a resolved device name sitting inside a segment measurably
    # dilutes its classification embedding away from the actual category
    # concept — "iphone 17 case" scores far weaker against "cases and
    # covers" than bare "case" does). Stripped for classification only;
    # build_search_plans still uses the untouched segment text.
    strip_phrases = re.findall(r'`([^`]+)`', device_filter) if device_filter else []
    if device_field is not None:
        for seg in chat_intent_split.split_segments(search_query):
            seg_filter = await resolve_field_value_filter(seg, index_id, device_field)
            if not seg_filter:
                continue
            if device_filter is None:
                device_filter = seg_filter
                log.debug("chat multi-intent split: device filter resolved per-segment "
                          "(whole-message resolution failed): segment=%r filter=%s", seg, seg_filter)
            for name in re.findall(r'`([^`]+)`', seg_filter):
                if name not in strip_phrases:
                    strip_phrases.append(name)

    async def _resolve_span(span: str) -> bool:
        return await resolve_span_for_index(index_id, device_field, span)

    plans = await chat_intent_split.maybe_build_plans(
        search_query,
        category_vectors=category_vectors,
        label_by_category=labels,
        embed=embed,
        confirm=confirm,
        constraint_extractor=extract_price_filter,
        strip_constraint_phrase=strip_price_phrase,
        strip_phrases=strip_phrases or None,
        resolve_span=_resolve_span,
    )
    log.debug("chat multi-intent split: plans=%s", plans)
    if not plans:
        return None

    # Each plan's own device filter, resolved from its own plan.query, and
    # (independently) whether its category is device-fit at all. Kept as a
    # separate pass (not inline in _run below) so a single shared device
    # named in only one segment of a multi-item message ("case, screen
    # protector and airpods for iPhone 17" — only the last segment names it)
    # can be backfilled onto sibling device-fit plans that named none of
    # their own: if exactly one distinct device resolves across this
    # message's plans, it's applied to every device-fit plan that resolved
    # none, purely from "how many distinct devices did this message's own
    # plans agree on" — no vocabulary.
    #
    # Resolution is attempted for EVERY plan, not just device-fit ones —
    # a device named in a non-fit segment ("headphones for iPhone 17") must
    # still be able to seed a fit sibling that named none of its own
    # ("case"), or that fit plan searches completely unfiltered instead.
    # _device_confirmed_in_text guards this against the false-positive class
    # a blanket "resolve everything" would otherwise reintroduce (a bare
    # brand word like "apple" in "apple airpods" resolving to an unrelated
    # iPhone value with no real device named) — a non-fit plan's own device
    # filter still always ends up None below regardless of this check;
    # confirmation only decides whether it's trusted as a backfill source
    # for siblings.
    async def _plan_device_info(plan: chat_intent_split.SearchPlan) -> tuple[bool, str | None]:
        is_fit = bool(device_field) and await _is_device_fit_category(index_id, plan.category, device_field)
        resolved = None
        if device_field:
            candidate = await resolve_field_value_filter(plan.query, index_id, device_field)
            if candidate and _device_confirmed_in_text(plan.query, candidate):
                resolved = candidate
        return is_fit, resolved

    fit_and_filters = await asyncio.gather(*(_plan_device_info(p) for p in plans))
    distinct_found = {f for _, f in fit_and_filters if f}
    backfill = next(iter(distinct_found)) if len(distinct_found) == 1 else None
    plan_device_filters = [(f or backfill) if is_fit else None for is_fit, f in fit_and_filters]

    async def _run(plan: chat_intent_split.SearchPlan, plan_device_filter: str | None):
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
        base_filter = combine_filters(global_filter, plan.filter_by)

        # plan_device_filter was already resolved (and possibly backfilled
        # from a sibling plan) above, gated by _is_device_fit_category —
        # never re-derived here from raw plan.query text unconditionally.
        # That gate is what keeps a category like AirPods from ever reaching
        # resolve_field_value_filter at all — the exact path that used to
        # turn "apple airpods" into a false-positive match on an unrelated
        # "Apple iPhone ..." compatible_devices value.
        filter_by = combine_filters(base_filter, plan_device_filter)
        try:
            cards, context = await retrieval.search_context(
                plan.query, index_id, query_by, query_by_weights, facet_query_by,
                price_filter=filter_by, boost_rules=boost_rules,
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
                if not await category_has_device_values(index_id, f"category:=[{plan.category}]", device_field):
                    cards, context = await retrieval.search_context(
                        plan.query, index_id, query_by, query_by_weights, facet_query_by,
                        price_filter=base_filter, boost_rules=boost_rules,
                    )
            return plan, cards, context, None
        except Exception as exc:
            return plan, [], "", exc

    results = await asyncio.gather(*(_run(p, f) for p, f in zip(plans, plan_device_filters)))

    all_cards: list[dict] = []
    sections: list[str] = []
    for plan, cards, context, err in results:
        capped = cards[:plan.top_n]
        # Stamped here, not in retrieval._hit_to_card_and_block — a card's
        # bucket label is a property of which plan searched for it, not of
        # the document itself. Lets the frontend group cards into one
        # carousel per confirmed intent instead of one flat list (§10 of the
        # multi-intent design doc) without parsing the prose "## Label"
        # headers.
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
