"""
Regression tests for chat/service.py's price-phrase stripping.

The bug: Typesense's lexical search leg matches the literal query text
against product fields — a price comparator phrase ("under $1000") is
already captured structurally as `filter_by`, but was still being left in
the text sent as `q`. Since no product's actual text contains words like
"under" or a literal "$1000", that phrase is pure noise for lexical
matching and can silently collapse the hit count to near zero (verified
live: "iphone under $1000" matched only 1 of 5 filter-eligible iPhones on
the lexical leg, and the LLM confidently reported it as the only match).

_strip_structural_phrases already did this for the superlative path
("cheapest iphone under $800"); these tests cover extending the same fix
to the plain price-filtered path via the shared _remove_price_phrase.
"""
from __future__ import annotations

from isp.services.infra.chat.service import _resolve_retrieval_params
from isp.services.infra.chat.query_filters import (
    extract_price_filter as _extract_price_filter,
    strip_price_phrase as _strip_price_phrase,
    _remove_price_phrase,
    strip_structural_phrases as _strip_structural_phrases,
)


# ── _strip_price_phrase (plain price-filtered path) ─────────────────────────

def test_strips_under_phrase_leaving_the_real_subject():
    assert _strip_price_phrase("iphone under $1000") == "iphone"


def test_strips_over_phrase():
    assert _strip_price_phrase("phones over $500") == "phones"


def test_strips_between_phrase():
    assert _strip_price_phrase("case between $20 and $50") == "case"


def test_no_price_phrase_is_a_no_op():
    assert _strip_price_phrase("iphone 17 case") == "iphone 17 case"


def test_falls_back_to_original_when_stripping_leaves_nothing():
    # No real subject at all — an empty `q` sent to Typesense is worse than
    # the unstripped original, so this deliberately does NOT strip.
    assert _strip_price_phrase("under $1000") == "under $1000"


def test_extracted_filter_matches_the_stripped_phrase():
    # Sanity check that filter_by and the stripped text agree on what was
    # actually a price phrase, for the same input.
    query = "iphone under $1000"
    assert _extract_price_filter(query) == "price:<1000"
    assert _strip_price_phrase(query) == "iphone"


# ── _strip_structural_phrases (superlative path) — must not regress ────────

def test_superlative_path_still_strips_both_phrases():
    assert _strip_structural_phrases("cheapest iphone under $800", "cheapest") == "iphone"


def test_superlative_path_falls_back_to_browse_all_not_the_price_phrase():
    # Regression case: composing _strip_price_phrase's own "preserve on
    # empty" fallback inside _strip_structural_phrases would incorrectly
    # leave "under $800" in place instead of falling through to "*" — this
    # locks in that _remove_price_phrase (no fallback) is what's shared,
    # not _strip_price_phrase.
    assert _strip_structural_phrases("cheapest under $800", "cheapest") == "*"


def test_superlative_path_falls_back_to_browse_all_with_no_price_either():
    assert _strip_structural_phrases("cheapest", "cheapest") == "*"


def test_remove_price_phrase_has_no_fallback():
    assert _remove_price_phrase("under $1000") == ""


# ── End-to-end through _resolve_retrieval_params ────────────────────────────

async def test_resolve_retrieval_params_strips_price_phrase_from_search_query():
    search_query, multi_intent_query, filter_by, _ = await _resolve_retrieval_params("iphone under $1000", [])
    assert search_query == "iphone"
    assert filter_by == "price:<1000"
    # multi_intent_query deliberately keeps the price phrase — multi-intent
    # segmentation needs it present to extract a per-segment constraint from
    # (see _resolve_retrieval_params's docstring); only search_query (used
    # for the single-search/superlative Typesense `q`) has it stripped.
    assert multi_intent_query == "iphone under $1000"


async def test_resolve_retrieval_params_leaves_plain_query_untouched():
    search_query, multi_intent_query, filter_by, _ = await _resolve_retrieval_params("iphone 17 case", [])
    assert search_query == "iphone 17 case"
    assert multi_intent_query == "iphone 17 case"
    assert filter_by is None
