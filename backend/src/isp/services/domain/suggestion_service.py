"""
suggestion_service.py — populates and maintains the search_suggestions Typesense collection.

Two population paths:
  1. seed_suggestions_from_index(index_id) — called at end of every rebuild.
     Runs extractSuggestions(doc) from every JS extension assigned to the index.
     If no extension defines extractSuggestions, nothing is seeded — all phrase
     logic lives in JS, not in Python.
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


def _run_extraction_sync(
    docs: list[dict], extension_records: list[dict]
) -> list[tuple[str, str | None, str | None, str | None]]:
    """
    Run extractSuggestions(doc) from each assigned extension that defines it.

    Mirrors the optional-function detection pattern used by extension_runner.py
    for filterUrl — compile once, check typeof, call per document.

    Returns a flat list of (query_text, category_or_None, url_or_None, doc_id_or_None) tuples.
    Returns empty list if no extension defines extractSuggestions.
    """
    if not _MR_AVAILABLE or not docs or not extension_records:
        return []

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

    phrases: list[tuple[str, str | None, str | None, str | None]] = []
    for doc in docs:
        for name, ctx in contexts:
            try:
                result = ctx.call("extractSuggestions", doc)
                if not isinstance(result, list):
                    continue
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    query  = str(item.get("query") or "").strip().lower()
                    cat    = item.get("category") or None
                    # url and doc_id enable link-mode and pinned-result suggestions
                    # respectively — both are optional extensions to the base query field.
                    url    = str(item.get("url") or "").strip() or None
                    doc_id = str(item.get("doc_id") or "").strip() or None
                    if query:
                        phrases.append((query, cat, url, doc_id))
            except Exception as exc:
                log.error("Extension '%s' extractSuggestions error on doc '%s': %s",
                          name, doc.get("id", "?"), exc)

    return phrases


async def seed_suggestions_from_index(index_id: str) -> int:
    """
    Refresh catalog suggestions for an index on every rebuild.

    All phrase logic lives in JS extensions — Python only orchestrates:
      1. Fetch all documents from the index's Typesense collection (paginated).
      2. Resolve the index's intent_label from the DB.
      3. Run JS extractSuggestions from assigned extensions via py_mini_racer.
         If no extension defines extractSuggestions, returns 0 — nothing seeded.
      4. When extension returns no category, fill in intent_label automatically.
      5. Preserve existing operator boost values before deletion.
      6. Delete stale catalog entries (by intent_label + raw doc categories).
      7. Re-upsert fresh phrases with preserved boost values.

    type=query suggestions (from search_events) are never touched.
    Returns the count of entries upserted.
    """
    col = ts.collection_name(index_id)

    # Paginate through all documents — large indexes have 500+ items.
    docs: list[dict] = []
    page = 1
    while True:
        try:
            raw = ts.get_client().collections[col].documents.search({
                "q": "*",
                "query_by": "title",
                "per_page": 250,
                "page": page,
            })
        except Exception:
            break
        hits = raw.get("hits", [])
        docs.extend(hit.get("document", {}) for hit in hits)
        if len(hits) < 250:
            break
        page += 1

    if not docs:
        return 0

    # Resolve the category scope for this index.
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT intent_label FROM indexes WHERE id = $1", index_id
        )
    intent_label = (row["intent_label"] or "").strip() if row else ""

    # Run JS extractSuggestions. No extension = no suggestions — all logic is in JS.
    extension_records = await extension_service.list_index_extensions(index_id)
    loop = asyncio.get_event_loop()
    phrase_pairs = await loop.run_in_executor(
        None, _run_extraction_sync, docs, extension_records
    )

    if not phrase_pairs:
        log.info("index %s: no extractSuggestions extension found — skipping seed", index_id)
        return 0

    # Deduplicate. When extension returns no category, use intent_label so the
    # suggestion is correctly scoped for the delete-and-reinsert cycle.
    seen_ids: set[str] = set()
    candidates: list[dict] = []
    for query_text, cat, url, doc_id in phrase_pairs:
        effective_cat = cat or intent_label or None
        entry_id = _make_id("catalog", query_text, effective_cat)
        if entry_id in seen_ids:
            continue
        seen_ids.add(entry_id)
        candidate: dict = {
            "id":         entry_id,
            "query":      query_text,
            "type":       "catalog",
            "popularity": 0,
            "boost":      0,
            **({"category": effective_cat} if effective_cat else {}),
        }
        # When the extension supplies a url, mark the suggestion as link-mode so
        # the frontend navigates directly instead of running a search.
        if url:
            candidate["url"]  = url
            candidate["mode"] = "link"
        # featured_doc_ids pins results at the top positions for search-mode suggestions.
        if doc_id:
            candidate["featured_doc_ids"] = [doc_id]
        candidates.append(candidate)

    if not candidates:
        return 0

    # Preserve boost, delete stale entries, re-upsert.
    # Delete by both intent_label and raw doc category slugs to catch entries
    # created by older extractors that used doc.category instead of intent_label.
    if intent_label:
        boost_map = ts.fetch_catalog_boost_map(intent_label)

        raw_doc_categories = {
            str(doc.get("category") or "").strip().lower()
            for doc in docs
        } - {""}
        for cat in {intent_label} | raw_doc_categories:
            ts.delete_from_collection(
                "search_suggestions",
                f"type:=catalog && category:={cat}",
            )

        for entry in candidates:
            entry["boost"] = boost_map.get(entry["id"], 0)

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
