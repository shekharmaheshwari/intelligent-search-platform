"""
Anthropic (Claude) streaming provider.

Uses the official ``anthropic`` SDK.  API key is injected at runtime via
``configure()`` when the operator saves settings — no restart needed.
"""
from __future__ import annotations
from typing import AsyncIterator

_api_key: str = ""


def configure(api_key: str) -> None:
    global _api_key
    _api_key = api_key


async def list_models(api_key: str) -> list[str]:
    """Return model ids this key currently has access to, via Anthropic's
    /v1/models REST endpoint — same save-time validation purpose as the
    Groq/Google list_models functions in the sibling provider modules."""
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        resp.raise_for_status()
    return [m["id"] for m in resp.json().get("data", [])]


async def stream(system: str, messages: list[dict], model: str) -> AsyncIterator[str]:
    import anthropic  # imported here so missing package fails at call time, not import time

    client = anthropic.AsyncAnthropic(api_key=_api_key)
    async with client.messages.stream(
        model=model,
        max_tokens=1024,
        system=system,
        messages=messages,
    ) as s:
        async for text in s.text_stream:
            yield text
