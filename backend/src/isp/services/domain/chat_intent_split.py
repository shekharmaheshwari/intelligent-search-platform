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
_DELIM_RE = re.compile(r"\s*(?:,|&|\band\b)\s*", re.IGNORECASE)

# Protected patterns: don't split inside a price range or a thousands
# separator. Checked against the two tokens immediately surrounding a
# candidate split point before it's accepted.
_NUMERIC_RE = re.compile(r"^\$?\d[\d,]*(?:\.\d+)?%?$")


def split_segments(query: str) -> list[str]:
    """
    Split a (already filler-stripped) message into segments on ``,``/``&``/
    ``and``, guarding numeric ranges ("$20 and $50") and thousands
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

    segments = [s for s in segments if s]
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


def parse_and_validate_confirmation(
    raw_text: str,
    query: str,
    segments: list[str],
    candidate_indices: set[int],
) -> ConfirmationResult | None:
    """
    Server-side validation (§7) — any failure returns None, which the caller
    must treat exactly like a failed/timed-out call: fail open to the
    existing single-search fallback, never partial trust of a bad response.
    """
    try:
        start, end = raw_text.index("{"), raw_text.rindex("}") + 1
        data = json.loads(raw_text[start:end])
    except (ValueError, json.JSONDecodeError):
        return None

    indices = data.get("confirmed_segments")
    if not isinstance(indices, list) or not indices:
        return None
    if len(set(indices)) != len(indices):
        return None  # duplicates
    if not all(isinstance(i, int) and i in candidate_indices for i in indices):
        return None  # invented an index that wasn't a candidate

    shared_span = data.get("shared_span")
    if shared_span is not None:
        if not isinstance(shared_span, str) or not shared_span.strip():
            return None
        shared_span = shared_span.strip()
        if shared_span.lower() not in query.lower():
            return None  # not verbatim in the original message
        if _NUMERIC_RE.match(shared_span):
            # A shared span is only ever legitimate as identity/context (a
            # device or brand named once) — never as a quantity or price.
            # Each item can carry its own different number; a bare numeric
            # value claimed as "shared" is exactly the swap this contract
            # exists to make impossible — a model attaching one segment's
            # own price to every intent. Reject outright, no positional
            # heuristic needed: a real shared span is never purely numeric.
            return None

    if len(indices) < 2:
        return None  # same as never having triggered — fallback

    return ConfirmationResult(confirmed_indices=sorted(indices), shared_span=shared_span)


# ── Search plan construction (§9) ───────────────────────────────────────────

@dataclass
class SearchPlan:
    category: str
    label: str
    query: str
    filter_by: str
    top_n: int


def allocate_top_n(n_intents: int, global_cap: int = 5) -> list[int]:
    """§11's budget formula: base = cap // n, remainder distributed to the
    first `remainder` intents in order. With MAX_INTENTS=4 and cap=5,
    base is always >= 1."""
    base, remainder = divmod(global_cap, n_intents)
    return [base + 1 if i < remainder else base for i in range(n_intents)]


def build_search_plans(
    query: str,
    segments: list[str],
    confirmed: ConfirmationResult,
    category_by_index: dict[int, str],
    label_by_category: dict[str, str],
    constraint_extractor: Callable[[str], str | None],
    global_cap: int = 5,
) -> list[SearchPlan]:
    """
    Builds one plan per confirmed intent, in original mention order.
    `constraint_extractor` is the caller's existing per-message constraint
    logic (e.g. price extraction), invoked once per intent's own text —
    never on the whole message — so a local modifier can never leak into
    another intent's filter.
    """
    shared = confirmed.shared_span
    first_seg_pos = query.lower().find(segments[confirmed.confirmed_indices[0]].lower())
    shared_is_prefix = True
    if shared:
        shared_pos = query.lower().find(shared.lower())
        shared_is_prefix = shared_pos <= first_seg_pos if first_seg_pos != -1 else True

    top_ns = allocate_top_n(len(confirmed.confirmed_indices), global_cap)
    plans: list[SearchPlan] = []
    for slot, i in enumerate(confirmed.confirmed_indices):
        seg_text = segments[i]
        if shared:
            text = f"{shared} {seg_text}".strip() if shared_is_prefix else f"{seg_text} {shared}".strip()
        else:
            text = seg_text
        category = category_by_index[i]
        constraint = constraint_extractor(text)
        filter_by = f"category:=[{category}]"
        if constraint:
            filter_by = f"({filter_by}) && ({constraint})"
        plans.append(SearchPlan(
            category=category,
            label=label_by_category.get(category, category),
            query=text,
            filter_by=filter_by,
            top_n=top_ns[slot],
        ))
    return plans


# ── Orchestration ────────────────────────────────────────────────────────────

EmbedFn = Callable[[str], Awaitable[list[float] | None]]
ConfirmFn = Callable[[str, str], Awaitable[str]]   # (system, prompt) -> raw text


async def maybe_build_plans(
    query: str,
    *,
    category_vectors: dict[str, list[float]],
    label_by_category: dict[str, str],
    embed: EmbedFn,
    confirm: ConfirmFn,
    constraint_extractor: Callable[[str], str | None],
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
) -> list[SearchPlan] | None:
    """
    Full pipeline, §3. Returns None at any gate — the caller's existing
    single `_search_context` call is the fallback in every case, per the
    design's baseline safety rule (§2), which applies regardless of what
    this function returns.
    """
    if len(category_vectors) < 2:
        return None  # nothing to split against

    segments = split_segments(query)
    if not segments:
        return None

    segment_vectors = []
    for seg in segments:
        vec = await embed(seg)
        if vec is None:
            return None
        segment_vectors.append(vec)

    candidates = classify_segments(
        segment_vectors, category_vectors,
        threshold=similarity_threshold, margin=similarity_margin,
    )
    log.debug("chat_intent_split: segments=%s candidates=%s", segments, candidates)
    distinct_categories = set(candidates.values())
    if len(distinct_categories) < 2:
        return None

    candidate_map = {i: (segments[i], cat) for i, cat in candidates.items()}
    prompt = build_confirmation_prompt(query, candidate_map)
    try:
        raw = await confirm(_CONFIRM_SYSTEM_PROMPT, prompt)
    except Exception:
        return None  # provider error/timeout — fail open

    confirmed = parse_and_validate_confirmation(
        raw, query, segments, set(candidates.keys()),
    )
    if confirmed is None:
        return None

    # A duplicate category across confirmed segments collapses (§5) — keep
    # only the first occurrence's index per category.
    seen_categories: set[str] = set()
    deduped_indices = []
    for i in confirmed.confirmed_indices:
        cat = candidates[i]
        if cat in seen_categories:
            continue
        seen_categories.add(cat)
        deduped_indices.append(i)
    if len(deduped_indices) < 2:
        return None
    confirmed = ConfirmationResult(confirmed_indices=deduped_indices, shared_span=confirmed.shared_span)

    return build_search_plans(
        query, segments, confirmed, candidates, label_by_category,
        constraint_extractor, global_cap,
    )
