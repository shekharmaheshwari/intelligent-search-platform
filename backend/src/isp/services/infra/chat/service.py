"""
Chat service — dispatcher + RAG context assembly.

Two responsibilities:
1. Build RAG context: embed the user query, search Typesense for top-5 docs,
   assemble a system prompt with those products.
2. Route to the correct provider file based on the configured chat_provider.

Provider routing:
  "none"        → yield error, return
  "anthropic"   → providers/anthropic.py  (Anthropic SDK)
  "google"      → providers/google.py     (Google Generative AI SDK)
  "openai"      → providers/openai_compat.py (base_url=None)
  "groq"        → providers/openai_compat.py (base_url=Groq endpoint)
  "self_hosted" → providers/openai_compat.py (base_url=user endpoint)

Module-level state (provider, model, keys) is updated at runtime by
settings_service.set_chat_config() — no restart needed.
"""
from __future__ import annotations
import asyncio
import json
from typing import AsyncIterator

from isp.services.infra import typesense_client as ts
from isp.services.infra.embedding_service import embed_query as _embed_query
from isp.services.infra.chat.providers import anthropic_chat, google_chat, openai_compat

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_chat_provider: str = "none"
_chat_model:    str = ""
_chat_api_key:  str = ""
_chat_endpoint: str = ""


def get_provider() -> str:
    return _chat_provider


def set_chat_config(*, provider: str, model: str, api_key: str, endpoint: str) -> None:
    global _chat_provider, _chat_model, _chat_api_key, _chat_endpoint
    _chat_provider = provider
    _chat_model    = model
    _chat_api_key  = api_key
    _chat_endpoint = endpoint
    if provider == "anthropic":
        anthropic_chat.configure(api_key)
    elif provider == "google":
        google_chat.configure(api_key)


async def _search_context(query: str, index_id: str) -> tuple[list[dict], str]:
    """Search top-5 docs in the ISP index and return (product_cards, context_text).

    Uses multi_search (POST body) to avoid Typesense's 4000-char URL limit when
    the vector query string is too long for a GET request.
    Runs lexical + semantic passes and merges via RRF (0.7 lex / 0.3 sem).
    """
    vec = await _embed_query(query)
    col = ts.collection_name(index_id)
    client = ts.get_client()

    N = 15  # over-fetch before RRF slice
    lex_params: dict = {
        "collection": col,
        "q": query,
        "query_by": "title,content",
        "per_page": str(N),
        "exclude_fields": "vec",
    }

    if vec:
        vec_str = ",".join(str(v) for v in vec)
        sem_params: dict = {
            "collection": col,
            "q": "*",
            "query_by": "title",
            "vector_query": f"vec:([{vec_str}], k:{N})",
            "per_page": str(N),
            "exclude_fields": "vec",
        }
        resp = await asyncio.to_thread(
            lambda: client.multi_search.perform({"searches": [lex_params, sem_params]}, {})
        )
        results_list = resp.get("results", [{}, {}])
        lex_hits_list = results_list[0].get("hits", [])
        sem_hits_list = results_list[1].get("hits", [])

        lex_map = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(lex_hits_list)}
        sem_map = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(sem_hits_list)}
        K = 60
        scores: dict[str, float] = {}
        for did, (rank, _) in lex_map.items():
            scores[did] = scores.get(did, 0) + 0.7 / (K + rank + 1)
        for did, (rank, _) in sem_map.items():
            scores[did] = scores.get(did, 0) + 0.3 / (K + rank + 1)
        all_docs = {**{d: doc for d, (_, doc) in lex_map.items()},
                    **{d: doc for d, (_, doc) in sem_map.items()}}
        top5_ids = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)[:5]
        raw_hits = [{"document": all_docs[did]} for did in top5_ids]
    else:
        resp = await asyncio.to_thread(
            lambda: client.multi_search.perform({"searches": [lex_params]}, {})
        )
        raw_hits = resp.get("results", [{}])[0].get("hits", [])[:5]

    hits = raw_hits

    cards: list[dict] = []
    parts: list[str]  = []

    for i, hit in enumerate(hits, 1):
        doc  = hit.get("document", {})
        meta: dict = {}
        raw  = doc.get("metadata", "")
        if raw:
            try:
                meta = json.loads(raw)
            except Exception:
                pass

        title   = doc.get("title", "")
        content = doc.get("content", "")[:300]
        url     = doc.get("url", "")

        raw_price = meta.get("price", meta.get("Price", ""))
        price = str(raw_price) if raw_price != "" else ""

        # images field can be a CDN array (devices) or a single URL string (plants)
        images_field = meta.get("images")
        if isinstance(images_field, list):
            image = images_field[0] if images_field else ""
        else:
            image = meta.get("image_url", meta.get("image", meta.get("Image", meta.get("thumbnail", ""))))

        # Build rich context line for the LLM
        price_str = f"${raw_price}" if raw_price != "" else ""
        pet        = meta.get("pet_safety_label", "")
        difficulty = meta.get("care_difficulty", "")
        growth     = meta.get("growth_style", "")
        extras     = ", ".join(x for x in [price_str, pet, difficulty, growth] if x)
        parts.append(f"{i}. {title}{' — ' + extras if extras else ''}\n{content}")
        cards.append({
            "id":    doc.get("id", ""),
            "title": title,
            "price": price,
            "url":   url,
            "image": image,
        })

    return cards, "\n\n".join(parts)


def _system_prompt(context: str) -> str:
    return (
        "You are a helpful product expert assistant. "
        "Answer questions using only the products listed below. "
        "Be concise — 2 to 3 sentences maximum. "
        "If no product matches the question, say so honestly.\n\n"
        f"Products:\n{context}"
    )


async def chat_stream(
    query: str,
    messages: list[dict],
    index_id: str,
) -> AsyncIterator[dict]:
    """
    Yield SSE-ready dicts.  Two message types:
      {"type": "products", "products": [...]}   — sent first, renders product cards
      {"type": "text",     "content": "..."}    — streamed LLM tokens
      {"type": "error",    "content": "..."}    — sent on misconfiguration
    """
    if _chat_provider == "none":
        yield {"type": "error", "content": "Chat is not configured. Go to Settings → Chat to choose a provider."}
        return

    cards, context = await _search_context(query, index_id)
    yield {"type": "products", "products": cards}

    system  = _system_prompt(context)
    history = messages + [{"role": "user", "content": query}]

    if _chat_provider == "anthropic":
        gen = anthropic_chat.stream(system, history, _chat_model)
    elif _chat_provider == "google":
        gen = google_chat.stream(system, history, _chat_model)
    elif _chat_provider == "groq":
        gen = openai_compat.stream(system, history, _chat_model, _chat_api_key,
                                   base_url=_GROQ_BASE_URL)
    elif _chat_provider == "self_hosted":
        gen = openai_compat.stream(system, history, _chat_model, _chat_api_key,
                                   base_url=_chat_endpoint)
    else:  # openai
        gen = openai_compat.stream(system, history, _chat_model, _chat_api_key)

    try:
        async for chunk in gen:
            yield {"type": "text", "content": chunk}
    except Exception as exc:
        yield {"type": "error", "content": str(exc)}
