# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Document chunking — pure functions, no I/O. See docs/chunkingDesign.md.

A long document is split into overlapping passages, each stored as its own
Typesense record with its own vector:

    id           "{parent_id}.chunk{n}"     (a short doc keeps its original id)
    parent_id    the source document id      — group_by key for search
    chunk_index  0-based position
    chunk_count  chunks in the parent
    chunk_tail   true on chunks 1..n-1       — filter ``HEAD_FILTER`` keeps one row per doc

Splitting is paragraph-first: paragraphs (blank-line separated) are kept whole
where they fit, long paragraphs fall back to sentences, and a single sentence
longer than a chunk is cut by length. Each new chunk repeats the last
sentences of the previous one (``overlap_tokens``). Sizes use 4 chars ≈ 1 token.

Per-index config lives in ``IndexMeta.chunking``: the ``ChunkingConfig`` fields
(what the operator wants; ``enabled`` defaults from the connector type) plus
``applied`` — the config the collection was actually built with, or absent.
Search groups only when ``applied`` is set; appends chunk with the applied
sizes so one collection never mixes layouts; a rebuild adopts the wanted config.
"""
from __future__ import annotations
import re
from pydantic import BaseModel, Field

CHARS_PER_TOKEN = 4
# Source ids are limited to [A-Za-z0-9_-] (routers/indexes._resolve_doc_id), so
# the "." makes chunk ids a reserved namespace no source id can collide with.
CHUNK_ID_SEP = ".chunk"

# Filter that keeps one record per source document (heads + unchunked docs).
# ``!=`` matches docs that lack the field, so it is safe on every collection.
HEAD_FILTER = "chunk_tail:!=true"
GROUP_FIELD = "parent_id"

# Typesense schema fields added to ISP collections.
SCHEMA_FIELDS = [
    {"name": "parent_id",   "type": "string", "facet": True, "optional": True},
    {"name": "chunk_index", "type": "int32",  "optional": True},
    {"name": "chunk_count", "type": "int32",  "optional": True},
    {"name": "chunk_tail",  "type": "bool",   "optional": True},
]
FIELD_NAMES = frozenset(f["name"] for f in SCHEMA_FIELDS)

# Connector types whose documents are long free text → chunked by default.
_DEFAULT_ON = {"sitemap", "manual"}


class ChunkingConfig(BaseModel):
    enabled: bool = False
    chunk_tokens: int = Field(default=400, ge=50, le=4000)
    overlap_tokens: int = Field(default=60, ge=0, le=1000)
    min_chunk_tokens: int = Field(default=50, ge=0, le=1000)
    max_content_chars: int = Field(default=50_000, ge=1000, le=1_000_000)

    @property
    def size(self) -> int:
        return self.chunk_tokens * CHARS_PER_TOKEN

    @property
    def overlap(self) -> int:
        return min(self.overlap_tokens * CHARS_PER_TOKEN, self.size // 2)

    @property
    def min_size(self) -> int:
        return self.min_chunk_tokens * CHARS_PER_TOKEN


def default_enabled(connector_type: str | None) -> bool:
    """Default for ``enabled`` from the index's source (None = manual-only index)."""
    return (connector_type or "manual") in _DEFAULT_ON


def load(stored: dict | None) -> ChunkingConfig:
    """Config from the ``IndexMeta.chunking`` dict (extra keys like ``applied`` ignored)."""
    return ChunkingConfig(**{k: v for k, v in (stored or {}).items()
                             if k in ChunkingConfig.model_fields})


def is_applied(stored: dict | None) -> bool:
    """True when the collection was built with chunk fields — search may group."""
    return bool((stored or {}).get("applied"))


def applied_config(stored: dict | None) -> ChunkingConfig | None:
    """The config the collection was built with, or None when it isn't chunked."""
    applied = (stored or {}).get("applied")
    if not applied:
        return None
    return load(applied) if isinstance(applied, dict) else load(stored)


def needs_rebuild(stored: dict | None, wanted: ChunkingConfig) -> bool:
    """True when the wanted config differs from what the collection holds."""
    applied = applied_config(stored)
    if not wanted.enabled:
        return applied is not None
    return applied is None or applied.model_dump() != wanted.model_dump()


# ── Splitting ────────────────────────────────────────────────────────────────

_PARA_RE = re.compile(r"\n\s*\n")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[“A-Z0-9])")


def _units(text: str, size: int) -> list[tuple[str, bool]]:
    """Text → ``(unit, starts_paragraph)``; every unit is at most ``size`` chars."""
    out: list[tuple[str, bool]] = []
    for para in _PARA_RE.split(text):
        para = " ".join(para.split())
        if not para:
            continue
        pieces = [para] if len(para) <= size else _SENT_RE.split(para)
        first = True
        for piece in pieces:
            for i in range(0, len(piece), size):
                out.append((piece[i:i + size], first))
                first = False
    return out


def _join(units: list[tuple[str, bool]]) -> str:
    parts: list[str] = []
    for i, (u, para) in enumerate(units):
        if i:
            parts.append("\n\n" if para else " ")
        parts.append(u)
    return "".join(parts)


def _length(units: list[tuple[str, bool]]) -> int:
    return sum(len(u) for u, _ in units) + max(len(units) - 1, 0)


def _cut_point(window: list[tuple[str, bool]], size: int, start: int) -> int:
    """Where to end a full window: the last paragraph start in its second half
    (at or after ``start``), else the end of the window."""
    for i in range(len(window) - 1, start - 1, -1):
        if window[i][1] and _length(window[:i]) >= size // 2:
            return i
    return len(window)


def _overlap_tail(units: list[tuple[str, bool]], overlap: int) -> list[tuple[str, bool]]:
    """Trailing whole units of an emitted chunk that fit in ``overlap`` (never all of them)."""
    tail: list[tuple[str, bool]] = []
    for u in reversed(units[1:]):
        if _length([u, *tail]) > overlap:
            break
        tail.insert(0, u)
    return tail


def split_text(text: str, cfg: ChunkingConfig) -> list[str]:
    """Split ``text`` into overlapping, paragraph-aligned chunks of about ``cfg.size`` chars."""
    size = cfg.size
    chunks: list[tuple[list[tuple[str, bool]], int]] = []   # (units, carried-over count)
    window: list[tuple[str, bool]] = []
    carried = 0                     # leading units of ``window`` repeated from the last chunk
    for unit in _units(text, size):
        while len(window) > carried and _length([*window, unit]) > size:
            cut = _cut_point(window, size, start=carried + 1)
            emitted, rest = window[:cut], window[cut:]
            chunks.append((emitted, carried))
            if rest:                # ended on a paragraph break: no overlap needed
                window, carried = rest, 0
            else:
                window = _overlap_tail(emitted, cfg.overlap)
                carried = len(window)
        if len(window) == carried and _length([*window, unit]) > size:
            window, carried = [], 0  # overlap + this unit won't fit: drop the overlap
        window.append(unit)
    if len(window) > carried:
        chunks.append((window, carried))
    # A tiny final chunk is merged into the previous one (minus its repeated overlap).
    if len(chunks) > 1 and _length(chunks[-1][0]) < cfg.min_size:
        tail, tail_carried = chunks.pop()
        prev, prev_carried = chunks[-1]
        chunks[-1] = (prev + tail[tail_carried:], prev_carried)
    return [_join(units) for units, _ in chunks]


# ── Documents ────────────────────────────────────────────────────────────────

def _as_single(doc: dict) -> dict:
    return {**doc, "parent_id": doc["id"], "chunk_index": 0, "chunk_count": 1}


def chunk_documents(docs: list[dict], cfg: ChunkingConfig) -> list[dict]:
    """
    Expand ISP-schema docs into chunk records.

    ``content`` is first capped at ``cfg.max_content_chars`` (for every source).
    Every returned record carries ``parent_id`` (search groups on it), so a
    short doc is returned as a single head chunk under its original id. Fields
    other than ``content`` are copied to every chunk; ``vec`` is dropped so each
    chunk is embedded on its own.
    """
    out: list[dict] = []
    for doc in docs:
        if "." in doc["id"]:
            raise ValueError(f"source id {doc['id']!r} contains '.', reserved for chunk ids")
        text = str(doc.get("content") or "")[:cfg.max_content_chars]
        if len(text) < len(str(doc.get("content") or "")):
            doc = {**doc, "content": text}     # cap applies to every source, not just sitemaps
        if len(text) <= cfg.size:
            out.append(_as_single(doc))
            continue
        parts = split_text(text, cfg)
        if len(parts) <= 1:
            out.append(_as_single(doc))
            continue
        base = {k: v for k, v in doc.items() if k != "vec"}
        for i, part in enumerate(parts):
            chunk = {**base, "id": f"{doc['id']}{CHUNK_ID_SEP}{i}", "parent_id": doc["id"],
                     "chunk_index": i, "chunk_count": len(parts), "content": part}
            if i:
                chunk["chunk_tail"] = True
            out.append(chunk)
    return out


def parent_key(doc: dict) -> str:
    """The source document a record belongs to."""
    return doc.get("parent_id") or doc["id"]


def parent_of(record_id: str) -> str:
    """Source document id for a record id (strips a ``.chunkN`` suffix)."""
    base, sep, n = record_id.rpartition(CHUNK_ID_SEP)
    return base if sep and n.isdigit() else record_id


def is_chunk(doc: dict) -> bool:
    """True for a passage of a split document (its ``content`` is that passage)."""
    return (doc.get("chunk_count") or 1) > 1


def from_chunked_index(doc: dict) -> bool:
    """True for any record of a chunked collection, split or not: its ``content``
    is already bounded by the chunk size, so it can be used whole as context."""
    return "chunk_count" in doc


def parent_ids(docs: list[dict]) -> list[str]:
    """Distinct parent ids in order of first appearance."""
    return list(dict.fromkeys(parent_key(d) for d in docs))


def _quoted(ids: list[str]) -> str:
    return ",".join(f"`{i.replace('`', '')}`" for i in ids)


def family_filter(ids: list[str]) -> str:
    """Every record of these documents: their chunks, or a record under their own id."""
    q = _quoted(ids)
    return f"{GROUP_FIELD}:[{q}] || id:[{q}]"


def stale_filter(ids: list[str], chunk_count: int) -> str:
    """
    Records of these documents that aren't part of a new ``chunk_count``-chunk
    layout. The new chunks overwrite ids ``0..chunk_count-1`` and all carry
    ``chunk_count``, so anything else in the family is left over (a longer old
    layout, or a pre-chunking record under the document's own id).
    """
    return f"({family_filter(ids)}) && chunk_count:!={chunk_count}"


def top_per_parent(docs: list[dict], n: int, per_parent: int) -> list[dict]:
    """First ``n`` docs in rank order, at most ``per_parent`` from any one document."""
    taken: dict[str, int] = {}
    out: list[dict] = []
    for d in docs:
        key = parent_key(d)
        if taken.get(key, 0) >= per_parent:
            continue
        taken[key] = taken.get(key, 0) + 1
        out.append(d)
        if len(out) == n:
            break
    return out


def heads_only(filter_by: str | None) -> str:
    """AND ``filter_by`` with ``HEAD_FILTER`` so only one record per document matches."""
    return f"({filter_by}) && {HEAD_FILTER}" if filter_by else HEAD_FILTER


def collapse(docs: list[dict]) -> list[dict]:
    """Keep the first (best-ranked) record per parent; unchunked docs pass through."""
    seen: set[str] = set()
    out: list[dict] = []
    for d in docs:
        key = d.get("parent_id") or d.get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out
