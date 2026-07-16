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

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    query:    str
    messages: list[dict] = []


@router.post("/indexes/{index_id}/chat")
async def chat(index_id: str, body: ChatRequest) -> StreamingResponse:
    """Stream a chat response grounded in the given ISP index."""

    async def generate():
        async for chunk in chat_service.chat_stream(body.query, body.messages, index_id):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
