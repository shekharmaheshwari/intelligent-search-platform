"""
OpenAI-compatible streaming provider.

One file handles three cases — they share identical code, just different URLs:

  provider="openai"      → base_url=None   (default: api.openai.com)
  provider="groq"        → base_url="https://api.groq.com/openai/v1"
  provider="self_hosted" → base_url=<user-supplied endpoint>

The dispatcher in service.py picks the right base_url before calling here.
"""
from __future__ import annotations
from typing import AsyncIterator


async def stream(
    system: str,
    messages: list[dict],
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> AsyncIterator[str]:
    from openai import AsyncOpenAI  # imported here so missing package fails at call time

    kwargs: dict = {"api_key": api_key or "none"}
    if base_url:
        kwargs["base_url"] = base_url

    client = AsyncOpenAI(**kwargs)
    full_messages = [{"role": "system", "content": system}] + messages

    resp = await client.chat.completions.create(
        model=model,
        messages=full_messages,
        stream=True,
        max_tokens=1024,
    )
    async for chunk in resp:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
