"""
/v1/indexes/{index_id}/chat — conversational RAG over an ISP index.

POST body:
  query    — the user's current message (plain text)
  messages — prior conversation turns [{role, content}, ...]

Response: SSE stream (text/event-stream).
Each event is a JSON object, one of:
  {"type": "products", "products": [...]}   — top-5 matching products (arrives first)
  {"type": "text",     "content": "..."}    — LLM token chunk
  {"type": "error",    "content": "..."}    — provider not configured
  data: [DONE]                              — stream end sentinel
"""
from __future__ import annotations
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from isp.services.infra.chat import service as chat_service
from isp.services.domain import index_service

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    query:    str
    messages: list[dict] = []


@router.post("/indexes/{index_id}/chat")
async def chat(index_id: str, body: ChatRequest) -> StreamingResponse:
    """Stream a chat response grounded in the given ISP index."""
    # Resolve chat_fields (LLM context attributes) and query_by (retrieval fields)
    # from field_config. Done here so the infra service stays domain-agnostic.
    meta = await index_service.get_index(index_id)
    chat_fields: list[str] = []
    query_by: str = "title,content"
    query_by_weights: str | None = None
    facet_query_by: str | None = None
    if meta and meta.field_config:
        chat_fields = [fc["name"] for fc in meta.field_config if fc.get("chat")]
        # Structured fields that are searchable get top weight so they dominate
        # over generic word matches in title/content for attribute-specific queries.
        extra_qb = [
            fc["name"] for fc in meta.field_config
            if fc.get("searchable")
            and fc["name"] not in {"title", "content"}
            and fc.get("type", "string").startswith("string")
        ]
        if extra_qb:
            # Put structured fields first with high weight; title/content last at weight 1
            query_by = ",".join(extra_qb) + ",title,content"
            query_by_weights = ",".join(["10"] * len(extra_qb)) + ",1,1"

        # Structured-label pass: facet fields whose type is EXPLICITLY stored in
        # field_config (i.e. the operator made them searchable via the Fields tab,
        # which persists the Typesense type).  Fields without an explicit type are
        # skipped — they may be bools, proper-noun identifier strings, or long text
        # that would create false positive matches in the exact-word pass.
        facet_qb = [
            fc["name"] for fc in meta.field_config
            if fc.get("facet")
            and fc["name"] not in {"title", "content"}
            and fc.get("type") is not None          # explicit type only
            and fc["type"].startswith("string")
        ]
        if facet_qb:
            facet_query_by = ",".join(facet_qb)

    async def generate():
        async for chunk in chat_service.chat_stream(body.query, body.messages, index_id, chat_fields, query_by, query_by_weights, facet_query_by):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
