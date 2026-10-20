"""
Concept resolution — mapping a chat concept (e.g. "price", "device_compatibility")
to the actual field name that concept lives on for a given index.

Three layers, each owned by the part of the system that actually knows the
answer (see docs/superlativePriceDesign.md for the full design):
  1. Detection  — generic, linguistic (any superlative construction).
  2. Concept    — a small fixed table, because English fixes "cheapest" = price
                   regardless of catalog; NOT resolved via LLM or embeddings.
  3. Field      — schema-dependent per index, resolved deterministically
                   (exact name match, then numeric/date name-pattern match),
                   write-through cached on indexes.superlative_field_map so
                   repeat queries don't re-hit Typesense's schema endpoint.
A superlative with no fixed concept ("best", "nicest") or no resolvable field
on this index falls through to the existing vague-preference path rather
than guessing — caching a wrong guess would just make it wrong consistently.
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

from isp.services.infra import typesense_client as ts
from isp.services.domain import index_service
from isp.services.infra.chat.query_filters import resolve_field_value_filter

# concept -> candidate field-name substrings (tried in order; exact match wins
# first) for resolve_concept_field's heuristic fallback — externalized as
# data, not code, same reasoning as chat/service.py's _PROMPTS. This is the
# FALLBACK path only: an index that has actually been configured for a
# concept declares it explicitly in superlative_field_map (checked first, see
# resolve_concept_field), so a differently-named field on some other index
# just needs that one declaration, not an edit here. See
# plans_transformer.js for where "customer_group" itself comes from on the
# plans index, and docs/superlativePriceDesign.md for the full design.
CONCEPT_FIELD_CANDIDATES: dict[str, list[str]] = json.loads(
    (Path(__file__).with_name("concept_fields.json")).read_text(encoding="utf-8")
)

_SUPERLATIVE_RE = re.compile(r'\b(\w+est|most\s+\w+|least\s+\w+)\b', re.IGNORECASE)

# phrase -> [concept, sort direction] — English fixes this vocabulary, not any
# particular catalog, so it's shared across every index; externalized for the
# same reason as CONCEPT_FIELD_CANDIDATES above (a wording/vocab change is a
# data edit, not a code change).
_SUPERLATIVE_CONCEPT_MAP: dict[str, list[str]] = json.loads(
    (Path(__file__).with_name("superlative_phrases.json")).read_text(encoding="utf-8")
)

SORTABLE_TYPES = {"int32", "int64", "float"}
# Quantity concepts: unit_count is the numeric ARRAY of quantities an item is
# offered at (e.g. line counts), unit_price the aligned array of prices at
# each — neither a sortable scalar nor a string facet. Used by
# routers/indexes.py to validate a mapping against the field's shape.
UNIT_CONCEPTS = {"unit_count", "unit_price"}
NUMERIC_ARRAY_TYPES = {"int32[]", "int64[]", "float[]"}

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
SORT_CONCEPTS = {concept for concept, _direction in _SUPERLATIVE_CONCEPT_MAP.values()}
FILTER_CONCEPT_VALUE_TYPES = {"string", "string[]"}


def detect_superlative_concept(query: str) -> tuple[str, str, str] | None:
    """
    Return (matched_phrase, concept, direction) for a recognized superlative
    phrase, or None. Runs the generic superlative regex first purely as a
    cheap short-circuit — the actual decision comes from the fixed concept
    table, not the regex. The matched phrase is returned (not just the
    concept) so the caller can strip it from the text sent to Typesense —
    see query_filters.strip_structural_phrases.
    """
    if not _SUPERLATIVE_RE.search(query):
        return None
    low = query.lower()
    for phrase, (concept, direction) in _SUPERLATIVE_CONCEPT_MAP.items():
        if phrase in low:
            return phrase, concept, direction
    return None


def _match_field(fields: list[dict], candidates: list[str]) -> str | None:
    """Exact name match first (any type); then a numeric/date field whose name
    contains a candidate substring, preferring the shortest (most generic) name."""
    names = {f.get("name"): f for f in fields}
    for cand in candidates:
        if cand in names:
            return cand
    substring_matches = [
        f["name"] for f in fields
        if f.get("type") in SORTABLE_TYPES
        and any(cand in (f.get("name") or "").lower() for cand in candidates)
    ]
    return min(substring_matches, key=len) if substring_matches else None


async def resolve_concept_field(
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
    field = _match_field(fields, CONCEPT_FIELD_CANDIDATES.get(concept, [concept]))
    try:
        await index_service.merge_superlative_field_map(index_id, concept, field or "")
    except Exception:
        log.warning("failed to cache superlative_field_map for index=%s concept=%s",
                   index_id, concept, exc_info=True)
    return field


async def resolve_span_for_index(index_id: str, device_field: str | None, span: str) -> bool:
    """
    `chat_intent_split`'s `resolve_span` injection (see
    docs/multiIntentConjunctionSearchDesign.md §7's Tier 2 validation) —
    answers "does `span` name something this index actually recognizes?"
    for a candidate `shared_span`, independent of whatever the confirmation
    call claims.

    Reuses the same device-compatibility field already resolved once per
    request for the primary device filter (`device_field`, from
    `resolve_concept_field`) and the same Typesense-backed resolver
    (`query_filters.resolve_field_value_filter`) — no new matching logic, no
    new extension.

    An index with no resolvable device-compatibility field at all
    (`device_field is None`) has no vocabulary to check a claim against, so
    a shared_span is rejected unconditionally there — this function never
    even calls Typesense in that case.
    """
    if device_field is None:
        return False
    return bool(await resolve_field_value_filter(span, index_id, device_field))
