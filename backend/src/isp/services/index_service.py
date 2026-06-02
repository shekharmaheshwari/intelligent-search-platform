from __future__ import annotations
import json, time, uuid
from isp.models import IndexMeta, IngestStatus
from isp.services.database import get_pool


def _row_to_meta(row: dict) -> IndexMeta:
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
    )


async def list_indexes() -> list[IndexMeta]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM indexes ORDER BY created_at DESC")
    return [_row_to_meta(dict(r)) for r in rows]


async def get_index(idx_id: str) -> IndexMeta | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM indexes WHERE id = $1", idx_id)
    return _row_to_meta(dict(row)) if row else None


async def create_index(name: str, description: str) -> IndexMeta:
    idx_id = uuid.uuid4().hex[:12]
    meta = IndexMeta(
        id=idx_id,
        name=name,
        description=description,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        collection_name=f"isp_{idx_id}",
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO indexes
                (id, name, description, created_at, doc_count, status,
                 connectors, collection_name, is_external,
                 external_collection, external_filter, external_query_by,
                 external_title_field, external_url_field)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
        """,
            meta.id, meta.name, meta.description, meta.created_at,
            meta.doc_count, meta.status,
            json.dumps(meta.connectors), meta.collection_name, meta.is_external,
            meta.external_collection, meta.external_filter, meta.external_query_by,
            meta.external_title_field, meta.external_url_field,
        )
    return meta


async def delete_index(idx_id: str) -> bool:
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
    meta = await get_index(idx_id)
    if not meta:
        return None
    meta.connectors.append(config)
    await update_index(meta)
    return meta


async def update_index(meta: IndexMeta) -> None:
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
                external_url_field   = $14
            WHERE id = $1
        """,
            meta.id, meta.name, meta.description,
            meta.doc_count, meta.last_ingested, meta.status,
            json.dumps(meta.connectors), meta.collection_name, meta.is_external,
            meta.external_collection, meta.external_filter, meta.external_query_by,
            meta.external_title_field, meta.external_url_field,
        )


async def get_status(idx_id: str) -> IngestStatus:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ingest_status WHERE index_id = $1", idx_id)
    if row:
        return IngestStatus(**dict(row))
    return IngestStatus(index_id=idx_id)


async def set_status(status: IngestStatus) -> None:
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


# ── External collection discovery ─────────────────────────────────────────────

_KNOWN_SPLITS: dict[str, list[tuple[str, str, str, str]]] = {
    "catalog": [
        ("Phones", "T-Mobile cellphone catalog", "type:=device", "https://www.t-mobile.com/cell-phone/"),
        ("Accessories", "T-Mobile accessories catalog", "type:=accessory", "https://www.t-mobile.com/accessories/"),
    ],
}


async def discover_external_collections() -> list[IndexMeta]:
    from isp.services import typesense_client as ts

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
            )
            pool2 = await get_pool()
            async with pool2.acquire() as conn:
                await conn.execute("""
                    INSERT INTO indexes
                        (id, name, description, created_at, doc_count, status,
                         connectors, collection_name, is_external,
                         external_collection, external_filter, external_query_by,
                         external_title_field, external_url_field)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                    meta.id, meta.name, meta.description, meta.created_at,
                    meta.doc_count, meta.status,
                    json.dumps(meta.connectors), meta.collection_name, meta.is_external,
                    meta.external_collection, meta.external_filter, meta.external_query_by,
                    meta.external_title_field, meta.external_url_field,
                )
            created.append(meta)

    return created
