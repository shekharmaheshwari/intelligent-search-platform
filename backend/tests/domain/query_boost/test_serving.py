# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for query_boost.serving — the query-time lookup and hook.

store.load_model is patched, so these run without Postgres.
"""
from __future__ import annotations
from unittest.mock import AsyncMock, patch

import pytest

from isp.services.domain.query_boost import serving
from isp.services.domain.query_boost.store import QueryBoostModel


def _model(**overrides) -> QueryBoostModel:
    m = QueryBoostModel(
        index_id="idx",
        embed_model="m1",
        query_to_cluster={"cheap iphone": 0, "galaxy fold": 1},
        centroids={0: [1.0, 0.0], 1: [0.0, 1.0]},
        boosts={0: {"iphone-se": 0.8}, 1: {"fold-6": 0.5}},
    )
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


class TestBoostsForQuery:
    def test_exact_query(self):
        assert serving.boosts_for_query(_model(), "Cheap iPhone") == {"iphone-se": 0.8}

    def test_vector_match(self):
        assert serving.boosts_for_query(_model(), "budget iphone", [0.98, 0.1]) == {"iphone-se": 0.8}

    @pytest.mark.parametrize("q", ["", "   ", "*"])
    def test_browse_never_boosts(self, q):
        assert serving.boosts_for_query(_model(query_to_cluster={"": 0, "*": 0}), q) == {}

    def test_no_model(self):
        assert serving.boosts_for_query(None, "cheap iphone") == {}

    def test_unmatched_query(self):
        assert serving.boosts_for_query(_model(), "laptop") == {}


class TestResolve:
    @pytest.mark.asyncio
    async def test_exact_match_does_not_embed(self):
        embed = AsyncMock(return_value=[1.0, 0.0])
        with patch.object(serving.store, "load_model", AsyncMock(return_value=_model())):
            out = await serving.resolve("idx", "cheap iphone", embed_model="m1", embed=embed)
        assert out == {"iphone-se": 0.8}
        embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_embedding_for_unseen_query(self):
        embed = AsyncMock(return_value=[0.97, 0.2])
        with patch.object(serving.store, "load_model", AsyncMock(return_value=_model())):
            out = await serving.resolve("idx", "budget iphone", embed_model="m1", embed=embed)
        assert out == {"iphone-se": 0.8}

    @pytest.mark.asyncio
    async def test_no_embedding_when_caller_gives_none(self):
        with patch.object(serving.store, "load_model", AsyncMock(return_value=_model())):
            assert await serving.resolve("idx", "budget iphone", embed_model="m1") == {}

    @pytest.mark.asyncio
    async def test_skips_vector_match_when_embedding_model_changed(self):
        embed = AsyncMock(return_value=[0.97, 0.2])
        with patch.object(serving.store, "load_model", AsyncMock(return_value=_model())):
            out = await serving.resolve("idx", "budget iphone", embed_model="m2", embed=embed)
        assert out == {}
        embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_embedding_failure_means_no_boost_not_an_error(self):
        embed = AsyncMock(side_effect=RuntimeError("provider down"))
        with patch.object(serving.store, "load_model", AsyncMock(return_value=_model())):
            assert await serving.resolve("idx", "budget iphone", embed_model="m1", embed=embed) == {}

    @pytest.mark.asyncio
    async def test_browse_does_not_touch_the_store(self):
        load = AsyncMock()
        with patch.object(serving.store, "load_model", load):
            assert await serving.resolve("idx", "", embed_model="m1") == {}
        load.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_known_query_in_unboosted_cluster_gets_no_lift_and_no_fallthrough(self):
        # Regression: "iphone case" has its own cluster (2) that learned
        # nothing. Its vector is close to cluster 0's centroid, but it must not
        # fall through and pick up cluster 0's boosts.
        model = _model(query_to_cluster={"cheap iphone": 0, "iphone case": 2},
                       centroids={0: [1.0, 0.0], 2: [0.9, 0.44]})
        embed = AsyncMock(return_value=[0.99, 0.1])
        with patch.object(serving.store, "load_model", AsyncMock(return_value=model)):
            out = await serving.resolve("idx", "iphone case", embed_model="m1", embed=embed)
        assert out == {}
        embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_model_stored(self):
        with patch.object(serving.store, "load_model", AsyncMock(return_value=None)):
            assert await serving.resolve("idx", "cheap iphone", embed_model="m1") == {}


class TestMakeHook:
    def test_adds_scaled_boost_to_matching_docs_only(self):
        hook = serving.make_hook({"a": 1.0, "b": 0.5}, q="cheap iphone")
        scores = {"a": 0.01, "b": 0.01, "c": 0.01}
        hook(scores, {"a": {}, "b": {}, "c": {}})
        assert scores["a"] == pytest.approx(0.01 + serving.MAX_SCORE_BOOST)
        assert scores["b"] == pytest.approx(0.01 + 0.5 * serving.MAX_SCORE_BOOST)
        assert scores["c"] == pytest.approx(0.01)

    def test_empty_boosts_give_no_hook(self):
        assert serving.make_hook({}, q="cheap iphone") is None

    def test_custom_key(self):
        hook = serving.make_hook({"idx1:a": 1.0}, q="cheap iphone", key=lambda did, doc: f"{doc['_index_id']}:{did}")
        scores = {"a": 0.0}
        hook(scores, {"a": {"_index_id": "idx1"}})
        assert scores["a"] == pytest.approx(serving.MAX_SCORE_BOOST)

    def test_learned_boost_cannot_beat_a_featured_merchandising_boost(self):
        # Featured +0.5 is an operator override; the strongest learned boost on
        # a #1 RRF result must stay below it.
        rrf_top = 1.0 / 61
        assert rrf_top + serving.MAX_SCORE_BOOST < 0.5

    def test_strong_learned_boost_can_lift_a_mid_page_result_to_the_top(self):
        rrf = {f"d{r}": 1.0 / (60 + r + 1) for r in range(30)}
        hook = serving.make_hook({"d20": 1.0}, q="cheap iphone")
        hook(rrf, {d: {} for d in rrf})
        assert max(rrf, key=rrf.get) == "d20"


class TestExactTitleProtection:
    """
    Regression: the learned boost must never lift a sibling variant over a
    candidate whose title/alias exactly equals the query. A boost of 0.1
    adds 0.0008, more than the RRF gap between adjacent ranks.
    """

    def _rrf(self, docs: dict) -> dict:
        return {did: 1.0 / (60 + rank + 1) for rank, did in enumerate(docs)}

    def test_exact_title_match_suppresses_learned_boost(self):
        docs = {"iphone-17": {"title": "iPhone 17"}, "iphone-17-pro-max": {"title": "iPhone 17 Pro Max"}}
        scores = self._rrf(docs)
        before = dict(scores)
        serving.make_hook({"iphone-17-pro-max": 0.1}, q="  IPHONE 17 ")(scores, docs)
        assert scores == before
        assert max(scores, key=scores.get) == "iphone-17"

    def test_small_boost_would_otherwise_flip_adjacent_ranks(self):
        # Shows the guard is load-bearing: without an exact match, 0.1 is enough.
        docs = {"iphone-17": {"title": "iPhone 17"}, "iphone-17-pro-max": {"title": "iPhone 17 Pro Max"}}
        scores = self._rrf(docs)
        serving.make_hook({"iphone-17-pro-max": 0.1}, q="iphone 17 deals")(scores, docs)
        assert max(scores, key=scores.get) == "iphone-17-pro-max"

    @pytest.mark.parametrize("aliases", ["apple watch series 11 46mm", ["x", "Apple Watch Series 11 46mm"]])
    def test_alias_match_counts_as_exact(self, aliases):
        docs = {"w46": {"title": "Watch S11", "aliases": aliases}, "w42": {"title": "Watch S11 42"}}
        scores = self._rrf(docs)
        before = dict(scores)
        serving.make_hook({"w42": 1.0}, q="apple watch series 11 46mm")(scores, docs)
        assert scores == before

    def test_partial_title_match_does_not_suppress(self):
        docs = {"a": {"title": "iPhone 17 case"}}
        assert not serving.has_exact_title_match(docs, "iphone 17")

    @pytest.mark.parametrize("doc", [{}, {"title": None}, {"aliases": 5}, {"aliases": [None, 3]}])
    def test_missing_or_odd_fields_are_ignored(self, doc):
        assert not serving.has_exact_title_match({"d": doc}, "iphone 17")


class TestChainHooks:
    def test_all_none(self):
        assert serving.chain_hooks(None, None) is None

    def test_single_hook_returned_as_is(self):
        def h(scores, docs):
            pass
        assert serving.chain_hooks(None, h) is h

    def test_runs_in_order(self):
        calls = []
        h = serving.chain_hooks(lambda s, d: calls.append(1), None, lambda s, d: calls.append(2))
        h({}, {})
        assert calls == [1, 2]
