"""
Voyage AI embedding service with a two-tier cache.

Cache hierarchy (fastest → slowest):
  L1: in-process ``cachetools.LRUCache`` — zero-latency for hot queries within
      a single pod.  Evicts least-recently-used entries when the 2048-slot limit
      (configurable) is reached.
  L2: Redis — shared across pods and survives restarts.  TTL defaults to 24 hours.

Vectors are serialised to raw ``struct.pack`` bytes (4 bytes/float) for compact
Redis storage — about 4 KB per 1024-dim vector vs. ~16 KB for JSON.

``embed_texts`` uses ``input_type="document"`` (optimised for indexing).
``embed_query`` uses ``input_type="query"`` (optimised for retrieval) — Voyage AI's
asymmetric model produces better ranking when query and document use different types.

If ``VOYAGE_API_KEY`` is empty both functions return empty lists; callers treat
empty vectors as "no embedding available" and fall back to lexical search.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import struct
import voyageai
import redis.asyncio as aioredis
from cachetools import LRUCache
from isp.config import settings

_voyage_client: voyageai.AsyncClient | None = None
_redis_client: aioredis.Redis | None = None

# L1: in-memory LRU — keyed by (text, input_type), value is list[float]
_lru: LRUCache = LRUCache(maxsize=settings.embed_lru_size)
_lru_lock = asyncio.Lock()


def _get_voyage() -> voyageai.AsyncClient:
    """Return (and lazily create) the Voyage AI async client."""
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.AsyncClient(api_key=settings.voyage_api_key)
    return _voyage_client


async def _get_redis() -> aioredis.Redis | None:
    """Return the Redis client, or None if Redis is unavailable. Never raises."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=1)
        await client.ping()
        _redis_client = client
    except Exception:
        _redis_client = None
    return _redis_client


def _cache_key(text: str, input_type: str) -> str:
    """Build a stable Redis / LRU key from text and embedding input_type."""
    h = hashlib.sha256(f"{input_type}:{text}".encode()).hexdigest()
    return f"emb:{h}"


def _vec_to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _bytes_to_vec(b: bytes) -> list[float]:
    n = len(b) // 4
    return list(struct.unpack(f"{n}f", b))


async def _cache_get(key: str) -> list[float] | None:
    # L1
    async with _lru_lock:
        hit = _lru.get(key)
    if hit is not None:
        return hit
    # L2
    r = await _get_redis()
    if r:
        try:
            raw = await r.get(key)
            if raw:
                vec = _bytes_to_vec(raw)
                async with _lru_lock:
                    _lru[key] = vec
                return vec
        except Exception:
            pass
    return None


async def _cache_set(key: str, vec: list[float]) -> None:
    async with _lru_lock:
        _lru[key] = vec
    r = await _get_redis()
    if r:
        try:
            await r.set(key, _vec_to_bytes(vec), ex=settings.embed_cache_ttl)
        except Exception:
            pass


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of document strings for indexing.

    Checks the two-tier cache for each text first; only calls Voyage AI for
    texts that are not cached.  Results are stored back into both cache tiers.
    Batches API calls in groups of 128 (Voyage AI's recommended max batch size).

    Returns a list of the same length as ``texts``; uncacheable or failed texts
    get an empty list ``[]`` (caller should skip embedding for those documents).
    """
    if not texts or not settings.voyage_api_key:
        return [[] for _ in texts]

    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []

    for i, text in enumerate(texts):
        key = _cache_key(text, "document")
        vec = await _cache_get(key)
        if vec is not None:
            results[i] = vec
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)

    if uncached_texts:
        client = _get_voyage()
        fresh: list[list[float]] = []
        for i in range(0, len(uncached_texts), 128):
            batch = uncached_texts[i:i + 128]
            resp = await client.embed(batch, model="voyage-3", input_type="document")
            fresh.extend(resp.embeddings)
        for idx, vec in zip(uncached_indices, fresh):
            results[idx] = vec
            await _cache_set(_cache_key(texts[idx], "document"), vec)

    return [r or [] for r in results]


async def embed_query(text: str) -> list[float]:
    """
    Embed a single search query for retrieval.

    Uses ``input_type="query"`` which produces asymmetrically-optimised vectors
    that rank better against ``input_type="document"`` vectors in cosine similarity.
    Returns ``[]`` when VOYAGE_API_KEY is not set.
    """
    if not text or not settings.voyage_api_key:
        return []
    key = _cache_key(text, "query")
    cached = await _cache_get(key)
    if cached is not None:
        return cached
    client = _get_voyage()
    resp = await client.embed([text], model="voyage-3", input_type="query")
    vec = resp.embeddings[0]
    await _cache_set(key, vec)
    return vec
