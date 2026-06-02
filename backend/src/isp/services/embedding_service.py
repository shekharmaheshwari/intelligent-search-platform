from __future__ import annotations
import asyncio
import voyageai
from isp.config import settings

_client: voyageai.AsyncClient | None = None


def _get_client() -> voyageai.AsyncClient:
    global _client
    if _client is None:
        _client = voyageai.AsyncClient(api_key=settings.voyage_api_key)
    return _client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts or not settings.voyage_api_key:
        return [[] for _ in texts]
    client = _get_client()
    # Voyage supports up to 128 texts per call
    results: list[list[float]] = []
    for i in range(0, len(texts), 128):
        batch = texts[i:i + 128]
        resp = await client.embed(batch, model="voyage-3", input_type="document")
        results.extend(resp.embeddings)
    return results


async def embed_query(text: str) -> list[float]:
    if not text or not settings.voyage_api_key:
        return []
    client = _get_client()
    resp = await client.embed([text], model="voyage-3", input_type="query")
    return resp.embeddings[0]
