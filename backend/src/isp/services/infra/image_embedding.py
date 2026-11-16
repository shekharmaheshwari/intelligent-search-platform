# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Image embeddings for image search.

Independent of the text embedding model: text search keeps using ``vec`` and
whatever text model is configured, while product images are embedded into a
separate ``img_vec`` field with the image model chosen here. The image model is
multimodal, so a customer's query image (optionally with typed text, e.g.
"in black") lands in the same vector space as the product images.

Pieces:
- ``IMAGE_MODELS`` — selectable models; the active one is held in-process and
  restored from ``app_settings`` on startup (``""`` = image search off).
- ``fetch_image`` — downloads a product image with scheme, private-address,
  size, type and timeout guards (URLs come from indexed data).
- ``prepare_image`` — validates bytes as an image and downsizes to ≤512px,
  which keeps embedding cost and latency predictable.
- ``embed_documents`` / ``embed_query`` — Voyage multimodal calls.
- ``cache_get`` / ``cache_put`` — Postgres cache of product-image vectors keyed
  by (model, sha256(url)), so rebuilds only embed new or changed images.
"""
from __future__ import annotations
import asyncio, hashlib, io, ipaddress, logging, socket
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError
from cachetools import LRUCache

from isp.config import settings
from isp.services.infra.database import get_pool

log = logging.getLogger(__name__)

IMAGE_MODELS: list[dict] = [
    {
        "id": "voyage-multimodal-3",
        "label": "Voyage Multimodal 3",
        "description": "Embeds images and text into one space; supports image + text queries.",
        "num_dim": 1024,
        "provider": "voyage",
    },
]
DIM_BY_IMAGE_MODEL: dict[str, int] = {m["id"]: m["num_dim"] for m in IMAGE_MODELS}
_PROVIDER: dict[str, str] = {m["id"]: m["provider"] for m in IMAGE_MODELS}

MAX_SIDE = 512                     # px — longest side after downsizing
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_QUERY_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000            # reject decompression bombs before decoding
_DOC_BATCH = 16                    # images per Voyage request at index time
_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}

_current_model: str = ""
_query_lru: LRUCache = LRUCache(maxsize=512)
_voyage_client = None


class ImageError(ValueError):
    """The bytes are not a usable image (bad format, too large, corrupt)."""


# ── Model selection ──────────────────────────────────────────────────────────

def get_image_model() -> str:
    """Active image model id, or ``""`` when image search is off globally."""
    return _current_model


def set_image_model(model_id: str) -> None:
    """Switch the active image model (``""`` turns image search off)."""
    global _current_model
    if model_id and model_id not in DIM_BY_IMAGE_MODEL:
        raise ValueError(f"Unknown image model: {model_id}")
    _current_model = model_id


def model_available(model_id: str) -> bool:
    """True when the provider for ``model_id`` has the credentials it needs."""
    provider = _PROVIDER.get(model_id)
    if provider == "voyage":
        return bool(settings.voyage_api_key)
    return False


# ── Image loading ────────────────────────────────────────────────────────────

def prepare_image(data: bytes) -> Image.Image:
    """Decode ``data`` as an image, convert to RGB and downsize to ≤ ``MAX_SIDE`` px."""
    if not data:
        raise ImageError("empty image")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            w, h = probe.size
            probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ImageError("not a valid image") from exc
    if fmt not in _ALLOWED_FORMATS:
        raise ImageError(f"unsupported image format: {fmt}")
    if w * h > MAX_PIXELS:
        raise ImageError("image dimensions too large")
    img = Image.open(io.BytesIO(data))   # verify() leaves the first handle unusable
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img


def _host_is_public(host: str) -> bool:
    """Reject loopback/private/link-local targets so indexed URLs can't probe internal services."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


_MAX_REDIRECTS = 3


async def fetch_image(client: httpx.AsyncClient, url: str) -> bytes | None:
    """
    Download an image URL. Returns None (logged at debug) on any failure or guard hit.
    Redirects are followed manually so every hop passes the public-host check.
    """
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return None
            if not await asyncio.to_thread(_host_is_public, parsed.hostname):
                log.debug("image fetch refused for non-public host: %s", url)
                return None
            async with client.stream("GET", url) as resp:
                if resp.is_redirect:
                    url = str(resp.url.join(resp.headers.get("location", "")))
                    continue
                return await _read_image_body(resp)
        return None
    except Exception:
        log.debug("image fetch failed: %s", url, exc_info=True)
        return None


async def _read_image_body(resp: httpx.Response) -> bytes | None:
    """Read a response body if it is an image within the size cap."""
    if resp.status_code != 200:
        return None
    ctype = resp.headers.get("content-type", "")
    if ctype and not ctype.startswith("image/"):
        return None
    if int(resp.headers.get("content-length") or 0) > MAX_DOWNLOAD_BYTES:
        return None
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf.extend(chunk)
        if len(buf) > MAX_DOWNLOAD_BYTES:
            return None
    return bytes(buf)


def http_client() -> httpx.AsyncClient:
    """HTTP client for product image downloads (redirects off: each hop would need re-checking)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=False,
        headers={"User-Agent": "isp-image-indexer/1.0"},
    )


# ── Embedding ────────────────────────────────────────────────────────────────

def _voyage():
    global _voyage_client
    if _voyage_client is None:
        import voyageai
        _voyage_client = voyageai.AsyncClient(api_key=settings.voyage_api_key)
    return _voyage_client


def _require(model: str) -> None:
    if not model:
        raise RuntimeError("No image embedding model is configured")
    if not model_available(model):
        raise RuntimeError(f"Image model {model} is not available — check its API key")


async def embed_documents(images: list[Image.Image], model: str) -> list[list[float]]:
    """Embed product images (``input_type=document``), batched."""
    _require(model)
    out: list[list[float]] = []
    for i in range(0, len(images), _DOC_BATCH):
        batch = images[i:i + _DOC_BATCH]
        res = await _voyage().multimodal_embed(
            inputs=[[img] for img in batch], model=model, input_type="document",
        )
        out.extend(res.embeddings)
    return out


async def embed_query(image_bytes: bytes | None, text: str, model: str) -> list[float]:
    """
    Embed a customer query: an image, optional text, or both together as one
    input (Voyage multimodal treats ``[image, text]`` as a single combined query).
    Results are cached in-process by content hash.
    """
    _require(model)
    text = (text or "").strip()
    if image_bytes is None and not text:
        raise ImageError("an image or text is required")
    key = hashlib.sha256(
        model.encode() + b"\0" + (image_bytes or b"") + b"\0" + text.encode()
    ).hexdigest()
    hit = _query_lru.get(key)
    if hit is not None:
        return hit
    parts: list = []
    if image_bytes is not None:
        if len(image_bytes) > MAX_QUERY_BYTES:
            raise ImageError("image is larger than 5 MB")
        parts.append(prepare_image(image_bytes))
    if text:
        parts.append(text)
    res = await _voyage().multimodal_embed(inputs=[parts], model=model, input_type="query")
    vec = res.embeddings[0]
    _query_lru[key] = vec
    return vec


# ── Product-image vector cache (Postgres) ────────────────────────────────────

def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


async def cache_get(model: str, urls: list[str]) -> dict[str, list[float]]:
    """Return cached vectors for ``urls`` under ``model``."""
    if not urls:
        return {}
    by_hash = {url_hash(u): u for u in urls}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT url_hash, vec FROM image_embeddings WHERE model = $1 AND url_hash = ANY($2::char(64)[])",
            model, list(by_hash),
        )
    return {by_hash[r["url_hash"]]: list(r["vec"]) for r in rows if r["url_hash"] in by_hash}


async def cache_put(model: str, vectors: dict[str, list[float]]) -> None:
    """Store product-image vectors for ``model``."""
    if not vectors:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO image_embeddings (model, url_hash, url, vec)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (model, url_hash) DO UPDATE SET vec = EXCLUDED.vec, created_at = now()
            """,
            [(model, url_hash(u), u, v) for u, v in vectors.items()],
        )
