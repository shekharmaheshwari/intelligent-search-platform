"""
PostgreSQL CRUD for the ``indexes``, ``ingest_status``, and ``deleted_splits`` tables.

``discover_external_collections`` scans Typesense for collections that are not yet
tracked and registers them as external (read-only or write-back) ISP sources.
``_KNOWN_SPLITS`` maps Typesense collection names to named sub-sources; extend it
when new external catalog collections are deployed.
"""
from __future__ import annotations
import json, re, time, uuid
from isp.models import IndexMeta, IngestStatus
from isp.services.infra.database import get_pool
from isp.services.domain.viewed_item_policy import migrate as _migrate_policy


def _slugify(name: str) -> str:
    """
    Convert a display name to a URL-safe slug.

    Rules (in order):
    1. Lowercase.
    2. Spaces and underscores become hyphens.
    3. Strip any character that is not ``[a-z0-9-]``.
    4. Collapse runs of multiple hyphens to one.
    5. Strip leading/trailing hyphens.

    Examples: ``"TABLETS"`` → ``"tablets"``, ``"My Products v2"`` → ``"my-products-v2"``.
    """
    s = name.lower()
    s = re.sub(r"[ _]", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


async def _unique_slug(name: str, idx_id: str, conn) -> str:
    """
    Return a slug that is unique among all rows in the ``indexes`` table.

    Tries the base slug first.  If another index already holds that slug, appends
    the first 4 hex chars of ``idx_id`` as a disambiguator (e.g. ``tablets-9ed5``).
    The disambiguated form is always accepted without further collision checking
    because ``idx_id`` is itself globally unique.
    """
    base = _slugify(name)
    if not base:
        # Name produced an empty slug (e.g. all special chars); fall back to id prefix.
        return idx_id[:8]
    row = await conn.fetchrow(
        "SELECT id FROM indexes WHERE slug = $1", base
    )
    if row is None:
        return base
    # Slug taken by a different index — append the first 4 chars of the new id.
    return f"{base}-{idx_id[:4]}"


def _migrate_boost_rule(rule: dict) -> dict:
    """Migrate old flat-criteria rules to the trigger+targets shape."""
    if "trigger" not in rule and "criteria" in rule:
        return {**{k: v for k, v in rule.items() if k != "criteria"},
                "trigger": rule["criteria"], "targets": []}
    rule.setdefault("trigger", [])
    rule.setdefault("targets", [])
    return rule


def _row_to_meta(row: dict) -> IndexMeta:
    """Convert a raw asyncpg row dict to an ``IndexMeta`` Pydantic model."""
    return IndexMeta(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        created_at=row["created_at"],
        doc_count=row["doc_count"],
        last_ingested=row["last_ingested"],
        status=row["status"],
        connectors=json.loads(row["connectors"]) if isinstance(row["connectors"], str) else (row["connectors"] or []),
        collection_name=row["collection_name"] or "",
        is_external=row["is_external"],
        external_collection=row["external_collection"] or "",
        external_filter=row["external_filter"] or "",
        external_query_by=row["external_query_by"] or "display",
        external_title_field=row["external_title_field"] or "display",
        external_url_field=row["external_url_field"] or "",
        # embed_model / version_boost / intent_label / slug / affinity_dimensions added via ALTER TABLE; older rows may be NULL
        embed_model=row.get("embed_model") or "",
        version_boost=bool(row.get("version_boost") or False),
        intent_label=row.get("intent_label") or "",
        slug=row.get("slug") or "",
        affinity_dimensions=json.loads(row["affinity_dimensions"])
            if isinstance(row.get("affinity_dimensions"), str)
            else (row.get("affinity_dimensions") or []),
        viewed_item_policy=_migrate_policy(
            json.loads(row["viewed_item_policy"])
            if isinstance(row.get("viewed_item_policy"), str)
            else row.get("viewed_item_policy")
        ),
        boost_rules=[_migrate_boost_rule(r) for r in (
            json.loads(row["boost_rules"])
            if isinstance(row.get("boost_rules"), str)
            else (row.get("boost_rules") or [])
        )],
        field_config=(
            json.loads(row["field_config"])
            if isinstance(row.get("field_config"), str)
            else (row.get("field_config") or [])
        ),
        chat_persona_config=(
            json.loads(row["chat_persona_config"])
            if isinstance(row.get("chat_persona_config"), str)
            else (row.get("chat_persona_config") or {})
        ),
        superlative_field_map=(
            json.loads(row["superlative_field_map"])
            if isinstance(row.get("superlative_field_map"), str)
            else (row.get("superlative_field_map") or {})
        ),
    )


async def list_indexes() -> list[IndexMeta]:
    """Return all indexes ordered by creation date (newest first)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM indexes ORDER BY created_at DESC")
    return [_row_to_meta(dict(r)) for r in rows]


async def get_index(idx_id: str) -> IndexMeta | None:
    """Return a single index by id, or None if not found."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM indexes WHERE id = $1", idx_id)
    return _row_to_meta(dict(row)) if row else None


async def create_index(name: str, description: str, embed_model: str = "",
                       intent_label: str = "", slug: str = "") -> IndexMeta:
    """
    Insert a new index row and return the populated ``IndexMeta``.

    The collection name is pre-computed as ``isp_{id}`` but the Typesense collection
    itself is provisioned separately in the router (``ts.create_collection``).

    ``embed_model`` overrides the global setting for all ingest and search operations
    on this index.  Pass empty string to inherit the global default at runtime.

    ``slug`` is auto-generated from ``name`` when omitted or empty; uniqueness is
    enforced at the database level via ``indexes_slug_unique``.
    """
    idx_id = uuid.uuid4().hex[:12]
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Resolve slug inside the same connection so the uniqueness check and INSERT
        # see a consistent snapshot of the slug column.
        resolved_slug = slug.strip() or await _unique_slug(name, idx_id, conn)
        meta = IndexMeta(
            id=idx_id,
            name=name,
            description=description,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            collection_name=f"isp_{idx_id}",
            embed_model=embed_model,
            intent_label=intent_label,
            slug=resolved_slug,
        )
        await conn.execute("""
            INSERT INTO indexes
                (id, name, description, created_at, doc_count, status,
                 connectors, collection_name, is_external,
                 external_collection, external_filter, external_query_by,
                 external_title_field, external_url_field, embed_model, intent_label, slug)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        """,
            meta.id, meta.name, meta.description, meta.created_at,
            meta.doc_count, meta.status,
            json.dumps(meta.connectors), meta.collection_name, meta.is_external,
            meta.external_collection, meta.external_filter, meta.external_query_by,
            meta.external_title_field, meta.external_url_field, meta.embed_model,
            meta.intent_label, meta.slug,
        )
    return meta


async def delete_index(idx_id: str) -> bool:
    """
    Delete an index and its ingest status from PostgreSQL.

    For external sources also adds a ``deleted_splits`` record keyed by
    ``{collection}:{filter}`` so the auto-discovery job never re-creates
    this source.  Returns False if the index did not exist.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM indexes WHERE id = $1", idx_id)
        if not row:
            return False
        meta = _row_to_meta(dict(row))
        if meta.external_collection:
            split_key = f"{meta.external_collection}:{meta.external_filter}"
            await conn.execute(
                "INSERT INTO deleted_splits (split_key) VALUES ($1) ON CONFLICT DO NOTHING",
                split_key,
            )
        await conn.execute("DELETE FROM indexes WHERE id = $1", idx_id)
        await conn.execute("DELETE FROM ingest_status WHERE index_id = $1", idx_id)
    return True


async def add_connector(idx_id: str, config: dict) -> IndexMeta | None:
    """Append a connector config to the index's ``connectors`` list and persist it."""
    meta = await get_index(idx_id)
    if not meta:
        return None
    meta.connectors.append(config)
    await update_index(meta)
    return meta


async def update_index(meta: IndexMeta) -> None:
    """Persist all mutable fields of an ``IndexMeta`` back to PostgreSQL."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE indexes SET
                name                 = $2,
                description          = $3,
                doc_count            = $4,
                last_ingested        = $5,
                status               = $6,
                connectors           = $7,
                collection_name      = $8,
                is_external          = $9,
                external_collection  = $10,
                external_filter      = $11,
                external_query_by    = $12,
                external_title_field = $13,
                external_url_field   = $14,
                embed_model          = $15,
                intent_label         = $16,
                slug                 = $17,
                field_config         = $18
            WHERE id = $1
        """,
            meta.id, meta.name, meta.description,
            meta.doc_count, meta.last_ingested, meta.status,
            json.dumps(meta.connectors), meta.collection_name, meta.is_external,
            meta.external_collection, meta.external_filter, meta.external_query_by,
            meta.external_title_field, meta.external_url_field, meta.embed_model,
            meta.intent_label, meta.slug,
            json.dumps(meta.field_config or []),
        )


async def set_version_boost(idx_id: str, enabled: bool) -> None:
    """Toggle the version recency boost for an index."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE indexes SET version_boost = $1 WHERE id = $2",
            enabled, idx_id,
        )


async def set_affinity_dimensions(idx_id: str, dims: list[dict]) -> None:
    """Persist the affinity dimension config for an index."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE indexes SET affinity_dimensions = $1 WHERE id = $2",
            json.dumps(dims), idx_id,
        )


async def set_viewed_item_policy(idx_id: str, policy: dict) -> None:
    """Persist the viewed item policy for an index."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE indexes SET viewed_item_policy = $1 WHERE id = $2",
            json.dumps(policy), idx_id,
        )


async def set_boost_rules(idx_id: str, rules: list[dict]) -> None:
    """Persist the boost rules config for an index."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE indexes SET boost_rules = $1 WHERE id = $2",
            json.dumps(rules), idx_id,
        )


async def set_chat_persona_config(idx_id: str, config: dict) -> None:
    """Persist the chat persona config for an index."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE indexes SET chat_persona_config = $1 WHERE id = $2",
            json.dumps(config), idx_id,
        )


async def merge_superlative_field_map(idx_id: str, concept: str, field_name: str) -> None:
    """
    Write-through cache: merge one newly-resolved superlative concept into the
    stored map. Uses Postgres JSONB `||` so two requests resolving different
    concepts concurrently merge atomically instead of racing on a read-modify-write.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE indexes SET superlative_field_map = superlative_field_map || $1::jsonb WHERE id = $2",
            json.dumps({concept: field_name}), idx_id,
        )


async def set_concept_field(idx_id: str, field_name: str, concept: str) -> dict:
    """
    Atomically move one field's concept tag — the Fields tab's "Concept"
    control (see routers/indexes.py).

    Within a single UPDATE: remove any existing concept key that currently
    points to ``field_name`` (a field is single-select in the UI — it can't
    claim two concepts at once), then set ``concept`` -> ``field_name`` when
    ``concept`` is non-empty (empty string clears without setting a
    replacement).

    This runs entirely in SQL as one statement, keyed off the row's current
    value under Postgres's row-level UPDATE lock — not a Python-side
    read -> mutate -> full-replace. The latter lets two concurrent PATCHes
    (e.g. tagging Price and Popularity at nearly the same time) both read the
    same starting map and then have the second write silently discard the
    first's change. Returns the map as stored after the update.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE indexes
            SET superlative_field_map = COALESCE(
                (
                    SELECT jsonb_object_agg(kv.key, kv.value)
                    FROM jsonb_each(COALESCE(superlative_field_map, '{}'::jsonb)) AS kv(key, value)
                    WHERE kv.value <> to_jsonb($3::text)
                ),
                '{}'::jsonb
            ) || CASE WHEN $2 <> '' THEN jsonb_build_object($2, $3) ELSE '{}'::jsonb END
            WHERE id = $1
            RETURNING superlative_field_map
        """, idx_id, concept, field_name)
    raw = row["superlative_field_map"]
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


async def set_intent_label(idx_id: str, label: str) -> None:
    """Set the intent_label for an index (e.g. 'device', 'accessory', 'smartwatch', 'tablet')."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE indexes SET intent_label = $1 WHERE id = $2",
            label, idx_id,
        )


async def get_index_by_slug(slug: str) -> IndexMeta | None:
    """Return a single index by its slug, or None if not found."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM indexes WHERE slug = $1", slug)
    return _row_to_meta(dict(row)) if row else None


async def set_slug(idx_id: str, slug: str) -> None:
    """Persist a new slug value for an index identified by its hex id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE indexes SET slug = $1 WHERE id = $2",
            slug, idx_id,
        )


async def seed_slugs() -> None:
    """
    Back-fill slug for every index that currently has an empty slug.

    Safe to call repeatedly — rows with an existing slug are skipped.
    Called from the FastAPI lifespan handler after ``init_db()`` so existing
    deployments automatically receive slugs on their next restart.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Fetch only rows that need a slug so we don't mutate already-configured ones.
        rows = await conn.fetch(
            "SELECT id, name FROM indexes WHERE slug = '' OR slug IS NULL"
        )
    for row in rows:
        idx_id = row["id"]
        name = row["name"]
        pool2 = await get_pool()
        async with pool2.acquire() as conn2:
            new_slug = await _unique_slug(name, idx_id, conn2)
        await set_slug(idx_id, new_slug)


async def get_status(idx_id: str) -> IngestStatus:
    """Return the current ingest/rebuild status, defaulting to idle if no row exists."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ingest_status WHERE index_id = $1", idx_id)
    if row:
        return IngestStatus(**dict(row))
    return IngestStatus(index_id=idx_id)


async def set_status(status: IngestStatus) -> None:
    """
    Upsert an ingest status row (insert on first call, update on subsequent calls).

    Every ingest/rebuild path in the codebase (routers/indexes.py's several
    connector flows, infra/agent/setup_executor.py) reports its progress
    through this one function, which makes it the single chokepoint for
    reacting to a completed ingest regardless of which path produced it.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingest_status (index_id, state, total, indexed, error)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (index_id) DO UPDATE SET
                state   = EXCLUDED.state,
                total   = EXCLUDED.total,
                indexed = EXCLUDED.indexed,
                error   = EXCLUDED.error
        """,
            status.index_id, status.state, status.total, status.indexed, status.error,
        )
        if status.state == "done":
            # A completed ingest/rebuild may have changed this index's
            # document set — chat/service.py's in-memory per-index caches
            # (category classification vectors, customer-eligibility facet
            # values) are populated once and kept for the process lifetime,
            # so without this a changed/added eligibility value or category
            # goes unrecognized by chat until the process happens to
            # restart. Deferred import: chat/service.py already imports
            # this module at load time, so importing it back at module
            # level here would be circular.
            from isp.services.infra.chat import service as _chat_service
            _chat_service.invalidate_index_caches(status.index_id)


# ── External collection discovery ─────────────────────────────────────────────

_KNOWN_SPLITS: dict[str, list[tuple[str, str, str, str]]] = {
    "catalog": [
        ("Phones", "Cellphone catalog", "type:=device", "https://www.example.com/cell-phone/"),
        ("Accessories", "Accessories catalog", "type:=accessory", "https://www.example.com/accessory/"),
    ],
}


async def discover_external_collections() -> list[IndexMeta]:
    """
    Scan Typesense for collections not yet tracked by ISP and register them.

    Skips:
    - ``isp_*`` collections (ISP-managed)
    - Collections already registered as ISP-managed source collections
    - Splits that appear in ``deleted_splits`` (user explicitly removed them)

    For known collection names (``_KNOWN_SPLITS``) creates named sub-sources with
    explicit filter expressions.  For unknown collections creates a single generic
    source with no filter.

    Returns only the newly created ``IndexMeta`` records; existing ones are not returned.
    """
    from isp.services.infra import typesense_client as ts

    pool = await get_pool()
    all_cols = ts.list_all_collections()
    existing = await list_indexes()

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT split_key FROM deleted_splits")
    deleted_splits = {r["split_key"] for r in rows}

    tracked: dict[tuple[str, str], str] = {
        (m.external_collection, m.external_filter): m.id
        for m in existing if m.external_collection
    }
    isp_col_names = {m.collection_name for m in existing if not m.is_external}

    created: list[IndexMeta] = []

    for col in all_cols:
        col_name: str = col["name"]
        if col_name.startswith("isp_"):
            continue  # ISP-managed collection — never auto-discover
        if col_name in isp_col_names:
            continue

        splits = _KNOWN_SPLITS.get(col_name)
        entries = splits if splits else [
            (col_name.replace("_", " ").title(), f"External: {col_name}", "", "")
        ]

        for display_name, description, filter_by, url_prefix in entries:
            if (col_name, filter_by) in tracked:
                continue
            if f"{col_name}:{filter_by}" in deleted_splits:
                continue

            doc_count = ts.count_external(col_name, filter_by)
            idx_id = uuid.uuid4().hex[:12]
            pool2 = await get_pool()
            async with pool2.acquire() as conn:
                # Generate a unique slug within the same connection used for INSERT
                # so the uniqueness check and the write are consistent.
                new_slug = await _unique_slug(display_name, idx_id, conn)
                meta = IndexMeta(
                    id=idx_id,
                    name=display_name,
                    description=description,
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    collection_name="",
                    doc_count=doc_count,
                    is_external=True,
                    external_collection=col_name,
                    external_filter=filter_by,
                    external_query_by="display",
                    external_title_field="display",
                    external_url_field=url_prefix,
                    connectors=[{"type": "external", "url": url_prefix, "collection": col_name}],
                    slug=new_slug,
                )
                await conn.execute("""
                    INSERT INTO indexes
                        (id, name, description, created_at, doc_count, status,
                         connectors, collection_name, is_external,
                         external_collection, external_filter, external_query_by,
                         external_title_field, external_url_field, embed_model, intent_label, slug)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                """,
                    meta.id, meta.name, meta.description, meta.created_at,
                    meta.doc_count, meta.status,
                    json.dumps(meta.connectors), meta.collection_name, meta.is_external,
                    meta.external_collection, meta.external_filter, meta.external_query_by,
                    meta.external_title_field, meta.external_url_field, meta.embed_model,
                    meta.intent_label, meta.slug,
                )
            created.append(meta)

    return created
