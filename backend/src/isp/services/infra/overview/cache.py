# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Redis cache for AI overview answers.

Keyed on everything that changes the answer — model + prompt version, index,
normalised query, the source doc ids and a digest of their text (so an updated
price or rebuilt doc is a cache miss) — so a repeated popular query costs no LLM call.
Redis being down is never an error: the overview just isn't cached.
"""
from __future__ import annotations
import hashlib
import logging

from isp.config import settings

log = logging.getLogger(__name__)

TTL_SECONDS = 86400   # 24h — same as the embedding cache

_client = None   # redis.asyncio.Redis once connected


async def _get_client():
    """Return the Redis client, or None if Redis is unavailable. Never raises."""
    global _client
    if _client is not None:
        return _client
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=1)
        await client.ping()
        _client = client
    except Exception:
        _client = None
    return _client


def make_key(model: str, index_id: str, query: str, doc_ids: list[str], content_digest: str = "") -> str:
    norm = " ".join(query.lower().split())
    raw = "\x1f".join([model, index_id, norm, ",".join(doc_ids), content_digest])
    return "isp:overview:" + hashlib.sha256(raw.encode()).hexdigest()


async def get(key: str) -> str | None:
    r = await _get_client()
    if r is None:
        return None
    try:
        val = await r.get(key)
        return val.decode() if val else None
    except Exception:
        log.debug("overview cache get failed", exc_info=True)
        return None


async def set(key: str, answer: str) -> None:
    r = await _get_client()
    if r is None:
        return
    try:
        await r.set(key, answer.encode(), ex=TTL_SECONDS)
    except Exception:
        log.debug("overview cache set failed", exc_info=True)
