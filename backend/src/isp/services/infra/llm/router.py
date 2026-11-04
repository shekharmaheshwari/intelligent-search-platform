# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
LLM provider routing shared by every feature that calls the configured chat model.

Holds the operator-chosen provider/model/key (set from Settings → Chat Model)
and streams a completion from it. Chat and AI Overview both call ``stream()``,
so neither depends on the other — only on this module.

Provider routing:
  "none"        → nothing configured; callers check ``is_configured()`` first
  "anthropic"   → providers/anthropic_chat.py
  "google"      → providers/google_chat.py
  "openai"      → providers/openai_compat.py (base_url=None)
  "groq"        → providers/openai_compat.py (base_url=Groq endpoint)
  "self_hosted" → providers/openai_compat.py (base_url=user endpoint)
"""
from __future__ import annotations
from typing import AsyncIterator

from isp.services.infra.chat.providers import anthropic_chat, google_chat, openai_compat

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_provider: str = "none"
_model:    str = ""
_api_key:  str = ""
_endpoint: str = ""


def configure(*, provider: str, model: str, api_key: str, endpoint: str) -> None:
    """Apply the stored chat-model settings. Called on startup and on every Settings save."""
    global _provider, _model, _api_key, _endpoint
    _provider, _model, _api_key, _endpoint = provider, model, api_key, endpoint
    if provider == "anthropic":
        anthropic_chat.configure(api_key)
    elif provider == "google":
        google_chat.configure(api_key)


def provider() -> str:
    return _provider


def model() -> str:
    return _model


def is_configured() -> bool:
    return _provider != "none"


def stream(system: str, history: list[dict]) -> AsyncIterator[str]:
    """Stream text tokens from the configured provider."""
    if _provider == "anthropic":
        return anthropic_chat.stream(system, history, _model)
    if _provider == "google":
        return google_chat.stream(system, history, _model)
    if _provider == "groq":
        return openai_compat.stream(system, history, _model, _api_key, base_url=GROQ_BASE_URL)
    if _provider == "self_hosted":
        return openai_compat.stream(system, history, _model, _api_key, base_url=_endpoint)
    return openai_compat.stream(system, history, _model, _api_key)
