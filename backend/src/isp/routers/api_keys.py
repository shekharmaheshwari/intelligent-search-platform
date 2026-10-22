# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
/v1/indexes/{index_id}/api-keys — Per-index API key management.

API keys authenticate calls to ``/v1/indexes/{id}/search/public`` without
requiring a full admin JWT.  This makes it safe to call the public search
endpoint from external applications (PLP, etc.) using a long-lived token that
can be independently rotated without touching admin credentials.

Keys are stored as SHA-256 hashes — the plaintext is returned once on creation
and never again.  Revoking a key makes it immediately invalid.
"""
from __future__ import annotations
import hashlib
import secrets
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from isp.services.infra.database import get_pool

router = APIRouter(prefix="/indexes", tags=["api-keys"])


class ApiKeyCreate(BaseModel):
    label: str


@router.get("/{index_id}/api-keys", summary="List API keys for an index")
async def list_keys(index_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, label, created_at FROM api_keys "
            "WHERE index_id = $1 ORDER BY created_at DESC",
            index_id,
        )
    return [dict(r) for r in rows]


@router.post("/{index_id}/api-keys", summary="Create an API key for an index")
async def create_key(index_id: str, body: ApiKeyCreate) -> dict:
    """
    Generate a new API key and return it **once**.  The plaintext is not stored —
    only a SHA-256 hash is persisted.  Copy the key now; it cannot be retrieved later.
    """
    # Verify index exists
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM indexes WHERE id = $1", index_id)
        if not row:
            raise HTTPException(404, "Index not found")

        plaintext = "isp_" + secrets.token_hex(24)  # 52-char key
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        key_id = secrets.token_hex(6)  # 12-char id matching rest of codebase

        await conn.execute(
            "INSERT INTO api_keys (id, index_id, label, key_hash, created_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            key_id, index_id, body.label, key_hash, str(int(time.time())),
        )

    return {"id": key_id, "label": body.label, "key": plaintext}


@router.delete(
    "/{index_id}/api-keys/{key_id}",
    summary="Revoke an API key",
    status_code=200,
)
async def revoke_key(index_id: str, key_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM api_keys WHERE id = $1 AND index_id = $2",
            key_id, index_id,
        )
    if result == "DELETE 0":
        raise HTTPException(404, "Key not found")
    return {"ok": True}
