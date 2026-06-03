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
    global _voyage_client
    if _voyage_client is None:
        _voyage_client = voyageai.AsyncClient(api_key=settings.voyage_api_key)
    return _voyage_client


async def _get_redis() -> aioredis.Redis | None:
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
