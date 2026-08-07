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
    # Derive query_by from operator-marked searchable fields in field_config.
    # chatContent is always the facet pass target — it is built by the JS extension
    # at index time and contains all structured, labelled attributes for this index type.
    meta = await index_service.get_index(index_id)
    query_by: str = "chatContent,title,content"
    query_by_weights: str | None = None
    if meta and meta.field_config:
        extra_qb = [
            fc["name"] for fc in meta.field_config
            if fc.get("searchable")
            and fc["name"] not in {"title", "content", "chatContent"}
            and fc.get("type", "string").startswith("string")
        ]
        if extra_qb:
            query_by = ",".join(extra_qb) + ",chatContent,title,content"
            query_by_weights = ",".join(["10"] * len(extra_qb)) + ",5,1,1"

    persona_config = meta.chat_persona_config if meta else None
    superlative_field_map = meta.superlative_field_map if meta else None

    async def generate():
        async for chunk in chat_service.chat_stream(body.query, body.messages, index_id, query_by, query_by_weights, facet_query_by="chatContent", persona_config=persona_config, superlative_field_map=superlative_field_map):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
