# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Tests for popularity_trainer.train_and_apply — the Case A trained
re-scorer's domain-service logic (see docs/popularityTrainerImplementation.md).

Router-level integration tests (auth guard, unknown index, missing-Typesense
resilience) live in tests/routers/test_training.py and run against an
already-running server process, so patching typesense_client from the test
process has no effect on them. These tests call train_and_apply directly
against the real Postgres test DB (never mocked — see conftest.py) and patch
only typesense_client, to target the three edge cases fixed after code
review:

  - Fix 1: an all-empty feature matrix (no affinity dims, no is_featured/
    rating/recency on any document) must degrade gracefully instead of
    crashing DictVectorizer/LogisticRegression on a zero-column matrix.
  - Fix 2: the _MIN_EVENTS threshold must be re-checked against usable
    examples after joining events to documents, not just the raw query
    count — events referencing deleted/missing docs get dropped in the join.
  - Fix 3: the held-out AUC split must be grouped by doc_id so a document's
    events can't leak across train/test.
"""
from __future__ import annotations
from unittest.mock import patch

import pytest

from isp.services.domain import popularity_trainer
from isp.services.infra import database as database_module
from tests.conftest import seed_events


def _list_documents_response(docs: list[dict]) -> dict:
    return {"hits": [{"document": d} for d in docs]}


@pytest.fixture(autouse=True)
async def _fresh_pool_per_test():
    """
    database.get_pool() is a lazy module-level singleton bound to the event
    loop it was first created on. pytest-asyncio gives each test function
    its own event loop, so a pool cached from a previous test raises
    "Event loop is closed" here. Reset it around every test so it's
    (re)created on the current loop — the router-level tests in
    tests/routers/ never hit this because they talk to an already-running
    server process over HTTP instead of importing isp/asyncpg in-process.
    """
    database_module._pool = None
    yield
    if database_module._pool is not None:
        await database_module._pool.close()
        database_module._pool = None


class TestZeroFeatureMatrix:
    """Fix 1: an all-empty feature matrix must not crash training."""

    @pytest.mark.asyncio
    async def test_returns_zero_docs_scored_when_no_document_has_any_feature(self, db, test_index):
        # 6 browse events, mixed labels — clears _MIN_EVENTS and the
        # label-diversity check, so the run reaches vectorization.
        await seed_events(db, test_index["id"], [
            {"query": "", "doc_id": f"d{i}", "rank": i, "clicked": i % 2 == 0}
            for i in range(6)
        ])
        # test_index has no affinity_dimensions configured, and these docs
        # carry none of is_featured / rating / indexed_at — every feature
        # dict comes out {} and DictVectorizer produces a zero-column matrix.
        docs = [{"id": f"d{i}"} for i in range(6)]
        with patch(
            "isp.services.domain.popularity_trainer.ts.list_documents",
            return_value=_list_documents_response(docs),
        ), patch(
            "isp.services.domain.popularity_trainer.ts.update_document_fields",
        ) as mock_update:
            result = await popularity_trainer.train_and_apply(test_index["id"])

        assert result["docs_scored"] == 0
        assert mock_update.call_count == 0

    @pytest.mark.asyncio
    async def test_logs_empty_training_run_when_feature_matrix_is_empty(self, db, test_index):
        await seed_events(db, test_index["id"], [
            {"query": "", "doc_id": f"d{i}", "rank": i, "clicked": i % 2 == 0}
            for i in range(6)
        ])
        docs = [{"id": f"d{i}"} for i in range(6)]
        with patch(
            "isp.services.domain.popularity_trainer.ts.list_documents",
            return_value=_list_documents_response(docs),
        ), patch(
            "isp.services.domain.popularity_trainer.ts.update_document_fields",
        ):
            await popularity_trainer.train_and_apply(test_index["id"])

        row = await db.fetchrow(
            "SELECT n_events, n_docs_scored FROM training_runs WHERE index_id = $1",
            test_index["id"],
        )
        assert row["n_events"] == 6
        assert row["n_docs_scored"] == 0


class TestPostJoinThreshold:
    """Fix 2: _MIN_EVENTS must hold against usable examples, not raw rows."""

    @pytest.mark.asyncio
    async def test_returns_zero_docs_scored_when_usable_examples_fall_below_threshold(self, db, test_index):
        # 6 raw browse events clear _MIN_EVENTS, but only 2 of the
        # referenced doc_ids still exist in Typesense — the other 4 are
        # stale/deleted and get dropped by the event-to-document join.
        await seed_events(db, test_index["id"], [
            {"query": "", "doc_id": "kept-0", "rank": 0, "clicked": True},
            {"query": "", "doc_id": "kept-1", "rank": 1, "clicked": False},
            {"query": "", "doc_id": "gone-0", "rank": 2, "clicked": True},
            {"query": "", "doc_id": "gone-1", "rank": 3, "clicked": False},
            {"query": "", "doc_id": "gone-2", "rank": 4, "clicked": True},
            {"query": "", "doc_id": "gone-3", "rank": 5, "clicked": False},
        ])
        docs = [
            {"id": "kept-0", "is_featured": True},
            {"id": "kept-1"},
        ]
        with patch(
            "isp.services.domain.popularity_trainer.ts.list_documents",
            return_value=_list_documents_response(docs),
        ), patch(
            "isp.services.domain.popularity_trainer.ts.update_document_fields",
        ) as mock_update:
            result = await popularity_trainer.train_and_apply(test_index["id"])

        assert result["docs_scored"] == 0
        assert mock_update.call_count == 0

    @pytest.mark.asyncio
    async def test_reports_raw_event_count_even_when_usable_examples_are_fewer(self, db, test_index):
        await seed_events(db, test_index["id"], [
            {"query": "", "doc_id": "kept-0", "rank": 0, "clicked": True},
            {"query": "", "doc_id": "kept-1", "rank": 1, "clicked": False},
            {"query": "", "doc_id": "gone-0", "rank": 2, "clicked": True},
            {"query": "", "doc_id": "gone-1", "rank": 3, "clicked": False},
            {"query": "", "doc_id": "gone-2", "rank": 4, "clicked": True},
            {"query": "", "doc_id": "gone-3", "rank": 5, "clicked": False},
        ])
        docs = [
            {"id": "kept-0", "is_featured": True},
            {"id": "kept-1"},
        ]
        with patch(
            "isp.services.domain.popularity_trainer.ts.list_documents",
            return_value=_list_documents_response(docs),
        ), patch(
            "isp.services.domain.popularity_trainer.ts.update_document_fields",
        ):
            result = await popularity_trainer.train_and_apply(test_index["id"])

        assert result["n_events"] == 6


class TestGroupedHeldOutSplit:
    """Fix 3: the eval split is grouped by doc_id, not per-event."""

    @pytest.mark.asyncio
    async def test_trains_and_scores_successfully_across_multiple_documents(self, db, test_index):
        # >=10 events across 5 distinct docs so the grouped held-out split
        # actually runs; each doc carries mixed labels across its own
        # events and an is_featured/rating signal so the feature matrix
        # isn't empty.
        events = [
            {"query": "", "doc_id": f"d{i}", "rank": j, "clicked": (i + j) % 2 == 0}
            for i in range(5) for j in range(3)
        ]
        await seed_events(db, test_index["id"], events)
        docs = [
            {"id": f"d{i}", "is_featured": i % 2 == 0, "averageStarRating": 4.0, "ratingsCount": 10}
            for i in range(5)
        ]
        with patch(
            "isp.services.domain.popularity_trainer.ts.list_documents",
            return_value=_list_documents_response(docs),
        ), patch(
            "isp.services.domain.popularity_trainer.ts.update_document_fields",
            return_value=True,
        ) as mock_update:
            result = await popularity_trainer.train_and_apply(test_index["id"])

        assert result["docs_scored"] == 5
        assert mock_update.call_count == 5

    @pytest.mark.asyncio
    async def test_auc_is_none_instead_of_raising_when_grouped_split_yields_a_single_class(self, db, test_index):
        # Only 2 distinct documents, one always-clicked and one
        # never-clicked. A doc_id-grouped split with only 2 groups puts
        # each whole document on one side, so one side of the split ends
        # up single-class. Under the old per-event split (stratify=y) both
        # classes were guaranteed on both sides; the grouped split must
        # catch the resulting ValueError from LogisticRegression.fit and
        # fall back to auc=None instead of propagating the exception.
        events = [
            {"query": "", "doc_id": "always-clicked", "rank": j, "clicked": True}
            for j in range(6)
        ] + [
            {"query": "", "doc_id": "never-clicked", "rank": j, "clicked": False}
            for j in range(6)
        ]
        await seed_events(db, test_index["id"], events)
        docs = [
            {"id": "always-clicked", "is_featured": True},
            {"id": "never-clicked"},
        ]
        with patch(
            "isp.services.domain.popularity_trainer.ts.list_documents",
            return_value=_list_documents_response(docs),
        ), patch(
            "isp.services.domain.popularity_trainer.ts.update_document_fields",
            return_value=True,
        ):
            result = await popularity_trainer.train_and_apply(test_index["id"])

        assert result["auc"] is None

    @pytest.mark.asyncio
    async def test_still_scores_documents_when_grouped_split_yields_a_single_class(self, db, test_index):
        # Same degenerate-split scenario as above — the eval split failing
        # must not stop the real model (fit on the full data) from training
        # and writing scores back.
        events = [
            {"query": "", "doc_id": "always-clicked", "rank": j, "clicked": True}
            for j in range(6)
        ] + [
            {"query": "", "doc_id": "never-clicked", "rank": j, "clicked": False}
            for j in range(6)
        ]
        await seed_events(db, test_index["id"], events)
        docs = [
            {"id": "always-clicked", "is_featured": True},
            {"id": "never-clicked"},
        ]
        with patch(
            "isp.services.domain.popularity_trainer.ts.list_documents",
            return_value=_list_documents_response(docs),
        ), patch(
            "isp.services.domain.popularity_trainer.ts.update_document_fields",
            return_value=True,
        ) as mock_update:
            result = await popularity_trainer.train_and_apply(test_index["id"])

        assert result["docs_scored"] == 2
        assert mock_update.call_count == 2
