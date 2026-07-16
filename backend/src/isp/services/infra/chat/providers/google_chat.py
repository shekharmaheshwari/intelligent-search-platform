"""
Google Gemini streaming provider.

Uses the new ``google-genai`` SDK (replaces deprecated ``google-generativeai``).
API key is injected at runtime via ``configure()`` — no restart needed.
Converts OpenAI-style message history (role/content) to the Gemini content format.
"""
from __future__ import annotations
import asyncio
from typing import AsyncIterator

_api_key: str = ""


def configure(api_key: str) -> None:
    global _api_key
    _api_key = api_key


def _to_gemini_contents(messages: list[dict]) -> list[dict]:
    """Convert OpenAI message list to Gemini contents format."""
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    return contents


async def stream(system: str, messages: list[dict], model: str) -> AsyncIterator[str]:
    from google import genai  # imported here so missing package fails at call time
    from google.genai import types

    client = genai.Client(api_key=_api_key)
    contents = _to_gemini_contents(messages)

    # google-genai has native async streaming support
    async for chunk in await client.aio.models.generate_content_stream(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=1024,
        ),
    ):
        if chunk.text:
            yield chunk.text
