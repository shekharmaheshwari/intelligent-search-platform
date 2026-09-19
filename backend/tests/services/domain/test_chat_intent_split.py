"""
Pure-function tests for chat_intent_split.py — no DB, no Typesense, no LLM.
See docs/multiIntentConjunctionSearchDesign.md for the design these enforce.
"""
from __future__ import annotations

from isp.services.domain.chat_intent_split import (
    split_segments, classify_segments, parse_and_validate_confirmation,
    ConfirmationResult, build_search_plans, allocate_top_n, maybe_build_plans,
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


# ── Confirmation validation (§7) ─────────────────────────────────────────────

def test_valid_confirmation_parses():
    raw = '{"confirmed_segments": [0, 1], "shared_span": "iphone 17"}'
    r = parse_and_validate_confirmation(raw, "iphone 17 cases and headphones",
                                        ["iphone 17 cases", "headphones"], {0, 1})
    assert r == ConfirmationResult(confirmed_indices=[0, 1], shared_span="iphone 17")


def test_invented_index_is_rejected():
    raw = '{"confirmed_segments": [0, 2], "shared_span": null}'
    r = parse_and_validate_confirmation(raw, "cases and headphones",
                                        ["cases", "headphones"], {0, 1})
    assert r is None


def test_duplicate_index_is_rejected():
    raw = '{"confirmed_segments": [0, 0], "shared_span": null}'
    r = parse_and_validate_confirmation(raw, "cases and headphones",
                                        ["cases", "headphones"], {0, 1})
    assert r is None


def test_non_verbatim_shared_span_is_rejected():
    raw = '{"confirmed_segments": [0, 1], "shared_span": "iPhone Seventeen"}'
    r = parse_and_validate_confirmation(raw, "iphone 17 cases and headphones",
                                        ["iphone 17 cases", "headphones"], {0, 1})
    assert r is None


def test_shared_span_swap_attempt_is_rejected():
    """A model attempting to move a modifier between segments has no valid
    field to express it in — a shared_span equal to one segment's own price
    modifier, not present at all in the other, fails validation."""
    raw = '{"confirmed_segments": [0, 1], "shared_span": "$100"}'
    r = parse_and_validate_confirmation(
        raw, "red cases under $20 and noise-canceling headphones under $100",
        ["red cases under $20", "noise-canceling headphones under $100"], {0, 1},
    )
    assert r is None  # "$100" is inside segment 1 only, not segment 0 — ambiguous origin


def test_fewer_than_two_confirmed_is_rejected():
    raw = '{"confirmed_segments": [0], "shared_span": null}'
    r = parse_and_validate_confirmation(raw, "cases and headphones",
                                        ["cases", "headphones"], {0, 1})
    assert r is None


def test_malformed_json_is_rejected():
    assert parse_and_validate_confirmation("not json", "q", ["a", "b"], {0, 1}) is None


# ── Budget allocation (§11) ──────────────────────────────────────────────────

def test_budget_allocation_matches_formula():
    assert allocate_top_n(2, global_cap=5) == [3, 2]
    assert allocate_top_n(3, global_cap=5) == [2, 2, 1]
    assert allocate_top_n(4, global_cap=5) == [2, 1, 1, 1]


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


async def test_maybe_build_plans_single_intent_never_triggers():
    plans = await maybe_build_plans(
        "durable case for iphone 17",
        category_vectors={"a": [1.0], "b": [0.0]}, label_by_category={},
        embed=None, confirm=None, constraint_extractor=lambda t: None,
    )
    assert plans is None
