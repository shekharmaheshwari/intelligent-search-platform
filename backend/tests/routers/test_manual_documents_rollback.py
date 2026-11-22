# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Rollback behaviour of ``_save_manual_doc`` when the Postgres write fails.

Typesense and Postgres are replaced with in-memory fakes so the failure can be
forced deterministically: a failed create must remove the new Typesense copy,
a failed update must restore the previously indexed version.
"""
from __future__ import annotations

import re

import pytest

from isp.models import IndexMeta
from isp.routers import indexes as r


def _family(flt: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", flt))


class FakeTypesense:
    """In-memory collection supporting the family / stale filters manual saves use."""
    def __init__(self, existing: dict[str, dict] | None = None):
        self.docs: dict[str, dict] = dict(existing or {})
        self.reject: set[str] = set()      # record ids rejected once (the bad new version)

    def _in_family(self, d: dict, flt: str) -> bool:
        ids = _family(flt)
        return d["id"] in ids or d.get("parent_id") in ids

    def import_documents(self, _idx, docs):
        errors = []
        for d in docs:
            if d["id"] in self.reject:
                self.reject.discard(d["id"])
                errors.append("rejected")
            else:
                self.docs[d["id"]] = dict(d)
                errors.append(None)
        return errors

    def export_documents(self, _idx, _fields, flt):
        return [dict(d) for d in self.docs.values() if self._in_family(d, flt)]

    fail_deletes = False

    def delete_by_filter(self, _idx, flt):
        if self.fail_deletes:
            raise RuntimeError("typesense down")
        return self.delete_from_collection(None, flt)

    def delete_from_collection(self, _col, flt):
        m = re.search(r"chunk_count:!=(\d+)", flt)
        doomed = [i for i, d in self.docs.items() if self._in_family(d, flt)
                  and (not m or d.get("chunk_count") != int(m.group(1)))]
        for i in doomed:
            del self.docs[i]
        return len(doomed)

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
                     "count_documents", "upsert_document", "delete_document",
                     "import_documents", "export_documents", "delete_from_collection",
                     "delete_by_filter"):
            monkeypatch.setattr(r.ts, name, getattr(fake, name))

        async def no_embed(docs, model): pass
        async def dim(model): return 4
        async def failing_upsert(*_a): raise RuntimeError("postgres down")
        monkeypatch.setattr(r, "_embed_in_place", no_embed)
        monkeypatch.setattr(r, "_vec_dim_for", dim)
        monkeypatch.setattr(r.manual_docs, "upsert", failing_upsert)
        monkeypatch.setattr(r.embedding_service, "get_current_model", lambda: "test")
        async def no_db(*_a): pass
        monkeypatch.setattr(r.index_service, "merge_chunking", no_db)
        monkeypatch.setattr(r.index_service, "set_doc_count", no_db)
        return fake
    return setup


def _meta(**chunking) -> IndexMeta:
    return IndexMeta(id="abc123", name="t", description="", created_at="2026-01-01",
                     doc_count=1, chunking=chunking)


# Collection already built chunked at 50 tokens (200 chars).
_APPLIED = {"enabled": True, "chunk_tokens": 50, "overlap_tokens": 10, "min_chunk_tokens": 5}
_LONG = " ".join(f"Sentence number {i} has some words." for i in range(40))


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


async def test_long_manual_doc_is_split_when_collection_is_chunked(env, monkeypatch):
    fake = env()
    async def ok_upsert(*_a): pass
    monkeypatch.setattr(r.manual_docs, "upsert", ok_upsert)
    meta = _meta(**_APPLIED, applied=_APPLIED)
    out = await r._save_manual_doc(meta, "manual-3", {"title": "T", "content": _LONG}, is_update=False)
    chunks = [d for d in fake.docs.values() if d.get("parent_id") == "manual-3"]
    assert len(chunks) > 1 and out["id"] == "manual-3.chunk0"


async def test_update_to_shorter_doc_removes_old_chunks(env, monkeypatch):
    fake = env()
    async def ok_upsert(*_a): pass
    monkeypatch.setattr(r.manual_docs, "upsert", ok_upsert)
    meta = _meta(**_APPLIED, applied=_APPLIED)
    await r._save_manual_doc(meta, "manual-4", {"title": "T", "content": _LONG}, is_update=False)
    await r._save_manual_doc(meta, "manual-4", {"title": "T", "content": "Short now."}, is_update=True)
    assert set(fake.docs) == {"manual-4"}


async def test_failed_update_restores_every_previous_chunk(env, monkeypatch):
    fake = env()
    async def ok_upsert(*_a): pass
    monkeypatch.setattr(r.manual_docs, "upsert", ok_upsert)
    meta = _meta(**_APPLIED, applied=_APPLIED)
    await r._save_manual_doc(meta, "manual-5", {"title": "T", "content": _LONG}, is_update=False)
    before = {k: dict(v) for k, v in fake.docs.items()}
    async def failing_upsert(*_a): raise RuntimeError("postgres down")
    monkeypatch.setattr(r.manual_docs, "upsert", failing_upsert)
    with pytest.raises(RuntimeError):
        await r._save_manual_doc(meta, "manual-5", {"title": "T", "content": "Short now."}, is_update=True)
    assert fake.docs == before


async def test_rejected_chunk_rolls_back_and_reports(env):
    fake = env()
    fake.reject = {"manual-6.chunk1"}
    meta = _meta(**_APPLIED, applied=_APPLIED)
    with pytest.raises(r.HTTPException) as exc:
        await r._save_manual_doc(meta, "manual-6", {"title": "T", "content": _LONG}, is_update=False)
    assert exc.value.status_code == 400
    assert not fake.docs


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


async def test_failed_rebuild_keeps_existing_chunks_grouped(monkeypatch):
    """``applied`` is only cleared when the collection is actually replaced."""
    writes: list[dict] = []
    async def merge(_idx, fields): writes.append(fields)
    monkeypatch.setattr(r.index_service, "merge_chunking", merge)
    meta = _meta(**_APPLIED, applied=_APPLIED)
    plan = await r._chunking_plan(meta, "sitemap", rebuild=True)
    assert plan is not None and writes == []          # nothing cleared before the fetch
    await r._clear_applied("abc123", meta)            # at drop time
    assert writes == [{"applied": None}]


def test_chunk_fields_are_protected():
    assert {"parent_id", "chunk_index", "chunk_count", "chunk_tail"} <= r._FIELD_SYSTEM
    assert {"parent_id", "chunk_tail"} <= r._MANUAL_HIDDEN_FIELDS


async def test_delete_keeps_postgres_source_when_typesense_delete_fails(env, monkeypatch):
    fake = env({"manual-7": {"id": "manual-7", "title": "T", "content": "C"}})
    fake.fail_deletes = True
    pg_deleted: list[str] = []
    async def pg_delete(_idx, doc_id): pg_deleted.append(doc_id); return True
    async def resolve(_idx): return _meta()
    monkeypatch.setattr(r.manual_docs, "delete", pg_delete)
    monkeypatch.setattr(r, "_manual_index_or_404", resolve)
    with pytest.raises(r.HTTPException) as exc:
        await r.delete_manual_document("abc123", "manual-7")
    assert exc.value.status_code == 500 and pg_deleted == [] and "manual-7" in fake.docs


async def test_failed_stale_cleanup_rolls_back_the_update(env, monkeypatch):
    fake = env()
    async def ok_upsert(*_a): pass
    monkeypatch.setattr(r.manual_docs, "upsert", ok_upsert)
    meta = _meta(**_APPLIED, applied=_APPLIED)
    await r._save_manual_doc(meta, "manual-8", {"title": "T", "content": _LONG}, is_update=False)
    before = {k: dict(v) for k, v in fake.docs.items()}
    fake.fail_deletes = True
    with pytest.raises(r.HTTPException):
        await r._save_manual_doc(meta, "manual-8", {"title": "T", "content": "Short now."}, is_update=True)
    assert set(fake.docs) >= set(before)       # nothing of the old layout was lost


async def test_crawl_failing_after_first_batch_still_marks_chunks_applied(monkeypatch):
    marks: list[dict] = []
    async def merge(_idx, fields): marks.append(fields)
    async def nothing(*_a, **_k): pass
    async def same(batch, _exts): return batch
    calls = {"n": 0}
    async def embed(docs, _model):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("embedding provider down")
    async def crawl(_cfg, on_batch, **_k):
        await on_batch([{"id": "p1", "title": "T", "content": _LONG}])
        await on_batch([{"id": "p2", "title": "T", "content": _LONG}])
    monkeypatch.setattr(r.index_service, "merge_chunking", merge)
    monkeypatch.setattr(r.index_service, "set_status", nothing)
    monkeypatch.setattr(r, "apply_extensions", same)
    monkeypatch.setattr(r, "_embed_in_place", embed)
    monkeypatch.setattr(r.sitemap_connector, "crawl_batches", crawl)
    monkeypatch.setattr(r.ts, "drop_collection", lambda *_a: None)
    monkeypatch.setattr(r.ts, "create_collection", lambda *_a, **_k: None)
    monkeypatch.setattr(r.ts, "import_documents", lambda _i, docs: [None] * len(docs))
    monkeypatch.setattr(r.embedding_service, "get_current_model", lambda: "voyage-3")
    meta = _meta(**_APPLIED)
    plan = r.chunking.load(_APPLIED)
    s = r.IngestStatus(index_id="abc123")
    with pytest.raises(RuntimeError):
        await r._rebuild_sitemap_batched("abc123", meta, {}, r.SitemapConnectorConfig(url="https://x"),
                                         [], s, plan)
    assert marks and marks[-1]["applied"]["chunk_tokens"] == 50


def _layout(fake, parent):
    return sorted((d["id"], d.get("chunk_count")) for d in fake.docs.values()
                  if d.get("parent_id") == parent or d["id"] == parent)


def test_partial_import_on_append_restores_the_previous_version(env):
    """N-chunk doc updated to fewer chunks, a later new chunk rejected → old chunks intact."""
    fake = env()
    plan = r.chunking.load(_APPLIED)
    old = r.chunking.chunk_documents([{"id": "p", "title": "T", "content": _LONG[:520]}], plan)
    r._upsert("abc123", old, plan, replace_chunks=True)
    before = {k: dict(v) for k, v in fake.docs.items()}

    new = r.chunking.chunk_documents([{"id": "p", "title": "T", "content": _LONG[:330]}], plan)
    assert len(old) > len(new) >= 2
    fake.reject = {new[1]["id"]}
    assert r._upsert("abc123", new, plan, replace_chunks=True) == 0
    assert fake.docs == before


def test_partial_import_on_rebuild_leaves_no_half_document(env):
    fake = env()
    plan = r.chunking.load(_APPLIED)
    docs = r.chunking.chunk_documents([{"id": "p", "title": "T", "content": _LONG},
                                       {"id": "q", "title": "Q", "content": "short"}], plan)
    fake.reject = {"p.chunk1"}
    assert r._upsert("abc123", docs, plan) == 1
    assert _layout(fake, "p") == [] and set(fake.docs) == {"q"}
