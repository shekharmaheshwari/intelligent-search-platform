# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Pure-function tests for chat_intent_split.py — no DB, no Typesense, no LLM.
See docs/multiIntentConjunctionSearchDesign.md for the design these enforce.
"""
from __future__ import annotations

from isp.services.domain.chat_intent_split import (
    split_segments, classify_segments, parse_and_validate_confirmation,
    ConfirmationResult, build_search_plans, maybe_build_plans,
    _strip_phrases,
)


# ── Splitting (§5) ───────────────────────────────────────────────────────────

def test_splits_three_way_conjunction():
    segs = split_segments("iphone 17 screen protector and cases and compatible headphones")
    assert segs == ["iphone 17 screen protector", "cases", "compatible headphones"]


def test_spaced_ampersand_is_a_delimiter():
    segs = split_segments("cases & headphones for iphone 17")
    assert segs == ["cases", "headphones for iphone 17"]


def test_no_conjunction_returns_empty():
    assert split_segments("durable case for iphone 17") == []


def test_price_range_is_protected_from_splitting():
    assert split_segments("case between $20 and $50") == []


def test_thousands_separator_is_protected_from_splitting():
    assert split_segments("speaker under 1,000 rupees") == []


def test_more_than_max_intents_returns_empty():
    assert split_segments("cases and screen protectors and headphones and chargers and speakers") == []


def test_cases_and_cover_collapses_via_duplicate_category_not_splitting():
    # splitting doesn't know synonymy — this documents that "cover" IS its
    # own segment at the split stage; collapsing happens later (§5) once
    # both segments classify to the same category.
    segs = split_segments("iphone 17 screen protector and cases & cover and compatible headphones")
    assert segs == ["iphone 17 screen protector", "cases", "cover", "compatible headphones"]


# ── Classification (§6) ──────────────────────────────────────────────────────

def test_top1_with_margin_classifies_confidently_separated_vectors():
    vecs = {"cases": [1.0, 0.0], "headphones": [0.0, 1.0]}
    segment_vectors = [[0.95, 0.05], [0.05, 0.95]]
    out = classify_segments(segment_vectors, vecs, threshold=0.5, margin=0.1)
    assert out == {0: "cases", 1: "headphones"}


def test_near_tie_is_dropped_not_arbitrarily_assigned():
    vecs = {"cases": [1.0, 0.0], "screen-protectors": [0.99, 0.01]}
    segment_vectors = [[0.7, 0.3]]  # ~equally close to both
    out = classify_segments(segment_vectors, vecs, threshold=0.5, margin=0.1)
    assert out == {}


def test_below_threshold_is_dropped():
    vecs = {"cases": [1.0, 0.0]}
    segment_vectors = [[0.1, 0.99]]
    out = classify_segments(segment_vectors, vecs, threshold=0.5, margin=0.05)
    assert out == {}


# ── Confirmation validation (§7) — Tier 1: discards confirmed_segments ──────

async def test_valid_confirmation_parses():
    raw = '{"confirmed_segments": [0, 1], "shared_span": "iphone 17"}'
    r = await parse_and_validate_confirmation(raw, "iphone 17 cases and headphones",
                                              ["iphone 17 cases", "headphones"], {0, 1})
    assert r == ConfirmationResult(confirmed_indices=[0, 1], shared_span="iphone 17")


async def test_invented_index_is_rejected():
    raw = '{"confirmed_segments": [0, 2], "shared_span": null}'
    r = await parse_and_validate_confirmation(raw, "cases and headphones",
                                              ["cases", "headphones"], {0, 1})
    assert r is None


async def test_duplicate_index_is_rejected():
    raw = '{"confirmed_segments": [0, 0], "shared_span": null}'
    r = await parse_and_validate_confirmation(raw, "cases and headphones",
                                              ["cases", "headphones"], {0, 1})
    assert r is None


async def test_fewer_than_two_confirmed_is_rejected():
    raw = '{"confirmed_segments": [0], "shared_span": null}'
    r = await parse_and_validate_confirmation(raw, "cases and headphones",
                                              ["cases", "headphones"], {0, 1})
    assert r is None


async def test_malformed_json_is_rejected():
    assert await parse_and_validate_confirmation("not json", "q", ["a", "b"], {0, 1}) is None


# ── Confirmation validation (§7) — Tier 2: degrades shared_span only ────────
# A Tier 2 failure must NEVER return None — confirmed_indices survive with
# shared_span forced to None. See docs/multiIntentConjunctionSearchDesign.md
# §7 and Control G (§12) for why: a bad shared_span guess says nothing about
# whether the confirmed segments themselves are trustworthy.

async def test_non_verbatim_shared_span_degrades_not_discards():
    raw = '{"confirmed_segments": [0, 1], "shared_span": "iPhone Seventeen"}'
    r = await parse_and_validate_confirmation(raw, "iphone 17 cases and headphones",
                                              ["iphone 17 cases", "headphones"], {0, 1})
    assert r == ConfirmationResult(confirmed_indices=[0, 1], shared_span=None)


async def test_shared_span_swap_attempt_degrades_not_discards():
    """A model attempting to move a modifier between segments has no valid
    field to express it in — a shared_span equal to one segment's own price
    modifier, not present at all in the other, fails Tier 2, but the
    confirmed indices are still usable."""
    raw = '{"confirmed_segments": [0, 1], "shared_span": "$100"}'
    r = await parse_and_validate_confirmation(
        raw, "red cases under $20 and noise-canceling headphones under $100",
        ["red cases under $20", "noise-canceling headphones under $100"], {0, 1},
    )
    assert r == ConfirmationResult(confirmed_indices=[0, 1], shared_span=None)


async def test_shared_span_echoing_one_segments_own_text_degrades_not_discards():
    """Observed live failure: with a third segment dropped by classification,
    the model sometimes answers shared_span with one of the two remaining
    segments' own full text instead of null — build_search_plans would then
    duplicate that text onto every confirmed segment's query
    ("screen protector screen protector"). A real shared span is a
    sub-phrase common to multiple segments, never identical to one whole
    segment — but this alone doesn't make confirmed_segments untrustworthy."""
    raw = '{"confirmed_segments": [0, 1], "shared_span": "screen protector"}'
    r = await parse_and_validate_confirmation(
        raw, "headphones , iphone 17 case and screen protector",
        ["headphones", "screen protector"], {0, 1},
    )
    assert r == ConfirmationResult(confirmed_indices=[0, 1], shared_span=None)


async def test_shared_span_failing_resolver_degrades_not_discards():
    """Control G (§12): "apple airpods" is verbatim, non-numeric, and not a
    whole confirmed segment's text — it passes every other Tier 2 check —
    yet it's still not a legitimate shared modifier for an unrelated
    segment. resolve_span is the check that actually catches this."""
    raw = '{"confirmed_segments": [0, 2], "shared_span": "apple airpods"}'

    async def resolve_span(span: str) -> bool:
        return False  # "apple airpods" doesn't name a known device/brand value

    r = await parse_and_validate_confirmation(
        raw, "apple airpods , iphone 17 case and screen protector",
        ["apple airpods", "iphone 17 case", "screen protector"], {0, 2},
        resolve_span,
    )
    assert r == ConfirmationResult(confirmed_indices=[0, 2], shared_span=None)


async def test_shared_span_passing_resolver_is_kept():
    raw = '{"confirmed_segments": [0, 1], "shared_span": "iphone 17"}'

    async def resolve_span(span: str) -> bool:
        return span == "iphone 17"

    r = await parse_and_validate_confirmation(
        raw, "iphone 17 case and screen protector",
        ["iphone 17 case", "screen protector"], {0, 1},
        resolve_span,
    )
    assert r == ConfirmationResult(confirmed_indices=[0, 1], shared_span="iphone 17")


async def test_resolve_span_omitted_skips_the_check():
    """No resolve_span wired up at all (resolve_span=None, the default) —
    backward compatible: the check is skipped, not failed. Uses a span that
    would pass every other Tier 2 check regardless, so this isolates the
    resolve_span-omitted behavior specifically."""
    raw = '{"confirmed_segments": [0, 1], "shared_span": "iphone 17"}'
    r = await parse_and_validate_confirmation(
        raw, "iphone 17 case and screen protector",
        ["case", "screen protector"], {0, 1},
    )
    assert r == ConfirmationResult(confirmed_indices=[0, 1], shared_span="iphone 17")


# ── Budget allocation (§11) ──────────────────────────────────────────────────
# Each confirmed intent gets its own independent GLOBAL_CARD_CAP — a
# genuinely separate search against a different category, not N shares of
# one combined list. See test_local_modifiers_never_cross_intents and
# test_shared_span_prefixed_to_every_confirmed_intent below for the actual
# per-plan top_n assertions (build_search_plans sets top_n=global_cap for
# every plan directly; there's no longer a division formula to test here).

def test_every_plan_gets_the_full_cap_independently():
    confirmed = ConfirmationResult(confirmed_indices=[0, 1, 2], shared_span=None)
    segments = ["headphones", "case", "screen protector"]
    category_by_index = {0: "headphones", 1: "cases-&-covers", 2: "screen-protectors"}
    labels = {"headphones": "Headphones", "cases-&-covers": "Cases And Covers",
              "screen-protectors": "Screen Protectors"}
    plans = build_search_plans("...", segments, confirmed, category_by_index, labels,
                               lambda t: None, global_cap=5)
    assert [p.top_n for p in plans] == [5, 5, 5]


# ── Plan construction (§9) ───────────────────────────────────────────────────

def test_local_modifiers_never_cross_intents():
    confirmed = ConfirmationResult(confirmed_indices=[0, 1], shared_span=None)
    segments = ["red cases under $20", "noise-canceling headphones under $100"]
    category_by_index = {0: "cases-&-covers", 1: "headphones"}
    labels = {"cases-&-covers": "Cases & Covers", "headphones": "Headphones"}

    def extract_price(text: str) -> str | None:
        if "$20" in text:
            return "price:<=20"
        if "$100" in text:
            return "price:<=100"
        return None

    plans = build_search_plans("...", segments, confirmed, category_by_index, labels, extract_price)
    by_cat = {p.category: p for p in plans}
    assert "price:<=20" in by_cat["cases-&-covers"].filter_by
    assert "price:<=100" not in by_cat["cases-&-covers"].filter_by
    assert "price:<=100" in by_cat["headphones"].filter_by
    assert "price:<=20" not in by_cat["headphones"].filter_by


def test_shared_span_prefixed_to_every_confirmed_intent():
    confirmed = ConfirmationResult(confirmed_indices=[0, 1], shared_span="iphone 17")
    segments = ["iphone 17 screen protector", "cases"]
    category_by_index = {0: "screen-protectors", 1: "cases-&-covers"}
    labels = {"screen-protectors": "Screen Protectors", "cases-&-covers": "Cases & Covers"}
    plans = build_search_plans("iphone 17 screen protector and cases", segments, confirmed,
                               category_by_index, labels, lambda t: None)
    assert plans[1].query == "iphone 17 cases"


def test_shared_span_not_duplicated_onto_the_segment_it_already_lives_in():
    # §12's own worked example: segment 0 already starts with the shared
    # span. Re-prepending it there would produce
    # "iphone 17 iphone 17 screen protector" — the segment's own text is
    # already the correct, complete query and must be left untouched; the
    # span is still attached to every *other* confirmed segment that
    # doesn't already contain it.
    confirmed = ConfirmationResult(confirmed_indices=[0, 1, 2], shared_span="iphone 17")
    segments = ["iphone 17 screen protector", "cases", "compatible headphones"]
    category_by_index = {0: "screen-protectors", 1: "cases-&-covers", 2: "headphones"}
    labels = {"screen-protectors": "Screen Protectors", "cases-&-covers": "Cases & Covers",
              "headphones": "Headphones"}
    plans = build_search_plans(
        "iphone 17 screen protector and cases and compatible headphones",
        segments, confirmed, category_by_index, labels, lambda t: None,
    )
    by_cat = {p.category: p for p in plans}
    assert by_cat["screen-protectors"].query == "iphone 17 screen protector"
    assert by_cat["cases-&-covers"].query == "iphone 17 cases"
    assert by_cat["headphones"].query == "iphone 17 compatible headphones"


def test_shared_span_not_duplicated_when_it_is_a_suffix_match():
    # Same rule, but the shared span sits after the segment it originated
    # from in the message, so it would otherwise be re-suffixed instead of
    # re-prefixed onto that same segment.
    confirmed = ConfirmationResult(confirmed_indices=[1, 2], shared_span="iphone 17")
    segments = ["apple airpods", "iphone 17 case", "screen protector"]
    category_by_index = {1: "cases-&-covers", 2: "screen-protectors"}
    labels = {"cases-&-covers": "Cases & Covers", "screen-protectors": "Screen Protectors"}
    plans = build_search_plans(
        "apple airpods , iphone 17 case and screen protector",
        segments, confirmed, category_by_index, labels, lambda t: None,
    )
    by_cat = {p.category: p for p in plans}
    assert by_cat["cases-&-covers"].query == "iphone 17 case"
    assert by_cat["screen-protectors"].query == "iphone 17 screen protector"


# ── Full orchestration ───────────────────────────────────────────────────────

async def test_maybe_build_plans_end_to_end_happy_path():
    category_vectors = {"cases-&-covers": [1.0, 0.0, 0.0], "headphones": [0.0, 1.0, 0.0]}
    labels = {"cases-&-covers": "Cases & Covers", "headphones": "Headphones"}

    async def fake_embed(text: str):
        return {"cases": [0.95, 0.05, 0.0], "headphones": [0.05, 0.95, 0.0]}.get(
            "cases" if "case" in text else "headphones", [0.95, 0.05, 0.0])

    async def fake_confirm(system: str, prompt: str) -> str:
        return '{"confirmed_segments": [0, 1], "shared_span": null}'

    plans = await maybe_build_plans(
        "cases and headphones",
        category_vectors=category_vectors, label_by_category=labels,
        embed=fake_embed, confirm=fake_confirm, constraint_extractor=lambda t: None,
    )
    assert plans is not None
    assert {p.category for p in plans} == {"cases-&-covers", "headphones"}


async def test_maybe_build_plans_falls_back_on_confirmation_failure():
    category_vectors = {"cases-&-covers": [1.0, 0.0], "headphones": [0.0, 1.0]}

    async def fake_embed(text: str):
        return [0.95, 0.05] if "case" in text else [0.05, 0.95]

    async def failing_confirm(system: str, prompt: str) -> str:
        raise TimeoutError("provider timeout")

    plans = await maybe_build_plans(
        "cases and headphones",
        category_vectors=category_vectors, label_by_category={},
        embed=fake_embed, confirm=failing_confirm, constraint_extractor=lambda t: None,
    )
    assert plans is None


async def test_maybe_build_plans_control_g_bad_shared_span_keeps_both_plans():
    """Control G (§12): a bad shared_span must not sink otherwise-valid
    candidates. Before the two-tier fix, this end-to-end case returned None
    (full fallback) — the exact bug this whole feature exists to prevent,
    one level deeper."""
    category_vectors = {"headphones": [1.0, 0.0], "screen-protectors": [0.0, 1.0]}
    labels = {"headphones": "Headphones", "screen-protectors": "Screen Protectors"}

    async def fake_embed(text: str):
        return [0.95, 0.05] if "airpod" in text else [0.05, 0.95]

    async def fake_confirm(system: str, prompt: str) -> str:
        # The model tries to treat segment 0's own full text as shared —
        # exactly the live failure this test is named for.
        return '{"confirmed_segments": [0, 1], "shared_span": "apple airpods"}'

    async def resolve_span(span: str) -> bool:
        return False  # not a recognized device/brand value on this index

    plans = await maybe_build_plans(
        "apple airpods and screen protector",
        category_vectors=category_vectors, label_by_category=labels,
        embed=fake_embed, confirm=fake_confirm, constraint_extractor=lambda t: None,
        resolve_span=resolve_span,
    )
    assert plans is not None
    assert {p.category for p in plans} == {"headphones", "screen-protectors"}
    by_cat = {p.category: p for p in plans}
    assert by_cat["headphones"].query == "apple airpods"
    assert by_cat["screen-protectors"].query == "screen protector"


async def test_maybe_build_plans_single_intent_never_triggers():
    plans = await maybe_build_plans(
        "durable case for iphone 17",
        category_vectors={"a": [1.0], "b": [0.0]}, label_by_category={},
        embed=None, confirm=None, constraint_extractor=lambda t: None,
    )
    assert plans is None


# ── Device-phrase stripping for classification (not for the final query) ────

def test_strip_phrases_removes_case_insensitively():
    assert _strip_phrases("iPhone 17 case", ["iphone 17"]) == "case"


def test_strip_phrases_collapses_leftover_whitespace():
    assert _strip_phrases("a iphone 17 case", ["iphone 17"]) == "a case"


def test_strip_phrases_no_match_is_a_no_op():
    assert _strip_phrases("headphones", ["iphone 17"]) == "headphones"


def test_strip_phrases_empty_result_handled_by_caller_not_here():
    # _strip_phrases itself just returns "" — maybe_build_plans is the one
    # that falls back to the original text rather than embedding "".
    assert _strip_phrases("iphone 17", ["iphone 17"]) == ""


def test_strip_phrases_matches_words_individually_not_the_whole_phrase():
    """The real, observed bug: the resolved canonical device value carries a
    brand prefix ("Apple iPhone 17") the customer never typed ("iphone 17
    case") — an exact-phrase substring match finds nothing and silently
    strips nothing, leaving the segment fully diluted. Word-by-word
    stripping still removes "iPhone" and "17" even though "Apple" isn't
    present to match."""
    assert _strip_phrases("iphone 17 case", ["Apple iPhone 17"]) == "case"


def test_strip_phrases_partial_word_is_not_falsely_matched():
    # "17" as a whole-word match must not clip inside an unrelated token
    # that merely contains the same digits, and "pro" must not match inside
    # "protector" — \b boundaries are what make word-based stripping safe.
    assert _strip_phrases("screen protector", ["Apple iPhone 17 Pro"]) == "screen protector"


def test_strip_phrases_strips_a_canonical_trailing_symbol_the_customer_dropped():
    """The real, observed bug: "Samsung Galaxy S25+" is the phone's actual
    retail name (confirmed against the product's own title — not bad data),
    but a customer typing "s25" in chat naturally drops the trailing "+".
    An exact whole-word match on "S25+" (literal plus required) finds
    nothing in "s25", leaving the segment diluted with an undiluted model
    number. Stripping on each word's alphanumeric core closes this."""
    assert _strip_phrases("samsung s25 case", ["Samsung Galaxy S25+"]) == "case"


def test_strip_phrases_still_matches_the_canonical_form_verbatim():
    # The trailing "+" is itself a non-word character, so \bS25\b still
    # matches inside "S25+" too — nothing is lost for the case where the
    # customer did type the symbol.
    assert _strip_phrases("samsung s25+ case", ["Samsung Galaxy S25+"]) == "case"


async def test_device_phrase_diluting_a_segment_is_stripped_before_classification():
    # Simulates the real failure: "iphone 17 case" alone scores too weak
    # against the "cases" category vector, but once the device phrase is
    # stripped for classification, the bare "case" text scores strongly.
    # 3 dims so a "diluted" vector can point mostly along a 3rd, unrelated
    # axis — with only 2 orthogonal category vectors, cosine similarities to
    # them always sum to 1 in magnitude-squared, so neither can be pushed
    # below the 0.55 floor at once without a dimension to dilute into.
    category_vectors = {"cases-&-covers": [1.0, 0.0, 0.0], "headphones": [0.0, 1.0, 0.0]}

    async def fake_embed(text: str):
        if text == "case":
            return [0.95, 0.05, 0.1]     # strong, clean match once stripped
        if "iphone 17" in text:
            return [0.3, 0.1, 0.9]       # diluted — weak against both categories
        return [0.05, 0.95, 0.1]

    async def fake_confirm(system: str, prompt: str) -> str:
        return '{"confirmed_segments": [0, 1], "shared_span": null}'

    # Without stripping: the diluted vector fails classification, only one
    # category survives, and the split doesn't trigger at all.
    plans = await maybe_build_plans(
        "iphone 17 case and headphones",
        category_vectors=category_vectors, label_by_category={},
        embed=fake_embed, confirm=fake_confirm, constraint_extractor=lambda t: None,
    )
    assert plans is None

    # With the device phrase supplied to strip: the case segment classifies
    # correctly and both categories get a plan.
    plans = await maybe_build_plans(
        "iphone 17 case and headphones",
        category_vectors=category_vectors, label_by_category={},
        embed=fake_embed, confirm=fake_confirm, constraint_extractor=lambda t: None,
        strip_phrases=["iphone 17"],
    )
    assert plans is not None
    assert {p.category for p in plans} == {"cases-&-covers", "headphones"}
    # The final query still carries the device context — only the
    # classification embedding call saw the stripped text.
    by_cat = {p.category: p for p in plans}
    assert "iphone 17" in by_cat["cases-&-covers"].query


async def test_device_phrase_with_mismatched_brand_prefix_is_still_stripped():
    """The real, previously-broken case: Typesense's resolved canonical
    device value carries a brand prefix ("Apple iPhone 17") the customer
    never typed. Before word-based stripping, this silently failed to
    strip anything and the case segment never classified at all."""
    category_vectors = {"cases-&-covers": [1.0, 0.0, 0.0], "headphones": [0.0, 1.0, 0.0]}

    async def fake_embed(text: str):
        if text == "case":
            return [0.95, 0.05, 0.1]
        if "iphone 17" in text:
            return [0.3, 0.1, 0.9]  # diluted — the pre-fix failure mode
        return [0.05, 0.95, 0.1]

    async def fake_confirm(system: str, prompt: str) -> str:
        return '{"confirmed_segments": [0, 1], "shared_span": null}'

    plans = await maybe_build_plans(
        "iphone 17 case and headphones",
        category_vectors=category_vectors, label_by_category={},
        embed=fake_embed, confirm=fake_confirm, constraint_extractor=lambda t: None,
        strip_phrases=["Apple iPhone 17"],  # not verbatim in the segment
    )
    assert plans is not None
    assert {p.category for p in plans} == {"cases-&-covers", "headphones"}
