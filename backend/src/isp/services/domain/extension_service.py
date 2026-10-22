# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
PostgreSQL CRUD for the extension library and per-index assignments.

Two tables are managed here:
- ``extensions`` — global library of reusable JS transform functions.
- ``index_extensions`` — many-to-many join that controls which extensions
  run on which index, in what order, and whether they are enabled.

Startup seeding uses ``upsert_extension_by_name`` + ``assign_extension_if_new``
so that committing a new ``.js`` file to ``backend/extensions/`` is the only
deployment step needed to make a new extension available.
"""
from __future__ import annotations
import time, uuid
from isp.services.infra.database import get_pool


async def list_extensions() -> list[dict]:
    """Return all extensions from the library, newest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM extensions ORDER BY created_at DESC")
    return [dict(r) for r in rows]


async def get_extension(ext_id: str) -> dict | None:
    """Return a single extension by id, or None if not found."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM extensions WHERE id = $1", ext_id)
    return dict(row) if row else None


async def create_extension(name: str, description: str, code: str, ext_type: str = "transform") -> dict:
    """Insert a new extension and return it with its generated id and timestamps."""
    ext_id = uuid.uuid4().hex[:12]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO extensions (id, name, description, code, type, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            ext_id, name, description, code, ext_type, now, now,
        )
    return {"id": ext_id, "name": name, "description": description, "code": code,
            "type": ext_type, "created_at": now, "updated_at": now}


async def update_extension(ext_id: str, name: str | None, description: str | None, code: str | None, ext_type: str | None = None) -> dict | None:
    """
    Partially update an extension.  Pass ``None`` for any field to leave it unchanged.
    Returns the updated record, or ``None`` if the extension does not exist.
    """
    ext = await get_extension(ext_id)
    if not ext:
        return None
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    new_name = name if name is not None else ext["name"]
    new_desc = description if description is not None else ext["description"]
    new_code = code if code is not None else ext["code"]
    new_type = ext_type if ext_type is not None else ext.get("type", "transform")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE extensions SET name=$2, description=$3, code=$4, type=$5, updated_at=$6 WHERE id=$1",
            ext_id, new_name, new_desc, new_code, new_type, now,
        )
    return {**ext, "name": new_name, "description": new_desc, "code": new_code, "type": new_type, "updated_at": now}


async def upsert_extension_by_name(name: str, description: str, code: str, ext_type: str = "transform") -> dict:
    """Create or update an extension matched by name. Used by the file-based seed."""
    exts = await list_extensions()
    existing = next((e for e in exts if e["name"] == name), None)
    if existing:
        return await update_extension(existing["id"], name, description, code, ext_type) or existing
    return await create_extension(name, description, code, ext_type)


async def delete_extension(ext_id: str) -> bool:
    """Delete an extension by id. Returns True if a row was deleted, False if not found."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM extensions WHERE id = $1", ext_id)
    return result != "DELETE 0"


# ── Index ↔ Extension assignment ─────────────────────────────────────────────

async def list_index_extensions(idx_id: str) -> list[dict]:
    """Return extensions assigned to an index, ordered by run_order."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.id, e.name, e.description, e.code, e.type, e.created_at, e.updated_at,
                   ie.run_order, ie.enabled
            FROM index_extensions ie
            JOIN extensions e ON e.id = ie.extension_id
            WHERE ie.index_id = $1
            ORDER BY ie.run_order ASC, e.created_at ASC
        """, idx_id)
    return [dict(r) for r in rows]


async def assign_extension(idx_id: str, ext_id: str, run_order: int = 0, enabled: bool = True) -> None:
    """
    Assign an extension to an index (full upsert).

    If the assignment already exists it is updated with the new ``run_order`` and
    ``enabled`` values.  Used when the user explicitly changes an assignment via the UI.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO index_extensions (index_id, extension_id, run_order, enabled)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (index_id, extension_id) DO UPDATE SET
                run_order = EXCLUDED.run_order,
                enabled   = EXCLUDED.enabled
        """, idx_id, ext_id, run_order, enabled)


async def assign_extension_if_new(idx_id: str, ext_id: str, run_order: int = 0) -> None:
    """Assign only if not already assigned — does not override existing run_order or enabled state."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO index_extensions (index_id, extension_id, run_order, enabled)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT DO NOTHING
        """, idx_id, ext_id, run_order)


async def unassign_extension(idx_id: str, ext_id: str) -> bool:
    """Remove an extension assignment. Returns True if a row was deleted."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM index_extensions WHERE index_id=$1 AND extension_id=$2", idx_id, ext_id,
        )
    return result != "DELETE 0"
