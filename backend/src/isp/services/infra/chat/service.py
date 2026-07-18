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


# Fields never rendered as context attributes — images/URLs/descriptions are
# already covered by title, content, and image card data.
_CHAT_FIELD_SKIP = {"image_url", "image", "images", "thumbnail", "url", "url_name", "description"}


async def _search_context(query: str, index_id: str, chat_fields: list[str],
                          query_by: str = "title,content",
                          query_by_weights: str | None = None,
                          facet_query_by: str | None = None) -> tuple[list[dict], str]:
    """Search top-5 docs in the ISP index and return (product_cards, context_text).

    Uses multi_search (POST body) to avoid Typesense's 4000-char URL limit when
    the vector query string is too long for a GET request.
    Runs up to three passes and merges via RRF:
      - lex-all    (0.5): BM25 over all fields (structured + title + content)
      - lex-facets (0.3): BM25 over facet fields only — these hold short categorical
                          values ("Pet safe", "Hoya") so exact attribute queries like
                          "plants which are pet safe" resolve correctly without long
                          descriptive fields creating noise
      - sem        (0.2): vector nearest-neighbour

    ``chat_fields``, ``query_by``, and ``facet_query_by`` are resolved by the
    router from field_config and passed in — the service does not touch the domain layer.
    """
    vec = await _embed_query(query)
    col = ts.collection_name(index_id)
    client = ts.get_client()

    N = 15  # over-fetch before RRF slice
    lex_params: dict = {
        "collection": col,
        "q": query,
        "query_by": query_by,
        "per_page": str(N),
        "exclude_fields": "vec",
    }
    if query_by_weights:
        lex_params["query_by_weights"] = query_by_weights

    # Facet-only pass: short categorical fields let the query resolve exact attribute
    # values ("Pet safe") even when the full query has many generic words ("plants",
    # "which", "are").  num_typos=0 prevents accidental stemming matches (e.g.
    # "plants" matching "plant" in a plant_family value like "Coffee Plant").
    lex_struct_params: dict | None = None
    if facet_query_by:
        lex_struct_params = {
            "collection": col,
            "q": query,
            "query_by": facet_query_by,
            "num_typos": "0",
            "per_page": str(N),
            "exclude_fields": "vec",
        }

    searches = [lex_params]
    if lex_struct_params:
        searches.append(lex_struct_params)

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
        searches.append(sem_params)

    resp = await asyncio.to_thread(
        lambda: client.multi_search.perform({"searches": searches}, {})
    )
    results_list = resp.get("results", [])

    idx = 0
    lex_hits_list = results_list[idx].get("hits", []) if idx < len(results_list) else []
    idx += 1
    lex_struct_hits_list = results_list[idx].get("hits", []) if (lex_struct_params and idx < len(results_list)) else []
    if lex_struct_params:
        idx += 1
    sem_hits_list = results_list[idx].get("hits", []) if (vec and idx < len(results_list)) else []

    if vec or lex_struct_params:
        lex_map    = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(lex_hits_list)}
        struct_map = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(lex_struct_hits_list)}
        sem_map    = {h["document"]["id"]: (rank, h["document"]) for rank, h in enumerate(sem_hits_list)}
        K = 60
        scores: dict[str, float] = {}
        for did, (rank, _) in lex_map.items():
            scores[did] = scores.get(did, 0) + 0.3 / (K + rank + 1)
        # Facet pass gets highest weight: it uses exact-word (num_typos=0) matching
        # over short categorical fields, so it is precise and should dominate when
        # there ARE matches (e.g. "pet safe" → pet_safety_label="Pet safe").
        for did, (rank, _) in struct_map.items():
            scores[did] = scores.get(did, 0) + 0.6 / (K + rank + 1)
        for did, (rank, _) in sem_map.items():
            scores[did] = scores.get(did, 0) + 0.1 / (K + rank + 1)
        all_docs = {**{d: doc for d, (_, doc) in lex_map.items()},
                    **{d: doc for d, (_, doc) in struct_map.items()},
                    **{d: doc for d, (_, doc) in sem_map.items()}}
        top5_ids = sorted(all_docs.keys(), key=lambda d: scores[d], reverse=True)[:5]
        hits = [{"document": all_docs[did]} for did in top5_ids]
    else:
        hits = lex_hits_list[:5]

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

        # Build context block from operator-configured chat fields only.
        # Each field gets its own labelled line so the LLM can match values to
        # the correct product without ambiguity.
        attr_lines: list[str] = []
        for field_name in chat_fields:
            if field_name in _CHAT_FIELD_SKIP:
                continue
            value = meta.get(field_name)
            if value is None:
                value = doc.get(field_name)
            if value is None or value == "":
                continue
            label = field_name.replace("_", " ").title()
            attr_lines.append(f"  {label}: {value}")

        block = f"{i}. {title}"
        if attr_lines:
            block += "\n" + "\n".join(attr_lines)
        if content:
            block += f"\n  Description: {content}"
        parts.append(block)

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
        "Answer questions using ONLY the structured product data listed below — "
        "do NOT rely on general knowledge about these products. "
        "Each product has labelled attributes; use those exact values when answering "
        "attribute-specific questions. "
        "Be concise — 2 to 3 sentences maximum. "
        "If no product matches the question, say so honestly.\n\n"
        f"Products:\n{context}"
    )


async def chat_stream(
    query: str,
    messages: list[dict],
    index_id: str,
    chat_fields: list[str],
    query_by: str = "title,content",
    query_by_weights: str | None = None,
    facet_query_by: str | None = None,
) -> AsyncIterator[dict]:
    """
    Yield SSE-ready dicts.  Two message types:
      {"type": "products", "products": [...]}   — sent first, renders product cards
      {"type": "text",     "content": "..."}    — streamed LLM tokens
      {"type": "error",    "content": "..."}    — sent on misconfiguration

    ``chat_fields`` is resolved by the router from the index's field_config.
    """
    if _chat_provider == "none":
        yield {"type": "error", "content": "Chat is not configured. Go to Settings → Chat to choose a provider."}
        return

    cards, context = await _search_context(query, index_id, chat_fields, query_by, query_by_weights, facet_query_by)
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
