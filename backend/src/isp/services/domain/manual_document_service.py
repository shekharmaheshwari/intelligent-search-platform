# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Manually entered documents for ISP-managed indexes.

The ``manual_documents`` table is the source of truth.  Typesense only holds a
searchable copy, so a rebuild (which drops and recreates ``isp_{id}``) loses
nothing: ``_run_rebuild`` reloads every row for the index, embeds it with the
index's current model and upserts it alongside the connector documents.

Manual docs are marked with ``source_type = "manual"`` and ids prefixed with
``manual-`` so a connector ingest can never overwrite one and the Documents
tab can filter on origin.
"""
from __future__ import annotations
import json, uuid
from isp.services.infra.database import get_pool

SOURCE_TYPE = "manual"
ID_PREFIX = "manual-"

# Keys the client may not set directly — they are derived server-side.
_RESERVED = {"id", "vec", "indexed_at", "source_type", "metadata"}


def is_manual_id(doc_id: str) -> bool:
    """True when ``doc_id`` belongs to a manually entered document."""
    return doc_id.startswith(ID_PREFIX)


def new_doc_id() -> str:
    """Return a fresh Typesense-safe id for a manual document."""
    return f"{ID_PREFIX}{uuid.uuid4().hex[:16]}"


def clean_payload(fields: dict) -> dict:
    """
    Validate and normalise form input into the stored payload.

    Drops reserved keys and empty values, trims strings and string lists.
    Raises ``ValueError`` when ``title`` or ``content`` is missing — both are
    required by the ISP collection schema.
    """
    out: dict = {}
    for k, v in fields.items():
        if not isinstance(k, str) or not k or k in _RESERVED or k.startswith("_"):
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        elif isinstance(v, list):
            v = [x.strip() if isinstance(x, str) else x for x in v]
            v = [x for x in v if x not in ("", None)]
            if not v:
                continue
        elif v is None:
            continue
        elif not isinstance(v, (int, float, bool)):
            raise ValueError(f"Unsupported value for field '{k}'")
        out[k] = v
    if not out.get("title"):
        raise ValueError("title is required")
    if not out.get("content"):
        raise ValueError("content is required")
    return out


def to_index_doc(doc_id: str, payload: dict) -> dict:
    """Shape a stored payload as a raw doc ready for ``_ensure_isp_schema``."""
    return {**payload, "id": doc_id, "source_type": SOURCE_TYPE}


async def list_for_index(index_id: str) -> list[tuple[str, dict]]:
    """Return ``(doc_id, payload)`` for every manual document of an index, oldest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT doc_id, payload FROM manual_documents WHERE index_id = $1 ORDER BY created_at",
            index_id,
        )
    return [(r["doc_id"], _load(r["payload"])) for r in rows]


async def get(index_id: str, doc_id: str) -> dict | None:
    """Return the stored payload for one manual document, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT payload FROM manual_documents WHERE index_id = $1 AND doc_id = $2",
            index_id, doc_id,
        )
    return _load(row["payload"]) if row else None


async def upsert(index_id: str, doc_id: str, payload: dict) -> None:
    """Insert or replace a manual document's payload."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO manual_documents (index_id, doc_id, payload)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (index_id, doc_id)
            DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()
            """,
            index_id, doc_id, json.dumps(payload),
        )


async def merge_fields(index_id: str, doc_id: str, fields: dict) -> bool:
    """
    Merge ``fields`` into an existing payload (used when editorial signals are
    edited on a manual doc, so a rebuild does not revert them).
    Returns False when the document is not stored.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE manual_documents
            SET payload = payload || $3::jsonb, updated_at = now()
            WHERE index_id = $1 AND doc_id = $2
            """,
            index_id, doc_id, json.dumps(fields),
        )
    return res.endswith(" 1")


async def delete(index_id: str, doc_id: str) -> bool:
    """Delete a manual document. Returns False when it did not exist."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            "DELETE FROM manual_documents WHERE index_id = $1 AND doc_id = $2",
            index_id, doc_id,
        )
    return res.endswith(" 1")


def _load(raw) -> dict:
    """asyncpg returns JSONB as str unless a codec is registered."""
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
