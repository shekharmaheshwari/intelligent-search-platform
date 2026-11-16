# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Rollback behaviour of ``_save_manual_doc`` when the Postgres write fails.

Typesense and Postgres are replaced with in-memory fakes so the failure can be
forced deterministically: a failed create must remove the new Typesense copy,
a failed update must restore the previously indexed version.
"""
from __future__ import annotations

import pytest

from isp.models import IndexMeta
from isp.routers import indexes as r


class FakeTypesense:
    def __init__(self, existing: dict[str, dict] | None = None):
        self.docs: dict[str, dict] = dict(existing or {})

    def create_collection(self, *_a, **_k): pass
    def collection_name(self, idx): return f"isp_{idx}"
    def retrieve_document(self, _col, doc_id): return dict(self.docs[doc_id]) if doc_id in self.docs else None
    def count_documents(self, _idx): return len(self.docs)

    def upsert_document(self, _idx, doc):
        self.docs[doc["id"]] = dict(doc)
        return None

    def delete_document(self, _idx, doc_id):
        self.docs.pop(doc_id, None)
        return True


@pytest.fixture
def env(monkeypatch):
    def setup(existing=None):
        fake = FakeTypesense(existing)
        for name in ("create_collection", "collection_name", "retrieve_document",
                     "count_documents", "upsert_document", "delete_document"):
            monkeypatch.setattr(r.ts, name, getattr(fake, name))

        async def no_embed(docs, model): pass
        async def dim(model): return 4
        async def failing_upsert(*_a): raise RuntimeError("postgres down")
        monkeypatch.setattr(r, "_embed_in_place", no_embed)
        monkeypatch.setattr(r, "_vec_dim_for", dim)
        monkeypatch.setattr(r.manual_docs, "upsert", failing_upsert)
        monkeypatch.setattr(r.embedding_service, "get_current_model", lambda: "test")
        return fake
    return setup


def _meta() -> IndexMeta:
    return IndexMeta(id="abc123", name="t", description="", created_at="2026-01-01")


async def test_failed_create_removes_new_typesense_copy(env):
    fake = env()
    with pytest.raises(RuntimeError):
        await r._save_manual_doc(_meta(), "manual-new", {"title": "T", "content": "C"}, is_update=False)
    assert "manual-new" not in fake.docs


async def test_failed_update_restores_previous_version(env):
    previous = {"id": "manual-1", "title": "Old", "content": "old body",
                "source_type": "manual", "indexed_at": 1, "vec": [0.1, 0.2, 0.3, 0.4]}
    fake = env({"manual-1": previous})
    with pytest.raises(RuntimeError):
        await r._save_manual_doc(_meta(), "manual-1", {"title": "New", "content": "C"}, is_update=True)
    assert fake.docs["manual-1"] == previous


async def test_successful_save_updates_only_doc_count(env, monkeypatch):
    """``meta`` is read before embedding; a full write could revert concurrent settings edits."""
    fake = env()
    async def ok_upsert(*_a): pass
    async def no_full_write(_meta): raise AssertionError("update_index must not be called")
    counts: list[tuple[str, int]] = []
    async def set_doc_count(idx, n): counts.append((idx, n))
    monkeypatch.setattr(r.manual_docs, "upsert", ok_upsert)
    monkeypatch.setattr(r.index_service, "update_index", no_full_write)
    monkeypatch.setattr(r.index_service, "set_doc_count", set_doc_count)

    await r._save_manual_doc(_meta(), "manual-2", {"title": "T", "content": "C"}, is_update=False)
    assert "manual-2" in fake.docs
    assert counts == [("abc123", 1)]
