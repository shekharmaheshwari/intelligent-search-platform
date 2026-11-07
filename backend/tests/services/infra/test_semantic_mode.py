# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
search_with_vec(semantic_only=True) — the "semantic" search mode.

Regression for the bug where mode="semantic" ran the same hybrid RRF as
mode="hybrid": a natural-language question ("how do I protect an endpoint with
a password") matched every stopword in a few long, unrelated pages, and the
0.7 lexical weight pushed those above the correct vector matches. Semantic
mode must rank by vector similarity alone.
"""
from __future__ import annotations

import pytest

from isp.services.infra import typesense_client as ts


def _hit(doc_id: str, **extra) -> dict:
    return {"document": {"id": doc_id, "title": doc_id, **extra}}


class _FakeMultiSearch:
    def __init__(self, lexical_hits, vector_hits, vector_facets=None):
        self.lexical_hits = lexical_hits
        self.vector_hits = vector_hits
        self.vector_facets = vector_facets or []
        self.calls: list[list[dict]] = []

    def perform(self, body, _params):
        searches = body["searches"]
        self.calls.append(searches)
        results = []
        for s in searches:
            if "vector_query" in s:
                r = {"hits": self.vector_hits}
                if "facet_by" in s:
                    r["facet_counts"] = self.vector_facets
                results.append(r)
            else:
                results.append({"hits": self.lexical_hits})
        return {"results": results}


class _FakeClient:
    def __init__(self, multi_search):
        self.multi_search = multi_search


@pytest.fixture
def fake_search(monkeypatch):
    def _install(lexical_hits, vector_hits, vector_facets=None):
        ms = _FakeMultiSearch(lexical_hits, vector_hits, vector_facets)
        monkeypatch.setattr(ts, "get_client", lambda: _FakeClient(ms))
        return ms
    return _install


LEXICAL = [_hit("alternatives"), _hit("generating-sdks")]
VECTOR = [_hit("advanced-security"), _hit("http-basic-auth"), _hit("alternatives")]
QUERY = "how do I protect an endpoint with a password"


def _titles(result: dict) -> list[str]:
    return [h["document"]["id"] for h in result["hits"]]


class TestSemanticOnly:
    def test_ranks_by_vector_order_ignoring_lexical_matches(self, fake_search):
        fake_search(LEXICAL, VECTOR)
        result = ts.search_with_vec("idx", QUERY, [0.1, 0.2], 1, 10, semantic_only=True)
        assert _titles(result) == ["advanced-security", "http-basic-auth", "alternatives"]

    def test_sends_only_the_vector_search(self, fake_search):
        ms = fake_search(LEXICAL, VECTOR)
        ts.search_with_vec("idx", QUERY, [0.1, 0.2], 1, 10, semantic_only=True)
        assert len(ms.calls) == 1
        (searches,) = ms.calls
        assert len(searches) == 1 and "vector_query" in searches[0]

    def test_facet_counts_come_from_the_vector_pass(self, fake_search):
        facets = [{"field_name": "category", "counts": [{"value": "security", "count": 2}]}]
        ms = fake_search(LEXICAL, VECTOR, vector_facets=facets)
        result = ts.search_with_vec("idx", QUERY, [0.1], 1, 10,
                                    facet_fields=["category"], semantic_only=True)
        assert ms.calls[0][0]["facet_by"] == "category"
        assert result["facet_counts"] == {"category": [{"value": "security", "count": 2}]}

    def test_featured_boost_still_applies(self, fake_search):
        vector = [_hit("advanced-security"), _hit("http-basic-auth", is_featured=True)]
        fake_search([], vector)
        result = ts.search_with_vec("idx", QUERY, [0.1], 1, 10, semantic_only=True)
        assert _titles(result)[0] == "http-basic-auth"


class TestHybridUnchanged:
    def test_hybrid_still_sends_lexical_and_vector_searches(self, fake_search):
        ms = fake_search(LEXICAL, VECTOR)
        ts.search_with_vec("idx", QUERY, [0.1], 1, 10)
        assert len(ms.calls[0]) == 2

    def test_hybrid_keeps_lexical_weighting(self, fake_search):
        fake_search(LEXICAL, VECTOR)
        result = ts.search_with_vec("idx", QUERY, [0.1], 1, 10)
        # "alternatives" is lexical #1 and vector #3, so RRF puts it first.
        assert _titles(result)[0] == "alternatives"
