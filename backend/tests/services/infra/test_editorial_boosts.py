# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Regression tests for typesense_client.apply_editorial_boosts — the shared
editorial ranking layer used by the lexical, hybrid (search_with_vec) and
cross-catalog (catalog._rrf_search) paths.

These lock in the ranking-blend fix (see docs/trainedRescorerDesign.md):

  - Point 1/2: on a non-empty query, popularity is a *bounded multiplicative*
    adjustment (relevance *= 1 + _POP_QUERY_COEFF * pop_norm), never additive — so a
    highly-popular but poorly-matching doc cannot overtake a clearly more
    relevant result. The motivating bug: a max-popularity Galaxy fold ranking
    above the iPhone 17 for the query "iphone 17".
  - Point 3: on an empty-query browse, popularity keeps its full additive
    strength (up to +0.5) and is the dominant ordering signal.
  - Point 4: is_featured / boost_queries_* stay additive merchandising
    overrides and may still outrank pure relevance by design.

Pure function, no I/O — no fixtures from conftest.py needed.
"""
from __future__ import annotations

import pytest

from isp.services.infra.typesense_client import (
    _POP_QUERY_COEFF,
    _rerank_flat,
    apply_editorial_boosts,
)


def _boost(docs: dict[str, dict], scores: dict[str, float], q: str, *, is_browse: bool):
    """Run apply_editorial_boosts on a copy of scores and return the result."""
    out = dict(scores)
    apply_editorial_boosts(out, docs, q, is_browse=is_browse)
    return out


class TestQueryRelevancePrimary:
    """Non-empty query: relevance is the constraint, popularity only nudges."""

    def test_query_coefficient_is_the_agreed_value(self):
        # Pin the intended contract. The other tests reference _POP_QUERY_COEFF
        # symbolically and so pass at any value; this one fails if the coefficient
        # is changed without a deliberate decision. Agreed value: 0.25 (raised
        # from 0.10 so popularity has real pull on broad brand queries while
        # specific queries stay protected — see docs/scoreBoost.md). Lower it
        # toward 0.10 if relevance-tier reranking is not yet in place.
        assert _POP_QUERY_COEFF == pytest.approx(0.25)

    def test_most_popular_unrelated_phone_does_not_outrank_iphone_lexical_match(self):
        # "iphone 17": the iPhone doc is a strong lexical+semantic match; the
        # Galaxy fold only shows up via semantic similarity, several ranks down,
        # but carries the maximum popularity score. Pre-fix it won with +0.5.
        docs = {
            "iphone17":  {"id": "iphone17",  "popularity": 300_000},
            "galaxyfold": {"id": "galaxyfold", "popularity": 1_000_000},
            "pixel":      {"id": "pixel",      "popularity": 500_000},
        }
        scores = {
            "iphone17":  0.7 / 61 + 0.3 / 64,  # lex rank 0, sem rank 3
            "galaxyfold": 0.3 / 68,            # sem rank 7, no lexical match
            "pixel":      0.3 / 70,
        }
        out = _boost(docs, scores, "iphone 17", is_browse=False)
        assert out["iphone17"] == max(out.values())

    def test_max_popularity_lift_equals_one_plus_the_coefficient(self):
        docs = {
            "a": {"id": "a", "popularity": 1_000_000},  # sets max_pop
            "b": {"id": "b", "popularity": 1_000_000},
        }
        out = _boost(docs, {"a": 0.010, "b": 0.010}, "phone", is_browse=False)
        assert out["b"] == pytest.approx(0.010 * (1 + _POP_QUERY_COEFF))

    def test_zero_popularity_doc_is_unchanged_by_popularity_on_a_query(self):
        docs = {"a": {"id": "a"}, "b": {"id": "b", "popularity": 1_000_000}}
        out = _boost(docs, {"a": 0.02, "b": 0.005}, "phone", is_browse=False)
        assert out["a"] == pytest.approx(0.02)

    def test_popularity_breaks_ties_between_equally_relevant_docs(self):
        docs = {
            "hi": {"id": "hi", "popularity": 1_000_000},
            "lo": {"id": "lo", "popularity": 10_000},
        }
        out = _boost(docs, {"hi": 0.015, "lo": 0.015}, "phone", is_browse=False)
        assert out["hi"] > out["lo"]


class TestBrowseFullPopularity:
    """Empty query: popularity is additive (up to +0.5) and dominates."""

    def test_popular_doc_leads_on_empty_query_browse(self):
        docs = {
            "relevant_ish": {"id": "relevant_ish", "popularity": 100_000},
            "very_popular": {"id": "very_popular", "popularity": 1_000_000},
        }
        # identical retrieval scores; only popularity separates them
        out = _boost(docs, {"relevant_ish": 0.016, "very_popular": 0.016}, "", is_browse=True)
        assert out["very_popular"] > out["relevant_ish"]
        assert out["very_popular"] == pytest.approx(0.016 + 0.5)

    def test_browse_popularity_is_additive_not_multiplicative(self):
        docs = {"d": {"id": "d", "popularity": 500_000}}
        out = _boost(docs, {"d": 0.01}, "*", is_browse=True)
        assert out["d"] == pytest.approx(0.01 + 500_000 / 2_000_000)


class TestMerchandisingOverrides:
    """Point 4: is_featured / boost_queries_* stay additive and may win."""

    def test_is_featured_still_outranks_a_more_relevant_unfeatured_doc(self):
        docs = {
            "featured": {"id": "featured", "is_featured": True},
            "relevant": {"id": "relevant"},
        }
        out = _boost(docs, {"featured": 0.002, "relevant": 0.016}, "phone", is_browse=False)
        assert out["featured"] > out["relevant"]

    def test_boost_queries_equal_adds_half_on_exact_query_match(self):
        docs = {"d": {"id": "d", "boost_queries_equal": ["cheap phone"]}}
        out = _boost(docs, {"d": 0.005}, "cheap phone", is_browse=False)
        assert out["d"] == pytest.approx(0.005 + 0.5)

    def test_boost_queries_contains_adds_point_three_on_substring_match(self):
        docs = {"d": {"id": "d", "boost_queries_contains": ["cheap phone"]}}
        out = _boost(docs, {"d": 0.005}, "buy a cheap phone today", is_browse=False)
        assert out["d"] == pytest.approx(0.005 + 0.3)

    def test_boost_queries_do_not_fire_on_empty_browse_query(self):
        docs = {"d": {"id": "d", "boost_queries_equal": ["cheap phone"]}}
        out = _boost(docs, {"d": 0.005}, "", is_browse=True)
        assert out["d"] == pytest.approx(0.005)


class TestRerankFlatIntegration:
    """_rerank_flat wires apply_editorial_boosts into the lexical hit list."""

    def test_unrelated_popular_doc_far_down_the_text_matches_cannot_reach_top(self):
        # 25 lexical hits; the max-popularity doc is at rank 20 — clearly a weak
        # text match. With K=60, the multiplicative lift 1/(61+r)·(1+_POP_QUERY_COEFF)
        # tops the rank-0 score only for r below ~(61·_POP_QUERY_COEFF); rank 20 is
        # well past that at any coefficient <= 0.30, so d0 stays first.
        hits = [{"document": {"id": f"d{i}", "popularity": 0}} for i in range(25)]
        hits[20]["document"]["popularity"] = 1_000_000
        out = _rerank_flat({"hits": hits}, "iphone 17", is_browse=False)
        assert out["hits"][0]["document"]["id"] == "d0"

    def test_browse_reorders_flat_list_by_popularity(self):
        hits = [
            {"document": {"id": "a", "popularity": 1_000}},
            {"document": {"id": "b", "popularity": 900_000}},
            {"document": {"id": "c", "popularity": 10_000}},
        ]
        out = _rerank_flat({"hits": hits}, "", is_browse=True)
        assert [h["document"]["id"] for h in out["hits"]] == ["b", "c", "a"]

    def test_empty_hit_list_is_returned_untouched(self):
        raw = {"hits": [], "found": 0}
        assert _rerank_flat(raw, "phone", is_browse=False) is raw

    def test_editorial_false_preserves_typesense_order_and_only_runs_hook(self):
        # Browse path: Typesense has already sorted by is_featured,popularity —
        # _rerank_flat(editorial=False) must not re-apply popularity (which would
        # double-count), only the affinity hook.
        hits = [
            {"document": {"id": "a", "popularity": 1_000}},
            {"document": {"id": "b", "popularity": 1_000_000}},
        ]
        out = _rerank_flat({"hits": hits}, "", True, editorial=False)
        assert [h["document"]["id"] for h in out["hits"]] == ["a", "b"]

        def _promote_b(scores, docs):
            scores["b"] += 1.0

        out = _rerank_flat({"hits": hits}, "", True, _promote_b, editorial=False)
        assert out["hits"][0]["document"]["id"] == "b"


class TestPinnedHitsPrecedence:
    """
    Pinned results are an operator merchandising override — no editorial signal
    or affinity hook may displace them. The lexical path re-ranks in Python
    after Typesense has forced the pins into position, so _rerank_flat must
    re-assert them (mirrors search_with_vec). Regression for the bug where a
    max-popularity doc leapfrogged a pinned zero-popularity doc.
    """

    def test_pinned_zero_popularity_doc_stays_first_above_a_max_popularity_doc(self):
        hits = [
            {"document": {"id": "pinned", "popularity": 0}},
            {"document": {"id": "popular", "popularity": 1_000_000}},
        ]
        out = _rerank_flat({"hits": hits}, "iphone 17", is_browse=False,
                           pinned_hits=["pinned"])
        assert out["hits"][0]["document"]["id"] == "pinned"

    def test_pinned_doc_outranks_an_is_featured_doc(self):
        hits = [
            {"document": {"id": "pinned"}},
            {"document": {"id": "featured", "is_featured": True}},
        ]
        out = _rerank_flat({"hits": hits}, "phone", is_browse=False,
                           pinned_hits=["pinned"])
        assert out["hits"][0]["document"]["id"] == "pinned"

    def test_multiple_pins_keep_their_given_order_at_the_top(self):
        hits = [
            {"document": {"id": "a", "is_featured": True}},
            {"document": {"id": "b", "popularity": 1_000_000}},
            {"document": {"id": "c"}},
        ]
        out = _rerank_flat({"hits": hits}, "phone", is_browse=False,
                           pinned_hits=["c", "a"])
        assert [h["document"]["id"] for h in out["hits"][:2]] == ["c", "a"]

    def test_pins_survive_an_affinity_hook_that_would_otherwise_promote_another_doc(self):
        hits = [
            {"document": {"id": "pinned"}},
            {"document": {"id": "boosted"}},
        ]

        def _promote_boosted(scores, docs):
            scores["boosted"] += 5.0

        out = _rerank_flat({"hits": hits}, "phone", False, _promote_boosted,
                           pinned_hits=["pinned"])
        assert out["hits"][0]["document"]["id"] == "pinned"

    def test_pin_absent_from_page_hits_is_ignored_without_error(self):
        hits = [
            {"document": {"id": "x", "popularity": 500_000}},
            {"document": {"id": "y", "popularity": 100_000}},
        ]
        out = _rerank_flat({"hits": hits}, "phone", is_browse=False,
                           pinned_hits=["not-on-this-page"])
        assert {h["document"]["id"] for h in out["hits"]} == {"x", "y"}

    def test_no_pinned_hits_leaves_editorial_order_unchanged(self):
        hits = [
            {"document": {"id": "lo", "popularity": 10_000}},
            {"document": {"id": "hi", "popularity": 1_000_000}},
        ]
        without = _rerank_flat({"hits": hits}, "", True, editorial=False)
        with_none = _rerank_flat({"hits": hits}, "", True, editorial=False,
                                 pinned_hits=None)
        assert ([h["document"]["id"] for h in without["hits"]]
                == [h["document"]["id"] for h in with_none["hits"]])
