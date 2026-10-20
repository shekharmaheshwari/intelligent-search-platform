"""
Multi-intent conjunction splitting for chat retrieval.

See docs/multiIntentConjunctionSearchDesign.md for the full design and the
review history behind every rule enforced here. Summary of what this module
is for: a chat message can name more than one product category in one
sentence ("screen protector and cases and headphones") — today's single
blended `_search_context` call lets whichever category dominates the
ranking starve the others out of context entirely, and the LLM then
confidently reports the starved categories as unavailable (they are not).

Decoupled on purpose: this module imports nothing from
``isp.services.infra.chat`` — the caller injects the embedding function, the
LLM call, and the per-intent constraint extractor. No Typesense client, no
provider routing, no settings lookups happen in here, so every function is
testable with plain fakes.

The model never writes query text (the swap-attribution risk this design
went through several review rounds to close) — it only ever returns
candidate-segment indices and, optionally, one verbatim shared span. The
server (this module) builds every final query from the original message.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

# ── Splitting (§5 of the design doc) ────────────────────────────────────────

MAX_INTENTS = 4

# Supported conjunctions. Deliberately not "or" (signals alternatives, a
# different intent) and not a bare "&" (appears inside facet values and
# brand strings, e.g. "cases-&-covers" — a spaced " & " is what a person
# types when they mean "and").
_DELIM_RE = re.compile(
    r"\s*(?:[,;&]|\band\b|\bplus\b|[.!?](?=\s+[A-Za-z]))\s*",
    re.IGNORECASE,
)
_REQUEST_PREFIX_RE = re.compile(
    r"^\s*(?:also\s+)?(?:(?:i\s+)?(?:need|want)|show me|find me|give me)\s+",
    re.IGNORECASE,
)


def _clean_segment(text: str) -> str:
    """Remove request scaffolding, preserving product modifiers and constraints."""
    return _REQUEST_PREFIX_RE.sub("", text.strip()).rstrip(".!?").strip()

# Protected patterns: don't split inside a price range or a thousands
# separator. Checked against the two tokens immediately surrounding a
# candidate split point before it's accepted.
_NUMERIC_RE = re.compile(r"^\$?\d[\d,]*(?:\.\d+)?%?$")


def split_segments(query: str) -> list[str]:
    """
    Split a (already filler-stripped) message into segments on ``,``/``&``/
    ``and``, ``plus``, semicolons and sentence boundaries, guarding numeric ranges ("$20 and $50") and thousands
    separators ("1,000") so they are never split points.

    Returns an empty list when the message has 0 or 1 segments (nothing to
    classify) or when it would exceed MAX_INTENTS (more likely a run-on
    sentence than a clean multi-category ask) — both cases are the caller's
    signal to skip straight to the existing single-search fallback.
    """
    tokens = query.split()
    if len(tokens) < 3:
        return []

    parts = _DELIM_RE.split(query)
    if len(parts) < 2:
        return []

    segments: list[str] = []
    buf = parts[0]
    pos = len(parts[0])
    for part in parts[1:]:
        idx = query.find(part, pos)
        delim_text = query[pos:idx] if idx != -1 else ""
        pos = idx + len(part) if idx != -1 else pos + len(part)

        prev_tok = buf.strip().split()[-1] if buf.strip().split() else ""
        next_tok = part.strip().split()[0] if part.strip().split() else ""
        is_numeric_guard = (
            "and" in delim_text.lower()
            and bool(_NUMERIC_RE.match(prev_tok.rstrip(",")))
            and bool(_NUMERIC_RE.match(next_tok.rstrip(",")))
        )
        is_thousands_guard = (
            "," in delim_text
            and prev_tok.isdigit()
            and next_tok[:3].isdigit()
            and len(next_tok) >= 3
        )
        if is_numeric_guard or is_thousands_guard:
            buf = buf + delim_text + part
            continue

        segments.append(buf.strip())
        buf = part
    segments.append(buf.strip())

    segments = [clean for s in segments if (clean := _clean_segment(s))]
    if len(segments) < 2 or len(segments) > MAX_INTENTS:
        return []
    return segments


# ── Classification (§6) ─────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def classify_segments(
    segment_vectors: list[list[float]],
    category_vectors: dict[str, list[float]],
    *,
    threshold: float,
    margin: float,
) -> dict[int, str]:
    """
    Top-1-with-margin classification (§6): a segment's category is its
    single highest-scoring match, only if that score clears ``threshold``
    *and* beats the runner-up by at least ``margin``. A near-tie is treated
    as unclassified — assigning the wrong category is more dangerous than
    missing one, because a wrong category isn't caught by the confirmation
    call (§7) the way a false-positive category is.
    """
    out: dict[int, str] = {}
    for i, vec in enumerate(segment_vectors):
        scored = sorted(
            ((cat, _cosine(vec, cvec)) for cat, cvec in category_vectors.items()),
            key=lambda t: t[1], reverse=True,
        )
        if not scored:
            continue
        top_cat, top_score = scored[0]
        runner_up = scored[1][1] if len(scored) > 1 else -1.0
        log.debug("classify_segments: segment[%d] top3=%s", i, scored[:3])
        if top_score >= threshold and (top_score - runner_up) >= margin:
            out[i] = top_cat
    return out


# ── Confirmation call (§7) ──────────────────────────────────────────────────

_CONFIRM_SYSTEM_PROMPT = (
    "You are given a customer message and a list of candidate segments that "
    "may each name a separate product category the customer wants. Decide "
    "whether the customer is asking for multiple separate items, or "
    "describing one item with modifiers (e.g. \"wireless and durable case\" "
    "is one case, not two products). "
    "Respond with ONLY a JSON object, no other text, matching exactly this "
    "shape: {\"confirmed_segments\": [<int>, ...], \"shared_span\": <string or null>}. "
    "confirmed_segments is the subset of the given candidate indices that are "
    "genuinely separate items — you may return fewer than were given, never "
    "an index that wasn't given. shared_span, if present, must be an exact "
    "substring copied verbatim from the customer message (e.g. a device name "
    "mentioned once that applies to every item) — never invent, paraphrase, "
    "or combine text. If none of the candidates are genuinely separate "
    "items, return {\"confirmed_segments\": [], \"shared_span\": null}."
)


def build_confirmation_prompt(query: str, candidates: dict[int, tuple[str, str]]) -> str:
    cand_list = ", ".join(f"{i}: \"{seg}\" (looks like {cat})"
                           for i, (seg, cat) in candidates.items())
    return (
        f'Customer message: "{query}"\n'
        f"Candidate segments: {cand_list}\n\n"
        f"JSON:"
    )


@dataclass
class ConfirmationResult:
    confirmed_indices: list[int]
    shared_span: str | None


async def parse_and_validate_confirmation(
    raw_text: str,
    query: str,
    segments: list[str],
    candidate_indices: set[int],
    resolve_span: Callable[[str], Awaitable[bool]] | None = None,
) -> ConfirmationResult | None:
    """
    Server-side validation (§7), in two tiers.

    Tier 1 — structural failures on `confirmed_segments` itself (malformed
    JSON, an invented or duplicate index, fewer than 2 survivors). Any Tier 1
    failure discards the whole response: returns None, which the caller must
    treat exactly like a failed/timed-out call — fail open to the existing
    single-search fallback, never partial trust of a bad response.

    Tier 2 — failures specific to `shared_span` alone (not verbatim, purely
    numeric, identical to one whole confirmed segment's own text, or not
    recognized by `resolve_span`). A Tier 2 failure degrades `shared_span`
    to None and still returns the `confirmed_indices` validated by Tier 1 —
    it never discards them. A bad guess about what's "shared" says nothing
    about whether the confirmed segments themselves are trustworthy.

    `resolve_span`, if given, is called with the candidate shared_span and
    must return True only if it names something this index actually
    recognizes (e.g. a device/brand value already in its catalog) — see
    chat/service.py's `_resolve_span_for_index`, which returns False
    unconditionally for an index with no device-compatibility field at all,
    so a shared_span is never accepted there regardless of what the
    confirmation call returns. Omitting `resolve_span` entirely (None) skips
    this check rather than failing it, for callers that don't wire it up.
    """
    try:
        start, end = raw_text.index("{"), raw_text.rindex("}") + 1
        data = json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        return None

    # Tier 1 — confirmed_segments structural checks. Any failure here
    # discards the whole response.
    indices = data.get("confirmed_segments")
    if not isinstance(indices, list) or not indices:
        return None
    if len(set(indices)) != len(indices):
        return None  # duplicates
    if not all(isinstance(i, int) and i in candidate_indices for i in indices):
        return None  # invented an index that wasn't a candidate
    if len(indices) < 2:
        return None  # same as never having triggered — fallback

    # Tier 2 — shared_span checks. Any failure here degrades shared_span to
    # None; it never discards the indices validated above.
    shared_span = data.get("shared_span")
    if shared_span is not None:
        valid = isinstance(shared_span, str) and bool(shared_span.strip())
        if valid:
            shared_span = shared_span.strip()
            valid = shared_span.lower() in query.lower()  # verbatim in the message
        if valid and _NUMERIC_RE.match(shared_span):
            # A shared span is only ever legitimate as identity/context (a
            # device or brand named once) — never as a quantity or price.
            # Each item can carry its own different number; a bare numeric
            # value claimed as "shared" is exactly the swap this contract
            # exists to make impossible — a model attaching one segment's
            # own price to every intent. Reject outright, no positional
            # heuristic needed: a real shared span is never purely numeric.
            valid = False
        if valid and any(shared_span.lower() == segments[i].strip().lower() for i in indices):
            # Observed failure: with only 2 of 3 segments confirmed (the
            # third dropped earlier by classification), there's sometimes no
            # genuine cross-segment modifier left at all — but the model
            # answered "shared_span" anyway by echoing one confirmed
            # segment's own full text back. build_search_plans then prefixes
            # or suffixes that text onto every other confirmed segment,
            # producing garbled, duplicated queries ("screen protector
            # screen protector"). A real shared span is a sub-phrase common
            # to multiple segments' own descriptions (e.g. "iphone 17"); it
            # is never identical to one segment's entire text.
            valid = False
        if valid and resolve_span is not None:
            # Independent of what the model claims, verify the span names
            # something this index actually recognizes (e.g. a real device
            # value) — closes the gap the checks above can't: "apple
            # airpods" is verbatim, non-numeric, and not a whole confirmed
            # segment's text, yet is still not a legitimate shared modifier
            # for an unrelated segment (docs/multiIntentConjunctionSearchDesign.md
            # §7, Control G).
            valid = await resolve_span(shared_span)
        if not valid:
            shared_span = None

    return ConfirmationResult(confirmed_indices=sorted(indices), shared_span=shared_span)


# ── Search plan construction (§9) ───────────────────────────────────────────

@dataclass
class SearchPlan:
    category: str
    label: str
    query: str
    filter_by: str
    top_n: int


def build_search_plans(
    query: str,
    segments: list[str],
    confirmed: ConfirmationResult,
    category_by_index: dict[int, str],
    label_by_category: dict[str, str],
    constraint_extractor: Callable[[str], str | None],
    strip_constraint_phrase: Callable[[str], str] | None = None,
    global_cap: int = 5,
) -> list[SearchPlan]:
    """
    Builds one plan per confirmed intent, in original mention order.
    `constraint_extractor` is the caller's existing per-message constraint
    logic (e.g. price extraction), invoked once per intent's own text —
    never on the whole message — so a local modifier can never leak into
    another intent's filter.

    `strip_constraint_phrase`, if given, is applied to each intent's own
    text before it becomes that plan's `query` — the matched constraint
    phrase (e.g. "under $20") is already captured structurally in
    `filter_by` via `constraint_extractor` above, so leaving it in the
    lexical query text too is pure noise for text matching (the same
    reasoning as the caller's own single-search price-stripping; see
    chat/query_filters.py's strip_price_phrase). Applied unconditionally to
    every segment (not just ones where a constraint was found) since it's
    self-preserving — a no-op wherever there's nothing to strip. None skips
    this entirely, for a caller with no such phrase-stripping of its own.

    Each confirmed intent gets its own independent `global_cap` — each
    bucket is a genuinely separate search against a different category, not
    N shares of one combined result list, so there's no reason one bucket's
    budget should shrink just because more categories were named in the
    same message. (An earlier version divided one shared total across all
    intents — e.g. 3 intents got 2/2/1 instead of 5/5/5 — inherited from the
    single flat-list display this feature replaced; that division stopped
    making sense once each intent renders as its own independent carousel.)
    A single-intent message is unaffected either way: it never reaches this
    function at all, and always got its own full `_rrf_merge` cap of 5.
    """
    shared = confirmed.shared_span
    first_seg_pos = query.lower().find(segments[confirmed.confirmed_indices[0]].lower())
    shared_is_prefix = True
    if shared:
        shared_pos = query.lower().find(shared.lower())
        shared_is_prefix = shared_pos <= first_seg_pos if first_seg_pos != -1 else True

    plans: list[SearchPlan] = []
    for i in confirmed.confirmed_indices:
        seg_text = segments[i]
        # A segment whose own text already contains the shared span (e.g.
        # the segment the span was originally lifted from, "iphone 17
        # screen protector" when shared_span="iphone 17") must not have it
        # attached again — that would duplicate the phrase
        # ("iphone 17 iphone 17 screen protector") instead of leaving the
        # segment's own text as the correct, already-complete query.
        if shared and shared.lower() not in seg_text.lower():
            text = f"{shared} {seg_text}".strip() if shared_is_prefix else f"{seg_text} {shared}".strip()
        else:
            text = seg_text
        category = category_by_index[i]
        constraint = constraint_extractor(text)
        filter_by = f"category:=[{category}]"
        if constraint:
            filter_by = f"({filter_by}) && ({constraint})"
        plan_query = strip_constraint_phrase(text) if strip_constraint_phrase else text
        plans.append(SearchPlan(
            category=category,
            label=label_by_category.get(category, category),
            query=plan_query,
            filter_by=filter_by,
            top_n=global_cap,
        ))
    return plans


# ── Orchestration ────────────────────────────────────────────────────────────

EmbedFn = Callable[[str], Awaitable[list[float] | None]]
ConfirmFn = Callable[[str, str], Awaitable[str]]   # (system, prompt) -> raw text


def _strip_phrases(text: str, phrases: list[str]) -> str:
    """
    Remove each phrase's individual words (case-insensitive, whole-word)
    from text and collapse the resulting whitespace.

    Used ONLY to build the text handed to the classifier's embedding call —
    a device/model phrase inside a segment (e.g. "iphone 17 case") measurably
    dilutes its embedding away from the actual category concept ("case"),
    since two of the three tokens are device identity, not category meaning.
    The original segment text (with the phrase intact) is untouched and is
    still what becomes the final search query in build_search_plans — a
    device-specific search is exactly what we still want there.

    Token-based, not exact-phrase-substring: `phrases` is Typesense's
    *resolved, canonical* device value (e.g. "Apple iPhone 17"), which often
    isn't verbatim what the customer typed (e.g. "iphone 17" — no "Apple"
    prefix). Matching the phrase as one substring then silently strips
    nothing on that mismatch, leaving the segment fully diluted — observed
    live (docs/multiIntentConjunctionSearchDesign.md §4's known-gap note).
    Stripping word-by-word means any of the phrase's own words that DO
    appear ("iPhone", "17") still get removed even when others ("Apple")
    don't.

    Each word's trailing punctuation is stripped before matching (e.g.
    "S25+" -> "S25") — a second, distinct instance of the same canonical-vs-
    typed mismatch: the retailer's own model name genuinely carries a
    trailing "+" ("Samsung Galaxy S25+" is that phone's real name, not bad
    data — confirmed against the product's own title), but a customer
    typing "s25" in chat naturally drops it. `\bS25\b` still matches "S25+"
    in text too, since "+" is itself a non-word character and satisfies the
    trailing `\b` — so this loses nothing for a canonical form that does
    appear verbatim, while gaining the common case where a trailing symbol
    was dropped in the customer's own phrasing.
    """
    words: set[str] = set()
    for phrase in phrases:
        for w in phrase.split():
            core = re.sub(r"[^0-9A-Za-z]+$", "", w)
            if core:
                words.add(core)
    out = text
    for word in words:
        out = re.sub(rf"\b{re.escape(word)}\b", "", out, flags=re.IGNORECASE)
    # A word stripped from inside a symbol-suffixed token (e.g. "S25" out of
    # a customer-typed "S25+") can leave the bare trailing symbol behind as
    # its own orphaned token — drop anything with no alphanumeric content
    # rather than embed stray punctuation alongside the real category words.
    return " ".join(w for w in out.split() if re.search(r"[0-9A-Za-z]", w))


async def maybe_build_plans(
    query: str,
    *,
    category_vectors: dict[str, list[float]],
    label_by_category: dict[str, str],
    embed: EmbedFn,
    # None skips the LLM confirmation call entirely (§7) — classify_segments'
    # own confidence+margin gate (similarity_threshold/similarity_margin
    # below) is trusted directly as "confirmed," with shared_span always
    # None. Reasonable once nothing downstream still depends on an
    # LLM-guessed shared_span (see chat/multi_intent.py, which resolves
    # device-per-category deterministically from the live catalog's own data
    # instead) — the remaining risk this call used to catch (a modifier-only
    # segment like
    # "wireless" in "wireless and durable case" slipping through as its own
    # confirmed intent) is already unlikely to clear classify_segments' own
    # bar, since a bare adjective rarely embeds confidently+with-margin
    # against any single category.
    confirm: ConfirmFn | None = None,
    constraint_extractor: Callable[[str], str | None],
    # Applied to each intent's own text before it becomes that plan's
    # `query` — see build_search_plans's docstring. None (the default) skips
    # it, matching the pre-existing behavior for a caller with no such
    # phrase-stripping of its own.
    strip_constraint_phrase: Callable[[str], str] | None = None,
    # Device/model phrases already resolved from the full message (e.g.
    # ["iPhone 17"]) — stripped from each segment before it's embedded for
    # classification only. See _strip_phrases.
    strip_phrases: list[str] | None = None,
    # Validates a candidate shared_span against this index's own vocabulary
    # (e.g. a device/brand value) — see parse_and_validate_confirmation and
    # chat/service.py's _resolve_span_for_index. None skips that check.
    resolve_span: Callable[[str], Awaitable[bool]] | None = None,
    # Empirically calibrated against voyage-3 (the embed model configured in
    # this deployment — see docs/multiIntentConjunctionSearchDesign.md §6/§13):
    # a genuine category match for a short segment against a short category
    # label lands around 0.55-0.8 cosine, not the originally-guessed 0.62
    # floor, which silently dropped correct matches (e.g. "screen protector"
    # vs. "screen-protectors" at 0.56). Re-tune if the deployment's embed
    # model changes.
    similarity_threshold: float = 0.55,
    similarity_margin: float = 0.05,
    global_cap: int = 5,
    decomposed_segments: list[str] | None = None,
    matched_categories: dict[int, str] | None = None,
) -> list[SearchPlan] | None:
    """
    Full pipeline, §3. Returns None at any gate — the caller's existing
    single `_search_context` call is the fallback in every case, per the
    design's baseline safety rule (§2), which applies regardless of what
    this function returns.
    """
    if len(category_vectors) < (1 if decomposed_segments else 2):
        return None

    segments = decomposed_segments if decomposed_segments is not None else split_segments(query)
    if not segments or not 2 <= len(segments) <= MAX_INTENTS:
        return None

    catalog_matches = {
        i: category for i, category in (matched_categories or {}).items()
        if type(i) is int and 0 <= i < len(segments) and category in category_vectors
    }
    segment_vectors = []
    for i, seg in enumerate(segments):
        if i in catalog_matches:
            segment_vectors.append([])  # No embedding call for an exact catalog match.
            continue
        classify_text = _clean_segment(seg)
        classify_text = _strip_phrases(classify_text, strip_phrases) if strip_phrases else classify_text
        # Same dilution problem strip_phrases solves for a device name inside
        # a segment ("iphone 17 case" scoring weaker against "cases" than
        # bare "case" does) applies just as much to a price phrase left in
        # ("cases under $20" vs. bare "cases") — strip_constraint_phrase must
        # stay in the segment's own text for constraint_extractor to find
        # (see build_search_plans) and is only removed from the final
        # plan.query there, but classification never needs it and is
        # measurably hurt by it.
        if strip_constraint_phrase:
            classify_text = strip_constraint_phrase(classify_text)
        # Conversational articles carry no category meaning and can dilute a
        # one-word intent after its device phrase is removed ("an iPhone 17
        # case" -> "an case"). This is classifier-only; the final retrieval
        # query retains the user's original wording.
        classify_text = re.sub(r"^(?:a|an|the)\s+", "", classify_text, flags=re.IGNORECASE)
        vec = await embed(classify_text or seg)  # never embed an empty string
        if vec is None:
            return None
        segment_vectors.append(vec)

    candidates = classify_segments(
        segment_vectors, category_vectors,
        threshold=similarity_threshold, margin=similarity_margin,
    )
    candidates.update(catalog_matches)
    log.debug("chat_intent_split: segments=%s candidates=%s catalog_matches=%s",
              segments, candidates, catalog_matches)
    distinct_categories = set(candidates.values())
    if decomposed_segments is not None:
        if len(candidates) != len(segments):
            return None  # Never silently lose an intent from a structured plan.
    elif len(distinct_categories) < 2:
        return None

    if confirm is None:
        confirmed = ConfirmationResult(confirmed_indices=sorted(candidates.keys()), shared_span=None)
        log.debug("chat_intent_split: confirm=None, auto-confirmed=%s", confirmed)
    else:
        candidate_map = {i: (segments[i], cat) for i, cat in candidates.items()}
        prompt = build_confirmation_prompt(query, candidate_map)
        try:
            raw = await confirm(_CONFIRM_SYSTEM_PROMPT, prompt)
        except Exception:
            return None  # provider error/timeout — fail open

        confirmed = await parse_and_validate_confirmation(
            raw, query, segments, set(candidates.keys()), resolve_span,
        )
        log.debug("chat_intent_split: raw_confirmation=%r parsed=%s", raw, confirmed)
        if confirmed is None:
            return None

    # The legacy splitter collapses duplicate categories (§5). A structured
    # decomposition already distinguishes separate requests within a category.
    seen_categories: set[str] = set()
    deduped_indices = []
    for i in confirmed.confirmed_indices:
        cat = candidates[i]
        if cat in seen_categories and decomposed_segments is None:
            continue
        seen_categories.add(cat)
        deduped_indices.append(i)
    if len(deduped_indices) < 2:
        return None
    confirmed = ConfirmationResult(confirmed_indices=deduped_indices, shared_span=confirmed.shared_span)

    return build_search_plans(
        query, segments, confirmed, candidates, label_by_category,
        constraint_extractor, strip_constraint_phrase, global_cap,
    )
