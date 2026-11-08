# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for query_boost.trainer.train — orchestration only.

The DB reads/writes (_fetch_query_events, store.save_model, _log_run) and
index lookup are patched, so these run without Postgres or Typesense. The
real DB round trip is covered by test_store.py.
"""
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from isp.services.domain.query_boost import trainer


def _rows(query: str, doc: str, n: int, clicks: int, rank: int = 0) -> list[dict]:
    """``n`` impression rows plus ``clicks`` separate click rows, as analytics logs them."""
    row = {"query": query, "doc_id": doc, "rank": rank}
    return [{**row, "clicked": False} for _ in range(n)] + [{**row, "clicked": True} for _ in range(clicks)]


VECTORS = {
    "cheap iphone":  [1.0, 0.0],
    "budget iphone": [0.97, 0.2],
    "galaxy fold":   [0.0, 1.0],
}


async def _embed(q: str) -> list[float]:
    return VECTORS.get(q, [])


@pytest.fixture
def patched():
    """Patch every I/O boundary; yield the mocks for assertions."""
    mocks = SimpleNamespace(
        fetch=AsyncMock(return_value=[]),
        save=AsyncMock(),
        log=AsyncMock(),
        get_index=AsyncMock(return_value=SimpleNamespace(id="idx", embed_model="m1")),
    )
    with patch.object(trainer, "_fetch_query_events", mocks.fetch), \
         patch.object(trainer.store, "save_model", mocks.save), \
         patch.object(trainer, "_log_run", mocks.log), \
         patch.object(trainer.index_service, "get_index", mocks.get_index):
        yield mocks


class TestIsExcludedQuery:
    @pytest.mark.parametrize("q", ["", "*", "cheapest phone"])
    def test_excluded(self, q):
        assert trainer.is_excluded_query(q)

    def test_regular_query_kept(self):
        assert not trainer.is_excluded_query("iphone 17 case")


class TestTrain:
    @pytest.mark.asyncio
    async def test_unknown_index_raises(self, patched):
        patched.get_index.return_value = None
        with pytest.raises(ValueError):
            await trainer.train("nope", embed=_embed)

    @pytest.mark.asyncio
    async def test_too_few_events_keeps_existing_model(self, patched):
        patched.fetch.return_value = _rows("cheap iphone", "se", 3, 1)
        result = await trainer.train("idx", embed=_embed)
        assert result["trained"] is False
        patched.save.assert_not_awaited()
        patched.log.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_excluded_queries_do_not_count_toward_the_minimum(self, patched):
        patched.fetch.return_value = _rows("cheapest phone", "a", 20, 5) + _rows("iphone", "se", 2, 1)
        result = await trainer.train("idx", embed=_embed)
        assert result["trained"] is False
        assert result["n_events"] == 28  # raw count (25 + 3 rows) still reported

    @pytest.mark.asyncio
    async def test_paraphrases_pool_into_one_cluster_and_produce_boosts(self, patched):
        patched.fetch.return_value = (
            _rows("Cheap iPhone", "iphone-se", 10, 7)
            + _rows("budget iphone", "iphone-se", 10, 6)
            + _rows("cheap iphone", "iphone-17-pro", 10, 0, rank=1)
            + _rows("galaxy fold", "fold-6", 10, 8)
            + _rows("galaxy fold", "galaxy-a17", 10, 0, rank=1)
        )
        result = await trainer.train("idx", embed=_embed)

        assert result["trained"] is True
        assert result["n_queries"] == 3
        assert result["n_clusters"] == 2

        (index_id, embed_model, clusters, boosts), _ = patched.save.call_args
        assert index_id == "idx" and embed_model == "m1"
        member_sets = [set(c.queries) for c in clusters]
        assert {"cheap iphone", "budget iphone"} in member_sets
        boosted = {b.doc_id for b in boosts}
        assert boosted == {"iphone-se", "fold-6"}

    @pytest.mark.asyncio
    async def test_without_embeddings_queries_still_train_by_exact_text(self, patched):
        patched.fetch.return_value = _rows("cheap iphone", "se", 10, 7) + _rows("cheap iphone", "pro", 10, 0)

        async def no_embed(q: str) -> list[float]:
            return []

        result = await trainer.train("idx", embed=no_embed)
        assert result["trained"] is True
        (_, embed_model, clusters, _), _ = patched.save.call_args
        assert embed_model == ""  # no centroids → serving never tries vector matching
        assert clusters[0].centroid is None

    @pytest.mark.asyncio
    async def test_embedding_errors_are_tolerated(self, patched):
        patched.fetch.return_value = _rows("cheap iphone", "se", 10, 7) + _rows("cheap iphone", "pro", 10, 0)

        async def broken(q: str) -> list[float]:
            raise RuntimeError("provider down")

        result = await trainer.train("idx", embed=broken)
        assert result["trained"] is True

    @pytest.mark.asyncio
    async def test_max_queries_caps_the_long_tail(self, patched, monkeypatch):
        monkeypatch.setattr(trainer, "MAX_QUERIES", 1)
        patched.fetch.return_value = _rows("cheap iphone", "se", 10, 7) + _rows("galaxy fold", "fold", 3, 2)
        result = await trainer.train("idx", embed=_embed)
        assert result["n_queries"] == 1
        (_, _, clusters, _), _ = patched.save.call_args
        assert [c.queries for c in clusters] == [["cheap iphone"]]

    @pytest.mark.asyncio
    async def test_logs_case_b_run(self, patched):
        patched.fetch.return_value = _rows("cheap iphone", "se", 10, 7) + _rows("cheap iphone", "pro", 10, 0)
        await trainer.train("idx", embed=_embed)
        (index_id, n_events, n_docs, n_clusters), _ = patched.log.call_args
        assert (index_id, n_events, n_docs, n_clusters) == ("idx", 27, 1, 1)  # 10+7 + 10 rows
