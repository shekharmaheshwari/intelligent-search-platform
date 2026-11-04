# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

import pytest
from isp.services.infra.llm import router as llm_router
from isp.services.infra.overview import cache
from isp.services.infra.overview import service as overview

DOCS = {
    "a": {"id": "a", "title": "Roaming plans", "url": "https://x/roam", "content": "Roaming costs $5 a day."},
    "b": {"id": "b", "title": "5G coverage", "url": "https://x/5g", "content": "5G covers 98% of the US."},
}


@pytest.fixture
def env(monkeypatch):
    """Docs served from DOCS, an in-memory cache, and a fake LLM that counts calls."""
    monkeypatch.setattr(overview.ts, "retrieve_document", lambda col, i: DOCS.get(i))
    store: dict[str, str] = {}

    async def cache_get(key):
        return store.get(key)

    async def cache_set(key, answer):
        store[key] = answer

    monkeypatch.setattr(cache, "get", cache_get)
    monkeypatch.setattr(cache, "set", cache_set)

    calls = {"n": 0, "prompt": ""}

    async def fake_stream(system, history):
        calls["n"] += 1
        calls["prompt"] = history[0]["content"]
        for tok in ["Roaming is ", "$5 a day [1]."]:
            yield tok

    monkeypatch.setattr(llm_router, "stream", fake_stream)
    return calls


ISP_SRC = overview.DocSource(collection="isp_idx1")


async def _collect(query, ids, src=ISP_SRC):
    return [e async for e in overview.overview_stream("idx1", src, query, ids)]


def _text(events):
    return "".join(e["content"] for e in events if e["type"] == "text")


async def test_streams_sources_then_answer(env):
    events = await _collect("roaming cost", ["a", "b"])
    assert events[0] == {"type": "sources", "sources": [
        {"n": 1, "id": "a", "title": "Roaming plans", "url": "https://x/roam"},
        {"n": 2, "id": "b", "title": "5G coverage", "url": "https://x/5g"},
    ]}
    assert _text(events) == "Roaming is $5 a day [1]."
    assert events[-1] == {"type": "done", "cached": False}
    assert "[1] Roaming plans" in env["prompt"] and "[2] 5G coverage" in env["prompt"]


async def test_repeat_query_is_served_from_cache(env):
    await _collect("roaming cost", ["a", "b"])
    events = await _collect("  Roaming   COST ", ["a", "b"])
    assert env["n"] == 1
    assert _text(events) == "Roaming is $5 a day [1]."
    assert events[-1] == {"type": "done", "cached": True}


async def test_different_results_are_not_served_from_cache(env):
    await _collect("roaming cost", ["a", "b"])
    await _collect("roaming cost", ["b"])
    assert env["n"] == 2


@pytest.mark.parametrize("query, ids, reason", [
    ("ab", ["a"], "query_too_short"),
    ("Sign In", ["a"], "navigational"),
    ("roaming cost", ["missing"], "no_results"),
    ("roaming cost", [], "no_results"),
])
async def test_skips_llm(env, query, ids, reason):
    events = await _collect(query, ids)
    assert events == [{"type": "skipped", "reason": reason}]
    assert env["n"] == 0


async def test_only_top_sources_are_used(env, monkeypatch):
    many = {str(i): {"id": str(i), "title": f"Doc {i}", "content": "x"} for i in range(10)}
    monkeypatch.setattr(overview.ts, "retrieve_document", lambda col, i: many.get(i))
    events = await _collect("roaming cost", list(many))
    assert len(events[0]["sources"]) == overview.MAX_SOURCES


async def test_llm_failure_yields_error_and_is_not_cached(env, monkeypatch):
    async def broken(system, history):
        yield "partial"
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_router, "stream", broken)
    events = await _collect("roaming cost", ["a"])
    assert events[-1]["type"] == "error"

    # The partial answer wasn't cached, so the retry calls the LLM again.
    calls = []

    async def ok(system, history):
        calls.append(1)
        yield "fine"

    monkeypatch.setattr(llm_router, "stream", ok)
    events = await _collect("roaming cost", ["a"])
    assert calls == [1] and events[-1] == {"type": "done", "cached": False}


def test_source_text_is_truncated():
    doc = {"title": "Long", "content": "word " * 2000}
    assert len(overview._doc_text(doc)) <= 1500


@pytest.mark.parametrize("enabled, provider, expected", [
    (True, "openai", True),
    (True, "none", False),
    (False, "openai", False),
])
def test_is_enabled_needs_switch_and_model(monkeypatch, enabled, provider, expected):
    monkeypatch.setattr(llm_router, "_provider", provider)
    overview.configure(enabled=enabled)
    try:
        assert overview.is_enabled() is expected
    finally:
        overview.configure(enabled=False)


def test_cache_key_ignores_case_and_spacing_but_not_docs():
    k = cache.make_key("m", "i", "Roaming  Cost", ["a"])
    assert k == cache.make_key("m", "i", "roaming cost", ["a"])
    assert k != cache.make_key("m", "i", "roaming cost", ["b"])
    assert k != cache.make_key("m2", "i", "roaming cost", ["a"])


async def test_updated_source_text_is_not_served_from_cache(env):
    """Same ids, changed facts (a rebuild or price change) must regenerate, not reuse."""
    await _collect("roaming cost", ["a"])
    DOCS["a"] = {**DOCS["a"], "content": "Roaming costs $20 a day."}
    try:
        await _collect("roaming cost", ["a"])
        assert env["n"] == 2
        assert "$20" in env["prompt"]
    finally:
        DOCS["a"] = {**DOCS["a"], "content": "Roaming costs $5 a day."}


async def test_prefers_chat_content_over_marketing_copy(env, monkeypatch):
    doc = {"id": "p", "title": "Case", "content": "Show off your phone.", "chatContent": "Price: $54.99."}
    monkeypatch.setattr(overview.ts, "retrieve_document", lambda col, i: doc)
    await _collect("iphone case", ["p"])
    assert "Price: $54.99." in env["prompt"] and "Show off" not in env["prompt"]


async def test_external_index_fetches_in_scope_and_normalizes(env, monkeypatch):
    """External hits live in a shared collection under a filter, with their own field names."""
    seen = {}

    def fake_scoped(col, ids, scope, query_by):
        seen.update(col=col, ids=ids, scope=scope, query_by=query_by)
        return [{"id": "x1", "display": "Pixel Case", "description": "Fits Pixel 10."}]

    def fail_retrieve(col, i):
        raise AssertionError("external indexes must not read isp_{id}")

    monkeypatch.setattr(overview.ts, "fetch_documents_in_scope", fake_scoped)
    monkeypatch.setattr(overview.ts, "retrieve_document", fail_retrieve)
    src = overview.DocSource(
        collection="catalog", scope_filter="type:=accessory", query_by="display", external=True,
        normalize=lambda d: {"id": d["id"], "title": d["display"], "content": d["description"], "url": ""},
    )
    events = await _collect("pixel case", ["x1"], src)
    assert seen == {"col": "catalog", "ids": ["x1"], "scope": "type:=accessory", "query_by": "display"}
    assert events[0]["sources"][0]["title"] == "Pixel Case"
    assert "Fits Pixel 10." in env["prompt"]
