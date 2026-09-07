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


async def list_models(api_key: str) -> list[str]:
    """
    Return generateContent-capable model ids this key currently has access
    to, via the raw REST catalog endpoint (not the SDK's model-list helper,
    whose response-object attribute names have shifted across SDK versions —
    the REST field name, supportedGenerationMethods, is what's actually
    verified against a real response here).

    Note this list can still be optimistic: Google's catalog has been seen
    listing a model (e.g. gemini-2.5-flash) that still 404s on an actual
    generateContent call for keys/projects created after some internal
    cutoff ("no longer available to new users"). This check catches every
    model that's been fully retired from the catalog; it does not catch
    that specific per-key restriction. Chat-time errors remain the final
    authority — this is a best-effort save-time guard, not a guarantee.
    """
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
        )
        resp.raise_for_status()
    data = resp.json()
    return [
        m["name"].removeprefix("models/")
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


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
