# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the pure chunking module (docs/chunkingDesign.md)."""
from __future__ import annotations

from isp.services.domain import chunking as ch

CFG = ch.ChunkingConfig(enabled=True, chunk_tokens=50, overlap_tokens=10, min_chunk_tokens=5)  # 200 / 40 / 20 chars


def _sentences(n: int, prefix: str = "Sentence") -> str:
    return " ".join(f"{prefix} number {i} has some words." for i in range(n))


# ── split_text ───────────────────────────────────────────────────────────────

def test_short_text_is_one_chunk():
    assert ch.split_text("Just one line.", CFG) == ["Just one line."]


def test_chunks_respect_size():
    parts = ch.split_text(_sentences(40), CFG)
    assert len(parts) > 1
    assert all(len(p) <= CFG.size for p in parts)


def test_no_text_is_lost():
    text = _sentences(40)
    joined = " ".join(ch.split_text(text, CFG))
    for i in range(40):
        assert f"number {i} has" in joined


def test_overlap_repeats_previous_sentence():
    parts = ch.split_text(_sentences(40), CFG)
    last_sentence = parts[0].rsplit(". ", 1)[-1].rstrip(".")
    assert parts[1].startswith(last_sentence)


def test_prefers_paragraph_breaks():
    para_a = "Alpha intro sentence here. " * 4       # ~108 chars
    para_b = "Beta topic sentence here. " * 4
    parts = ch.split_text(f"{para_a.strip()}\n\n{para_b.strip()}\n\n{para_a.strip()}", CFG)
    assert parts[0].startswith("Alpha") and "Beta" not in parts[0]
    assert parts[1].startswith("Beta")


def test_long_sentence_is_hard_cut():
    parts = ch.split_text("x" * 1000, CFG)
    assert len(parts) == 5
    assert all(len(p) <= CFG.size for p in parts)


def test_tiny_tail_merges_into_previous():
    text = _sentences(5) + " End."            # "End." alone is under min size
    parts = ch.split_text(text, CFG)
    assert parts[-1].endswith("End.")
    assert all(len(p) >= CFG.min_size for p in parts)


# ── chunk_documents ──────────────────────────────────────────────────────────

def test_short_doc_keeps_id_and_gets_parent():
    [out] = ch.chunk_documents([{"id": "p1", "title": "T", "content": "short"}], CFG)
    assert out["id"] == "p1" and out["parent_id"] == "p1"
    assert out["chunk_index"] == 0 and out["chunk_count"] == 1
    assert "chunk_tail" not in out


def test_long_doc_becomes_chunks_with_copied_fields():
    doc = {"id": "p1", "title": "T", "url": "u", "content": _sentences(40), "vec": [0.1]}
    out = ch.chunk_documents([doc], CFG)
    assert len(out) > 1
    assert [c["id"] for c in out] == [f"p1.chunk{i}" for i in range(len(out))]
    assert {c["parent_id"] for c in out} == {"p1"}
    assert all(c["title"] == "T" and c["url"] == "u" and "vec" not in c for c in out)
    assert all(c["chunk_count"] == len(out) for c in out)
    assert "chunk_tail" not in out[0] and all(c["chunk_tail"] for c in out[1:])


def test_records_of_a_chunked_index_are_recognisable():
    [short] = ch.chunk_documents([{"id": "p1", "title": "T", "content": "short"}], CFG)
    assert ch.from_chunked_index(short) and not ch.is_chunk(short)
    assert not ch.from_chunked_index({"id": "plain", "content": "x"})


def test_chunk_ids_cannot_collide_with_source_ids():
    out = ch.chunk_documents([{"id": "page", "title": "T", "content": _sentences(40)}], CFG)
    # Source ids are [A-Za-z0-9_-]; every split chunk id carries the reserved ".".
    assert all("." in d["id"] for d in out)
    assert all(c.isalnum() or c in "-_." for d in out for c in d["id"])


def test_source_id_with_reserved_dot_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        ch.chunk_documents([{"id": "page.chunk0", "title": "T", "content": "x"}], CFG)


# ── helpers ──────────────────────────────────────────────────────────────────

def test_collapse_keeps_best_chunk_per_parent():
    docs = [{"id": "a.chunk2", "parent_id": "a"}, {"id": "b", "parent_id": "b"},
            {"id": "a.chunk0", "parent_id": "a"}, {"id": "legacy"}]
    assert [d["id"] for d in ch.collapse(docs)] == ["a.chunk2", "b", "legacy"]


def test_family_and_stale_filters_and_ids():
    docs = [{"id": "a.chunk0", "parent_id": "a"}, {"id": "a.chunk1", "parent_id": "a"}, {"id": "b"}]
    assert ch.parent_ids(docs) == ["a", "b"]
    assert ch.family_filter(["a", "b"]) == "parent_id:[`a`,`b`] || id:[`a`,`b`]"
    assert ch.stale_filter(["a"], 3) == "(parent_id:[`a`] || id:[`a`]) && chunk_count:!=3"


def test_parent_of_record_ids():
    assert ch.parent_of("manual-1.chunk12") == "manual-1"
    assert ch.parent_of("manual-1") == "manual-1"
    assert ch.parent_of("odd.chunkx") == "odd.chunkx"


def test_top_per_parent_caps_one_document():
    docs = [{"id": f"a.chunk{i}", "parent_id": "a"} for i in range(5)] + [{"id": "b"}, {"id": "c"}]
    assert [d["id"] for d in ch.top_per_parent(docs, 3, 2)] == ["a.chunk0", "a.chunk1", "b"]


def test_applied_config_and_rebuild_required():
    wanted = ch.ChunkingConfig(enabled=True, chunk_tokens=300)
    built = ch.ChunkingConfig(enabled=True).model_dump()
    assert ch.needs_rebuild({}, wanted)                                  # never built
    assert ch.needs_rebuild({"applied": built}, wanted)                  # size changed
    assert not ch.needs_rebuild({"applied": wanted.model_dump()}, wanted)
    assert ch.needs_rebuild({"applied": built}, ch.ChunkingConfig())     # turned off
    assert not ch.needs_rebuild({}, ch.ChunkingConfig())
    assert ch.applied_config({"applied": built}).chunk_tokens == 400
    assert ch.applied_config({"applied": None}) is None


def test_defaults_by_source():
    assert ch.default_enabled("sitemap") and ch.default_enabled(None)
    assert not ch.default_enabled("rest") and not ch.default_enabled("gsheet")


def test_load_ignores_state_keys():
    cfg = ch.load({"enabled": True, "applied": True, "chunk_tokens": 300})
    assert cfg.enabled and cfg.chunk_tokens == 300
    assert ch.is_applied({"applied": True}) and not ch.is_applied({})


# ── sitemap extraction feeding the chunker ───────────────────────────────────

_HTML = """<html><head><title>T</title><meta name="description" content="Meta desc."></head>
<body><main><h2>Roaming</h2><p>Use your phone <a href='#'>abroad</a> in Canada.</p>
<div><p>Second para.</p></div></main></body></html>"""


def test_sitemap_default_extraction_is_unchanged():
    from isp.models import SitemapConnectorConfig
    from isp.services.infra.sitemap_connector import _extract_page
    doc = _extract_page(_HTML, "https://x/a", SitemapConnectorConfig(url="https://x"))
    assert doc["content"] == "Meta desc. Roaming Use your phone abroad in Canada. Second para."


def test_sitemap_chunked_extraction_keeps_paragraphs():
    from isp.models import SitemapConnectorConfig
    from isp.services.infra.sitemap_connector import _extract_page
    doc = _extract_page(_HTML, "https://x/a", SitemapConnectorConfig(url="https://x"), 50_000)
    assert doc["content"].split("\n\n") == [
        "Meta desc.", "Roaming", "Use your phone abroad in Canada.", "Second para."]


def test_max_content_chars_caps_every_source():
    cfg = ch.ChunkingConfig(enabled=True, chunk_tokens=50, max_content_chars=1000)
    out = ch.chunk_documents([{"id": "r1", "title": "T", "content": "word " * 400}], cfg)  # 2,000 chars
    kept = " ".join(c["content"] for c in out)
    assert len(out) > 1 and "word" in kept
    assert sum(len(c["content"]) for c in out) <= 1000 + len(out) * cfg.overlap


def test_cap_applies_to_unsplit_docs():
    cfg = ch.ChunkingConfig(enabled=True, chunk_tokens=4000, max_content_chars=1000)
    [out] = ch.chunk_documents([{"id": "r1", "title": "T", "content": "x" * 2000}], cfg)
    assert len(out["content"]) == 1000
