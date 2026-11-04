# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
AI overview — a short, cited LLM summary of the top search results.

Independent of chat: it shares only the configured model, through
llm/router.py. One LLM call per (query, results) pair, cached in Redis.

Stream events (dicts, sent as SSE by routers/overview.py):
  {"type": "skipped", "reason": "..."}        — no LLM call (short/navigational query, no docs)
  {"type": "sources", "sources": [...]}       — the numbered docs the answer cites
  {"type": "text",    "content": "..."}       — answer token chunk (whole answer when cached)
  {"type": "done",    "cached": bool}
  {"type": "error",   "content": "..."}
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable

from isp.services.infra import typesense_client as ts
from isp.services.infra.llm import router as llm_router
from isp.services.infra.overview import cache

log = logging.getLogger(__name__)

_PROMPTS: dict = json.loads((Path(__file__).with_name("prompts.json")).read_text(encoding="utf-8"))
_SKIP_QUERIES = {q.lower() for q in _PROMPTS["skip_queries"]}
# Part of the cache key: editing the prompt (or how sources are built) retires old answers.
_PROMPT_VERSION = hashlib.sha256(json.dumps(
    [_PROMPTS["system"], _PROMPTS["user_template"], _PROMPTS["source_template"], "chatContent>content"]
).encode()).hexdigest()[:12]

MAX_SOURCES = 6
_MAX_CHARS_PER_SOURCE = 1500   # keeps one overview to roughly 2–3k input tokens
_MIN_QUERY_CHARS = 3

# Set from Settings → Chat Model ("AI Overview in search"). Off by default — opt-in,
# since every overview is an LLM call.
_enabled: bool = False


def configure(*, enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def is_enabled() -> bool:
    """True when the operator switched it on and a chat model is configured."""
    return _enabled and llm_router.is_configured()


def skip_reason(query: str, docs: list[dict]) -> str | None:
    """Why this request shouldn't spend an LLM call, or None to go ahead."""
    q = " ".join(query.lower().split())
    if len(q) < _MIN_QUERY_CHARS:
        return "query_too_short"
    if q in _SKIP_QUERIES:
        return "navigational"
    if not docs:
        return "no_results"
    return None


def _doc_text(doc: dict) -> str:
    # chatContent is the LLM-facing summary built at index time (price, compatibility,
    # colours, stock); content is often marketing copy only. Prefer the former.
    text = doc.get("chatContent") or doc.get("content") or ""
    return " ".join(str(text).split())[:_MAX_CHARS_PER_SOURCE]


def _sources_block(docs: list[dict]) -> str:
    return "\n\n".join(
        _PROMPTS["source_template"].format(n=i, title=d.get("title") or "Untitled", text=_doc_text(d))
        for i, d in enumerate(docs, 1)
    )


def build_prompt(query: str, docs: list[dict]) -> str:
    return _PROMPTS["user_template"].format(query=query, sources=_sources_block(docs))


@dataclass(frozen=True)
class DocSource:
    """
    Where an index's documents live. ISP-managed: ``collection`` only. External:
    also ``scope_filter`` (the index's filter_by over a shared collection),
    ``query_by`` and ``normalize`` (maps raw fields to title/content/url).
    """
    collection:   str
    scope_filter: str = ""
    query_by:     str = "title"
    normalize:    Callable[[dict], dict] | None = None
    external:     bool = False


async def _fetch_docs(src: DocSource, doc_ids: list[str]) -> list[dict]:
    """Fetch docs by id, keeping the caller's (search-rank) order and dropping misses."""
    if src.external:
        docs = await asyncio.to_thread(ts.fetch_documents_in_scope, src.collection, doc_ids,
                                       src.scope_filter, src.query_by)
    else:
        found = await asyncio.gather(*(asyncio.to_thread(ts.retrieve_document, src.collection, i)
                                       for i in doc_ids))
        docs = [d for d in found if d]
    return [src.normalize(d) for d in docs] if src.normalize else docs


def _content_digest(docs: list[dict]) -> str:
    """Fingerprint of the source text sent to the LLM — a changed price is a new key."""
    return hashlib.sha256(_sources_block(docs).encode()).hexdigest()[:16]


async def overview_stream(index_id: str, src: DocSource, query: str, doc_ids: list[str]) -> AsyncIterator[dict]:
    """Stream an overview of ``doc_ids`` (the top search hits, in rank order) for ``query``."""
    docs = await _fetch_docs(src, doc_ids[:MAX_SOURCES])
    reason = skip_reason(query, docs)
    if reason:
        yield {"type": "skipped", "reason": reason}
        return

    yield {"type": "sources", "sources": [
        {"n": i, "id": d.get("id", ""), "title": d.get("title", ""), "url": d.get("url", "")}
        for i, d in enumerate(docs, 1)
    ]}

    prompt = build_prompt(query, docs)
    key = cache.make_key(f"{llm_router.provider()}:{llm_router.model()}:{_PROMPT_VERSION}", index_id, query,
                         [str(d.get("id", "")) for d in docs], _content_digest(docs))
    cached = await cache.get(key)
    if cached:
        yield {"type": "text", "content": cached}
        yield {"type": "done", "cached": True}
        return

    parts: list[str] = []
    try:
        async for token in llm_router.stream(_PROMPTS["system"],
                                             [{"role": "user", "content": prompt}]):
            parts.append(token)
            yield {"type": "text", "content": token}
    except Exception:
        log.warning("AI overview generation failed: index=%s", index_id, exc_info=True)
        yield {"type": "error", "content": _PROMPTS["errors"]["failed"]}
        return

    answer = "".join(parts).strip()
    if answer:
        await cache.set(key, answer)
    yield {"type": "done", "cached": False}
