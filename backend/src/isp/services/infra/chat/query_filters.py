# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Query filters — building a Typesense filter_by clause (and the stripped
lexical query text that goes with it) from a chat message: price ranges,
resolved facet values (device compatibility, customer eligibility), and
message history (ID pinning, carried-over price constraints).

None of this decides WHICH field a concept maps to (see
chat/concept_resolution.py for that) — everything here takes a field name
the caller already resolved and turns query text + that field into a
concrete filter_by / stripped-query pair.
"""
from __future__ import annotations
import asyncio
import json
import re
from pathlib import Path

from isp.services.infra import typesense_client as ts

# ── Price filter ───────────────────────────────────────────────────────────────

_PRICE_BETWEEN = re.compile(
    r'between[\s.,]*\$?(\d+(?:\.\d+)?)\s*(?:and|to|-)[\s.,]*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)
_PRICE_LT = re.compile(
    r'(?:less than|under|cheaper than|below)[\s.,]*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)
_PRICE_GT = re.compile(
    r'(?:more than|over|above|at least)[\s.,]*\$?(\d+(?:\.\d+)?)', re.IGNORECASE)


def extract_price_filter(query: str) -> str | None:
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
    what an empty result should become (see strip_price_phrase and
    strip_structural_phrases, which want different fallbacks and would
    fight each other if this helper picked one itself).
    """
    text = _PRICE_BETWEEN.sub('', text)
    text = _PRICE_LT.sub('', text)
    text = _PRICE_GT.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def strip_price_phrase(text: str) -> str:
    """Remove a matched price phrase from lexical search text — falls back to
    the original text if stripping would leave nothing (a price-only query
    like "under $50" still needs *some* subject for Typesense's `q`)."""
    stripped = _remove_price_phrase(text)
    return stripped or text


def strip_matched_phrase(text: str, phrase: str) -> str:
    """
    Remove one already-matched literal phrase from lexical search text —
    generic counterpart to strip_price_phrase for filters whose match is a
    single known substring rather than a regex pattern (e.g. the customer-
    eligibility trigger word already captured as a filter_by clause). Falls
    back to the original text if stripping would leave nothing, same
    self-preserving reasoning as strip_price_phrase.
    """
    stripped = re.sub(re.escape(phrase), '', text, flags=re.IGNORECASE)
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    return stripped or text


def strip_structural_phrases(query: str, superlative_phrase: str) -> str:
    """
    Remove the matched superlative phrase and any price-range phrase from the
    query text before it's used as Typesense's lexical `q`. Both are already
    captured structurally (sort_by / filter_by) — see _remove_price_phrase
    for the price half and the measured effect of leaving such phrases in.
    Falls back to "*" (browse-all) if stripping would leave nothing (e.g.
    the query was just "cheapest" with no subject) — unlike
    strip_price_phrase's own fallback, an empty superlative query has no
    real subject worth falling back to, so this uses the unconditional
    _remove_price_phrase rather than strip_price_phrase's self-preserving
    one (composing the two would let a price-only remainder like "under
    $800" survive instead of correctly falling through to "*").
    """
    text = re.sub(re.escape(superlative_phrase), '', query, flags=re.IGNORECASE)
    text = _remove_price_phrase(text)
    return text or "*"


def combine_filters(*filters: str | None) -> str | None:
    parts = [f for f in filters if f]
    return " && ".join(parts) if parts else None


# ── Quantity (unit_count concept) ────────────────────────────────────────────
#
# "single line", "3 lines", "two guests", "family of 4" -> N. Number words and
# group phrasings are generic English, externalized as data; the UNIT word
# itself ("line", "guest", "seat") is never written here — it is derived from
# whichever field this index mapped to the unit_count concept, so chat code
# stays catalog-agnostic.

_QTY: dict = json.loads(Path(__file__).with_name("quantity_phrases.json").read_text(encoding="utf-8"))
_UNIT_NAME_SUFFIXES = {"counts", "count", "sizes", "size", "quantities", "quantity"}


def unit_terms_from_field(field_name: str) -> list[str]:
    """'eligible_lines' -> ['lines', 'line']; 'guest_counts' -> ['guests', 'guest']."""
    tokens = [t for t in field_name.lower().split("_") if t]
    if not tokens:
        return []
    word = tokens[-1]
    if word in _UNIT_NAME_SUFFIXES and len(tokens) > 1:
        word = tokens[-2]
    singular = word[:-1] if word.endswith("s") and len(word) > 1 else word
    plural = word if word.endswith("s") else word + "s"
    return list(dict.fromkeys([plural, singular]))


def extract_unit_counts(query: str, unit_terms: list[str]) -> list[tuple[str, int]]:
    """
    Every (matched_text, N) quantity the query states, in text order. Needs
    either an explicit unit word ("3 lines", "single line") or a generic
    group phrasing ("family of 4") — a bare number ("iphone 15") never
    counts, so device/model numbers can't be mistaken for quantities.
    Returning ALL matches lets the caller tell one shared quantity apart
    from a multi-intent query naming a different N per segment.
    """
    found: list[tuple[int, str, int]] = []
    if unit_terms:
        units = "|".join(re.escape(t) for t in sorted(unit_terms, key=len, reverse=True))
        words = "|".join(re.escape(w) for w in _QTY["number_words"])
        for m in re.finditer(rf"\b(\d+|{words})\s+({units})\b", query, re.IGNORECASE):
            tok = m.group(1).lower()
            n = int(tok) if tok.isdigit() else _QTY["number_words"][tok]
            if n > 0:
                found.append((m.start(), m.group(0), n))
    for pat in _QTY["group_patterns"]:
        rx = r"\b" + re.escape(pat).replace(r"\{n\}", r"(\d+)") + r"\b"
        for m in re.finditer(rx, query, re.IGNORECASE):
            if int(m.group(1)) > 0:
                found.append((m.start(), m.group(0), int(m.group(1))))
    return [(text, n) for _, text, n in sorted(found)]


def extract_unit_count(query: str, unit_terms: list[str]) -> tuple[str, int] | None:
    """First quantity the query states, or None — see extract_unit_counts."""
    hits = extract_unit_counts(query, unit_terms)
    return hits[0] if hits else None


# ── Leftover-text constraints (quantity-superlative path) ───────────────────
#
# "cheapest Essentials plan with netflix (but without hulu) for 2 lines": the
# leftover words must constrain, but words like "plan" appear in only some
# docs' text and would silently drop the true cheapest item if used
# lexically. So each leftover word narrows ONLY when it resolves against real
# data on this index:
#   1. a value of the mapped feature field (facet_query) -> feature clause
#   2. otherwise an item title (typo-tolerant title search) -> id clause
#      (exclusions use exact titles: Typesense 0.25 rejects `id:!=`)
# A word resolving to neither is never used as a filter. If it also appears
# nowhere in the index's searchable text (a genuinely unknown term, e.g. a
# brand this catalog doesn't carry) it is RETURNED so the caller can tell the
# model it was not applied. A word that does appear in product text ("plan",
# "5g") is left to the model, which sees that text in the context.
# Negation cues ("without", "no", "except", ...) flip the following words to
# exclusion clauses until a positive cue ("with", "and", "but", ...).
# Stopwords / cues are generic English, not a catalog vocabulary.

_FEATURE_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "per", "but",
    "any", "all", "has", "have", "want", "need", "show", "find", "give", "get",
    "includes", "including", "include", "also", "only", "just", "please",
}
_NEGATION_CUES = {"without", "no", "not", "except", "excluding", "exclude", "minus", "non"}
_POSITIVE_CUES = {"with", "and", "but", "plus", "including", "include", "includes"}
# Prepositions close a negation's scope ("without hulu from verizon" negates
# only hulu) while "or" keeps it open ("without netflix or hulu" negates both).
_SCOPE_TERMINATORS = {"from", "for", "on", "in", "at", "by", "to", "via"}
_FEATURE_MAX_VALUES = 50
_TITLE_MAX_IDS = 250


def _constraint_words(text: str) -> list[tuple[str, bool]]:
    """Content words in order, each with its negated flag."""
    out: list[tuple[str, bool]] = []
    negated = False
    for tok in re.findall(r"[a-z0-9][a-z0-9+']*", text.lower()):
        if tok in _NEGATION_CUES:
            negated = True
            continue
        if tok in _POSITIVE_CUES or tok in _SCOPE_TERMINATORS:
            negated = False
            continue
        if len(tok) < 3 or tok in _FEATURE_STOPWORDS:
            continue
        out.append((tok, negated))
    return list(dict.fromkeys(out))


def _quote_values(values: list[str]) -> str:
    return ",".join(f"`{v}`" for v in values)


async def text_constraint_filters(
    text: str, index_id: str, feature_field: str | None, title_field: str = "title",
    content_query_by: str | None = None,
) -> tuple[list[str], list[str]]:
    """
    Resolve leftover words into (filter_clauses, unapplied_words). Clauses
    are AND-ed: "netflix hulu" requires both; "without netflix" excludes it.
    Feature matching needs `feature_field` faceted (enforced when mapped).
    """
    words = _constraint_words(text)
    if not words:
        return [], []
    col    = ts.collection_name(index_id)
    client = ts.get_client()
    filters: list[str] = []
    unresolved: list[tuple[str, bool]] = []

    if feature_field:
        searches = [{
            "collection": col, "q": "*", "query_by": feature_field, "per_page": "0",
            "facet_by": feature_field, "facet_query": f"{feature_field}:{w}",
            "max_facet_values": str(_FEATURE_MAX_VALUES),
        } for w, _ in words]
        try:
            resp = await asyncio.to_thread(
                lambda: client.multi_search.perform({"searches": searches}, {})
            )
            results = resp.get("results", [])
        except Exception:
            results = []
        for (w, neg), result in zip(words, results + [{}] * (len(words) - len(results))):
            values = [c["value"] for fc in result.get("facet_counts", [])
                      if fc.get("field_name") == feature_field
                      for c in fc.get("counts", []) if c.get("value") and "`" not in c["value"]]
            if values:
                op = ":!=" if neg else ":="
                filters.append(f"{feature_field}{op}[{_quote_values(values)}]")
            else:
                unresolved.append((w, neg))
    else:
        unresolved = list(words)

    unapplied: list[str] = []
    if unresolved:
        searches = [{
            "collection": col, "q": w, "query_by": title_field, "per_page": str(_TITLE_MAX_IDS),
            "include_fields": f"id,{title_field}", "prefix": "false", "num_typos": "1",
            "drop_tokens_threshold": "0", "exclude_fields": "vec",
        } for w, _ in unresolved]
        try:
            resp = await asyncio.to_thread(
                lambda: client.multi_search.perform({"searches": searches}, {})
            )
            results = resp.get("results", [])
        except Exception:
            results = []
        leftover: list[tuple[str, bool]] = []
        for (w, neg), result in zip(unresolved, results + [{}] * (len(unresolved) - len(results))):
            docs = [h.get("document", {}) for h in result.get("hits", [])]
            found = int(result.get("found", 0) or 0)
            if neg:
                titles = list(dict.fromkeys(d.get(title_field) for d in docs if d.get(title_field)))
                if titles and found <= _TITLE_MAX_IDS and all("`" not in t for t in titles):
                    filters.append(f"{title_field}:!=[{_quote_values(titles)}]")
                    continue
            else:
                ids = [d["id"] for d in docs if d.get("id")]
                if ids and found <= _TITLE_MAX_IDS and all("`" not in i for i in ids):
                    filters.append(f"id:=[{_quote_values(ids)}]")
                    continue
            leftover.append((w, neg))
        if leftover and content_query_by:
            searches = [{
                "collection": col, "q": w, "query_by": content_query_by, "per_page": "0",
                "prefix": "false", "num_typos": "1", "drop_tokens_threshold": "0",
            } for w, _ in leftover]
            try:
                resp = await asyncio.to_thread(
                    lambda: client.multi_search.perform({"searches": searches}, {})
                )
                results = resp.get("results", [])
            except Exception:
                results = []
            known = {w for (w, _), r in zip(leftover, results) if int(r.get("found", 0) or 0) > 0}
            leftover = [(w, neg) for w, neg in leftover if w not in known]
        unapplied = [f"not {w}" if neg else w for w, neg in leftover]
    return filters, unapplied


async def resolve_field_value_filter(query: str, index_id: str, field_name: str) -> str | None:
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


async def category_has_device_values(index_id: str, category_filter: str, device_field: str) -> bool:
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
    as eligibility_values_for_index and multi_intent's category vectors.
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


# ── Customer-eligibility-group values ───────────────────────────────────────
#
# Per index — mirrors multi_intent.py's category-vector cache (same
# rationale: a small, closed, per-index vocabulary, cached for the process
# lifetime rather than re-fetched every request). Deliberately NOT a
# hardcoded English word list: whatever values this index's data actually
# holds for the resolved field ARE the only vocabulary ever checked, so a
# differently-worded index (or one relabelled after a reindex, once the
# process restarts) is picked up automatically — no code change here.

_ELIGIBILITY_MAX_VALUES = 20
_eligibility_values_cache: dict[str, list[str]] = {}


async def eligibility_values_for_index(index_id: str, field_name: str) -> list[str]:
    """Real, distinct values `field_name` holds on this index — cached per index."""
    cache_key = f"{index_id}:{field_name}"
    if cache_key in _eligibility_values_cache:
        return _eligibility_values_cache[cache_key]
    facets = await asyncio.to_thread(ts.get_facets, index_id, [field_name])
    values = [c["value"] for c in facets.get(field_name, []) if c.get("value")][:_ELIGIBILITY_MAX_VALUES]
    _eligibility_values_cache[cache_key] = values
    return values


def match_eligibility_value(query: str, values: list[str]) -> tuple[str, str] | None:
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


def clear_eligibility_values(index_id: str) -> None:
    """Drop this index's cached eligibility values — see chat/service.py's
    invalidate_index_caches, which calls this alongside
    multi_intent.clear_category_vectors on every completed ingest/rebuild."""
    for key in [k for k in _eligibility_values_cache if k.startswith(f"{index_id}:")]:
        del _eligibility_values_cache[key]


# ── History-derived filters ──────────────────────────────────────────────────

def _price_filter_from_history(messages: list[dict]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            f = extract_price_filter(msg.get("content", ""))
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


def resolve_retrieval_filter(
    query: str, messages: list[dict], is_follow_up: bool,
) -> str | None:
    price_filter = extract_price_filter(query)
    if price_filter is None and is_follow_up:
        price_filter = _price_filter_from_history(messages)
    id_filter = _ids_from_history(messages) if is_follow_up else None
    if id_filter and price_filter:
        return f"{id_filter} && {price_filter}"
    return id_filter or price_filter


def price_filter_for_smart(query: str, messages: list[dict]) -> str | None:
    """Price filter for smart mode — hard constraint preserved even without ID filter."""
    return extract_price_filter(query) or _price_filter_from_history(messages)
