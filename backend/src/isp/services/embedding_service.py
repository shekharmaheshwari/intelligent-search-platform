"""
Multi-provider embedding service with a two-tier cache and runtime-swappable model.

Four providers are supported, selected by the active model id:

- **voyage**   — Voyage AI (``voyageai.AsyncClient``); model ids: voyage-3, voyage-3-large, etc.
- **openai**   — OpenAI Embeddings API (``httpx``); model id: text-embedding-3-small
- **rexbert**  — RexBERT model family (``httpx``); model ids: rexbert-micro/mini/base/large;
                 all variants share one configurable endpoint, model id sent as ``model`` field
- **custom**   — Any OpenAI-compatible endpoint (``httpx``); model id: "custom"

Cache hierarchy (fastest → slowest):
  L1: in-process ``cachetools.LRUCache`` — zero-latency for hot queries within
      a single pod.  Evicts least-recently-used entries when the 2048-slot limit
      is reached.
  L2: Redis — shared across pods and survives restarts.  TTL defaults to 24 hours.

Vectors are serialised to raw ``struct.pack`` bytes (4 bytes/float) for compact
Redis storage — about 4 KB per 1024-dim vector vs. ~16 KB for JSON.

``embed_texts`` uses ``input_type="document"`` for Voyage AI (optimised for indexing).
``embed_query`` uses ``input_type="query"`` for Voyage AI (optimised for retrieval).
OpenAI and custom providers do not distinguish input types.

If the required API key is empty (voyage_api_key / openai_api_key / custom endpoint)
both functions return empty lists; callers treat empty vectors as "no embedding available"
and fall back to lexical search.

The active model and provider settings can be changed at runtime without restarting via
``set_current_model()``, ``set_openai_api_key()``, and ``set_custom_config()``.
The model name is included in every cache key so cached vectors from one model are never
returned when a different model is active.
"""
from __future__ import annotations
import asyncio
import hashlib
import struct
import httpx
import voyageai
import redis.asyncio as aioredis
from cachetools import LRUCache
from isp.config import settings

_voyage_client: voyageai.AsyncClient | None = None
_redis_client: aioredis.Redis | None = None

# Active model id — updated at runtime by settings_service.set_embed_config()
_current_model: str = settings.embed_model

# OpenAI provider state — key stored in app_settings and injected here at startup/change
_openai_api_key: str = settings.openai_api_key

# RexBERT provider state — shared endpoint for all rexbert-* model variants
_rexbert_endpoint: str = ""
_rexbert_api_key:  str = ""

# Custom provider state — all stored in app_settings and injected here at startup/change
_custom_endpoint:   str = ""
_custom_api_key:    str = ""
_custom_model_name: str = ""
_custom_num_dim:    int = 1536

# L1: in-memory LRU — keyed by cache_key(model, input_type, text), value is list[float]
_lru: LRUCache = LRUCache(maxsize=settings.embed_lru_size)
_lru_lock = asyncio.Lock()


def get_current_model() -> str:
    """Return the currently active embedding model id."""
    return _current_model


def set_current_model(model_id: str) -> None:
    """
    Switch the active embedding model at runtime.

    Updates ``_current_model`` so subsequent ``embed_texts`` and ``embed_query``
    calls use the new model.  Because the model name is part of every cache key,
    stale vectors from the previous model become unreachable and evict naturally.
    """
    global _current_model
    _current_model = model_id


def set_openai_api_key(api_key: str) -> None:
    """
    Update the OpenAI API key used for the ``openai`` provider at runtime.

    Called by ``settings_service.load_embed_model()`` on startup and by
    ``set_embed_config()`` when the user saves a new key via the UI.
    """
    global _openai_api_key
    _openai_api_key = api_key


def set_rexbert_config(*, endpoint: str, api_key: str) -> None:
    """
    Update the shared RexBERT endpoint config at runtime.

    All rexbert-* model variants use this endpoint; the specific variant is
    passed as the ``model`` field in the request body.  Called by
    ``settings_service`` after persisting the config to the database.
    """
    global _rexbert_endpoint, _rexbert_api_key
    _rexbert_endpoint = endpoint
    _rexbert_api_key  = api_key


def set_custom_config(
    *,
    endpoint: str,
    api_key: str,
    model_name: str,
    num_dim: int,
) -> None:
    """
    Update the custom-endpoint config used for the ``custom`` provider at runtime.

    All four fields are required; pass empty strings / 0 to clear individual values.
    Called by ``settings_service`` after persisting the config to the database.
    """
    global _custom_endpoint, _custom_api_key, _custom_model_name, _custom_num_dim
    _custom_endpoint   = endpoint
    _custom_api_key    = api_key
    _custom_model_name = model_name
    _custom_num_dim    = num_dim


def _get_provider() -> str:
    """
    Derive the active embedding provider from the current model id.

    Returns ``"voyage"``, ``"openai"``, or ``"custom"``.
    Voyage is the fallback for any unrecognised model id.
    """
    if _current_model == "custom":
        return "custom"
    if _current_model.startswith("rexbert-"):
        return "rexbert"
    if _current_model == "text-embedding-3-small":
        return "openai"
    return "voyage"


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
    """
    Build a stable Redis / LRU cache key from active model, input_type, and text.

    The model name is included so switching models never returns cached vectors
    from the previous model — vectors from different models are not comparable.
    """
    h = hashlib.sha256(f"{_current_model}:{input_type}:{text}".encode()).hexdigest()
    return f"emb:{h}"


def _vec_to_bytes(vec: list[float]) -> bytes:
    """Serialise a float vector to raw bytes (4 bytes per float) for compact Redis storage."""
    return struct.pack(f"{len(vec)}f", *vec)


def _bytes_to_vec(b: bytes) -> list[float]:
    """Deserialise raw bytes back to a float vector. Inverse of ``_vec_to_bytes``."""
    n = len(b) // 4
    return list(struct.unpack(f"{n}f", b))


async def _cache_get(key: str) -> list[float] | None:
    """
    Look up a vector in the two-tier cache (L1 LRU then L2 Redis).

    Promotes a Redis hit into the in-process LRU so subsequent requests within
    the same pod skip the Redis round-trip.  Returns None on a full cache miss.
    """
    async with _lru_lock:
        hit = _lru.get(key)
    if hit is not None:
        return hit
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
    """Write a vector to both the in-process LRU and Redis (if available). Never raises."""
    async with _lru_lock:
        _lru[key] = vec
    r = await _get_redis()
    if r:
        try:
            await r.set(key, _vec_to_bytes(vec), ex=settings.embed_cache_ttl)
        except Exception:
            pass


async def _call_openai_compat(
    texts: list[str],
    endpoint: str,
    api_key: str,
    model_name: str,
) -> list[list[float]]:
    """
    Call an OpenAI-compatible embeddings endpoint via httpx.

    Expects the standard response shape:
      ``{"data": [{"embedding": [...]}, ...]}``

    Used for both the built-in OpenAI provider and user-defined custom endpoints.

    :param texts:       List of strings to embed.
    :param endpoint:    Full URL, e.g. ``https://api.openai.com/v1/embeddings``.
    :param api_key:     Bearer token; sent as ``Authorization: Bearer <key>`` if non-empty.
    :param model_name:  Model name sent in the request body.
    :returns:           List of float vectors, one per input text.
    :raises httpx.HTTPStatusError: on non-2xx responses.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            endpoint,
            headers=headers,
            json={"model": model_name, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data["data"]]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of document strings for indexing.

    Checks the two-tier cache for each text first; only calls the embedding
    provider for texts that are not cached.  Results are stored back into both
    cache tiers.  Voyage AI batches are capped at 128 texts per API call.

    Returns a list of the same length as ``texts``; uncacheable or failed texts
    get an empty list ``[]`` (caller skips embedding for those documents).

    Provider is selected by the current model id:
    - voyage models → voyageai.AsyncClient
    - text-embedding-3-small → OpenAI API via httpx
    - custom → user-supplied endpoint via httpx
    """
    provider = _get_provider()

    if provider == "voyage"  and not settings.voyage_api_key:
        return [[] for _ in texts]
    if provider == "openai"  and not _openai_api_key:
        return [[] for _ in texts]
    if provider == "rexbert" and not _rexbert_endpoint:
        return [[] for _ in texts]
    if provider == "custom"  and not _custom_endpoint:
        return [[] for _ in texts]
    if not texts:
        return []

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
        if provider == "voyage":
            client = _get_voyage()
            fresh: list[list[float]] = []
            for i in range(0, len(uncached_texts), 128):
                batch = uncached_texts[i:i + 128]
                resp = await client.embed(batch, model=_current_model, input_type="document")
                fresh.extend(resp.embeddings)
        elif provider == "openai":
            fresh = await _call_openai_compat(
                uncached_texts,
                endpoint="https://api.openai.com/v1/embeddings",
                api_key=_openai_api_key,
                model_name=_current_model,
            )
        elif provider == "rexbert":
            fresh = await _call_openai_compat(
                uncached_texts,
                endpoint=_rexbert_endpoint,
                api_key=_rexbert_api_key,
                model_name=_current_model,  # e.g. "rexbert-base"
            )
        else:  # custom
            fresh = await _call_openai_compat(
                uncached_texts,
                endpoint=_custom_endpoint,
                api_key=_custom_api_key,
                model_name=_custom_model_name,
            )

        for idx, vec in zip(uncached_indices, fresh):
            results[idx] = vec
            await _cache_set(_cache_key(texts[idx], "document"), vec)

    return [r or [] for r in results]


async def embed_query(text: str) -> list[float]:
    """
    Embed a single search query for retrieval.

    For Voyage AI, uses ``input_type="query"`` which produces asymmetrically-
    optimised vectors that rank better against ``input_type="document"`` vectors
    in cosine similarity.  OpenAI and custom endpoints do not distinguish types.

    Returns ``[]`` when the required API key / endpoint is not configured.
    """
    provider = _get_provider()

    if provider == "voyage"  and not settings.voyage_api_key:
        return []
    if provider == "openai"  and not _openai_api_key:
        return []
    if provider == "rexbert" and not _rexbert_endpoint:
        return []
    if provider == "custom"  and not _custom_endpoint:
        return []
    if not text:
        return []

    key = _cache_key(text, "query")
    cached = await _cache_get(key)
    if cached is not None:
        return cached

    if provider == "voyage":
        client = _get_voyage()
        resp = await client.embed([text], model=_current_model, input_type="query")
        vec = resp.embeddings[0]
    elif provider == "openai":
        vecs = await _call_openai_compat(
            [text],
            endpoint="https://api.openai.com/v1/embeddings",
            api_key=_openai_api_key,
            model_name=_current_model,
        )
        vec = vecs[0]
    elif provider == "rexbert":
        vecs = await _call_openai_compat(
            [text],
            endpoint=_rexbert_endpoint,
            api_key=_rexbert_api_key,
            model_name=_current_model,
        )
        vec = vecs[0]
    else:  # custom
        vecs = await _call_openai_compat(
            [text],
            endpoint=_custom_endpoint,
            api_key=_custom_api_key,
            model_name=_custom_model_name,
        )
        vec = vecs[0]

    await _cache_set(key, vec)
    return vec
