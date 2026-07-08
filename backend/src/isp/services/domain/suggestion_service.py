"""
suggestion_service.py — populates and maintains the search_suggestions Typesense collection.

Two population paths:
  1. seed_suggestions_from_index(index_id) — called at end of every rebuild.
     Primary: runs extractSuggestions(doc) from every JS extension assigned to the index.
     Baseline: if no extension defines extractSuggestions, falls back to extracting the
     document's title and category fields directly — no heuristics, just those two fields.
  2. run_nightly_aggregation() — called by a scheduled job at 02:00.
     Reads search_events, counts query frequency over 30 days, upserts as type="query".

Does NOT own any PostgreSQL tables directly. Never imports from routers/.
"""
from __future__ import annotations
import asyncio
import logging
import re

from isp.services.domain import extension_service
from isp.services.infra import typesense_client as ts
from isp.services.infra.database import get_pool

log = logging.getLogger(__name__)

try:
    from py_mini_racer import MiniRacer
    _MR_AVAILABLE = True
except ImportError:
    _MR_AVAILABLE = False
    log.warning("py-mini-racer not installed — suggestion extraction will be skipped")


def _slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[ _]", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def _make_id(type_: str, query: str, category: str | None) -> str:
    """Stable upsert key: ``{type}:{slug(query)}:{slug(category)}``.

    Deterministic id means repeated seeding never creates duplicate entries.
    """
    return f"{type_}:{_slugify(query)}:{_slugify(category or 'global')}"


def _baseline_phrases(doc: dict, intent_label: str) -> list[tuple[str, str | None]]:
    """
    Minimal fallback when no extension defines extractSuggestions.

    Extracts the document title scoped to the index's intent_label — not the
    document's own category field, which may differ (e.g. "handset" vs "device").
    Using intent_label ensures the suggestion appears in the correct Suggestions tab.
    """
    title = str(doc.get("title") or "").strip().lower()
    if not title:
        return []
    return [(title, intent_label or None)]


def _run_extraction_sync(docs: list[dict], extension_records: list[dict]) -> list[tuple[str, str | None]]:
    """
    Run extractSuggestions(doc) from each assigned extension that defines it.

    Mirrors the optional-function detection pattern used by extension_runner.py
    for filterUrl — compile once, check typeof, call per document.

    Returns a flat list of (query_text, category_or_None) tuples.
    """
    if not _MR_AVAILABLE or not docs or not extension_records:
        return []

    # Compile each extension and record whether it exports extractSuggestions.
    contexts: list[tuple[str, object]] = []
    for ext in extension_records:
        if not ext.get("enabled", True):
            continue
        name = ext.get("name", ext.get("id", "?"))
        code = ext.get("code", "")
        try:
            ctx = MiniRacer()
            ctx.eval(code)
            if ctx.eval("typeof extractSuggestions") == "function":
                contexts.append((name, ctx))
        except Exception as exc:
            log.error("Extension '%s' failed to compile (extractSuggestions pass): %s", name, exc)

    if not contexts:
        return []

    phrases: list[tuple[str, str | None]] = []
    for doc in docs:
        for name, ctx in contexts:
            try:
                result = ctx.call("extractSuggestions", doc)
                if not isinstance(result, list):
                    continue
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    query = str(item.get("query") or "").strip().lower()
                    cat   = item.get("category") or None
                    if query:
                        phrases.append((query, cat))
            except Exception as exc:
                log.error("Extension '%s' extractSuggestions error on doc '%s': %s",
                          name, doc.get("id", "?"), exc)

    return phrases


async def seed_suggestions_from_index(index_id: str) -> int:
    """
    Refresh catalog suggestions for an index on every rebuild.

    Strategy:
      1. Resolve the index's intent_label (category scope) from the DB.
      2. Run JS extractSuggestions from assigned extensions; fall back to
         title-only baseline if none define it.
      3. Preserve existing operator boost values by reading them before deletion.
      4. Delete all type=catalog suggestions scoped to this category.
      5. Re-upsert the fresh phrase set with preserved boost values.

    This means the Suggestions tab always reflects the current index content —
    stale entries from old extraction logic are never left behind.
    Operator boost values survive the refresh cycle.
    type=query suggestions (from search_events) are never touched.

    Returns the count of entries upserted.
    """
    col = ts.collection_name(index_id)

    try:
        raw = ts.get_client().collections[col].documents.search({
            "q": "*",
            "query_by": "title",
            "per_page": 250,
        })
    except Exception:
        return 0

    hits = raw.get("hits", [])
    if not hits:
        return 0

    docs = [hit.get("document", {}) for hit in hits]

    # Resolve the category scope for this index.
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT intent_label FROM indexes WHERE id = $1", index_id
        )
    intent_label = (row["intent_label"] or "").strip() if row else ""

    # Run JS extractSuggestions; fall back to title-only baseline if none fire.
    extension_records = await extension_service.list_index_extensions(index_id)
    if extension_records:
        loop = asyncio.get_event_loop()
        phrase_pairs = await loop.run_in_executor(
            None, _run_extraction_sync, docs, extension_records
        )
    else:
        phrase_pairs = []

    if not phrase_pairs:
        for doc in docs:
            phrase_pairs.extend(_baseline_phrases(doc, intent_label))

    if not phrase_pairs:
        return 0

    # Deduplicate phrases into candidate dicts.
    seen_ids: set[str] = set()
    candidates: list[dict] = []
    for query_text, cat in phrase_pairs:
        entry_id = _make_id("catalog", query_text, cat)
        if entry_id in seen_ids:
            continue
        seen_ids.add(entry_id)
        candidates.append({
            "id":         entry_id,
            "query":      query_text,
            "type":       "catalog",
            "popularity": 0,
            "boost":      0,
            **({"category": cat} if cat else {}),
        })

    if not candidates:
        return 0

    # When a category scope exists: read existing boost values, delete stale
    # catalog entries for this category, then re-upsert with boost preserved.
    # Without a scope we fall back to additive upsert to avoid touching other
    # indexes' global suggestions.
    if intent_label:
        boost_map = ts.fetch_catalog_boost_map(intent_label)
        ts.delete_from_collection(
            "search_suggestions",
            f"type:=catalog && category:={intent_label}",
        )
        for entry in candidates:
            entry["boost"] = boost_map.get(entry["id"], 0)
    else:
        # Additive path — only insert entries not already present.
        existing = list_suggestions_by_category(None, "*", 1, 1000)
        existing_ids = {h.get("id") for h in existing.get("hits", []) if h.get("id")}
        candidates = [e for e in candidates if e["id"] not in existing_ids]
        if not candidates:
            return 0

    return ts.upsert_suggestions(candidates)


def list_suggestions_by_category(category: str | None, q: str, page: int, per_page: int) -> dict:
    """Thin passthrough to the infra layer — kept here so callers stay in the domain tier."""
    return ts.list_suggestions_by_category(category, q, page, per_page)


async def run_nightly_aggregation() -> int:
    """
    Aggregate query frequency from the last 30 days and upsert as type="query" suggestions.

    Reads search_events, groups by normalised query text, upserts the top-500 terms
    with their frequency as popularity. Existing entries are fully replaced so the
    popularity value always reflects the current 30-day window.

    Returns the count of entries successfully upserted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT lower(trim(query)) AS q, COUNT(*) AS freq
            FROM search_events
            WHERE created_at > NOW() - INTERVAL '30 days'
              AND query IS NOT NULL AND length(trim(query)) >= 2
            GROUP BY lower(trim(query))
            ORDER BY freq DESC
            LIMIT 500
            """
        )

    if not rows:
        return 0

    entries = [
        {
            "id":         _make_id("query", row["q"], None),
            "query":      row["q"],
            "type":       "query",
            "popularity": int(row["freq"]),
            "boost":      0,
        }
        for row in rows
    ]

    return ts.upsert_suggestions(entries)
