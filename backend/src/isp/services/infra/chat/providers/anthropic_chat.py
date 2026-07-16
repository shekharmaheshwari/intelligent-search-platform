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
