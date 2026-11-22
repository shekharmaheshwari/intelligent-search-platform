# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Chat retrieval on chunked indexes (docs/chunkingDesign.md §8.2)."""
from __future__ import annotations

from isp.services.infra import typesense_client as ts
from isp.services.infra.chat import retrieval as rt

_LONG = "intro " * 100 + "the matching phrase is here at the end"


def test_grouped_params_ask_for_two_passages_per_document():
    searches = rt._build_search_params("q", "isp_x", [0.1, 0.2], "title,content", None,
                                       "chatContent", None, grouped=True)
    assert all(s["group_by"] == "parent_id" and s["group_limit"] == 2 for s in searches)
    assert f"k:{rt._GROUPED_K}" in searches[-1]["vector_query"]


def test_ungrouped_params_are_unchanged():
    searches = rt._build_search_params("q", "isp_x", [0.1], "title,content", None, None, None)
    assert all("group_by" not in s for s in searches)
    assert f"k:{rt._RRF_N}" in searches[-1]["vector_query"]


def test_flatten_keeps_every_passage_of_each_group_in_order():
    raw = {"found": 2, "grouped_hits": [
        {"hits": [{"document": {"id": "a.chunk3"}}, {"document": {"id": "a.chunk0"}}]},
        {"hits": [{"document": {"id": "b"}}]},
    ]}
    assert [h["document"]["id"] for h in ts.flatten_groups(raw)["hits"]] == ["a.chunk3", "a.chunk0", "b"]


def test_single_chunk_document_sends_full_content():
    doc = {"id": "p1", "title": "T", "content": _LONG, "parent_id": "p1", "chunk_count": 1}
    _, block = rt._hit_to_card_and_block({"document": doc}, 1)
    assert "matching phrase" in block


def test_unchunked_index_still_truncates():
    _, block = rt._hit_to_card_and_block({"document": {"id": "p1", "title": "T", "content": _LONG}}, 1)
    assert "matching phrase" not in block


def test_one_document_cannot_fill_the_context():
    docs = [{"id": f"a.chunk{i}", "parent_id": "a", "chunk_count": 9} for i in range(9)]
    docs += [{"id": "b", "parent_id": "b", "chunk_count": 1}, {"id": "c", "parent_id": "c", "chunk_count": 1}]
    hits = rt._rrf_merge([{"hits": [{"document": d} for d in docs]}], has_struct=False, has_vec=False)
    assert [h["document"]["id"] for h in hits] == ["a.chunk0", "a.chunk1", "b", "c"]
