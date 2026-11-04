# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
/v1/indexes/{index_id}/search/overview — AI overview of the top search results.

Kept separate from /search so search latency and the public search API are
untouched: the UI shows results first, then requests the overview for the
top hits it already has.

POST body:
  q       — the search query
  doc_ids — ids of the top hits, in rank order (first MAX_SOURCES are used)

Response: SSE stream (text/event-stream) of the events documented in
services/infra/overview/service.py, ending with ``data: [DONE]``.
"""
from __future__ import annotations
import json
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from isp.services.infra.overview import service as overview_service
from isp.routers.indexes import _resolve_index, _map_external_doc
from isp.services.infra import typesense_client as ts

router = APIRouter(tags=["search"])


class OverviewRequest(BaseModel):
    q:       str
    doc_ids: list[str] = Field(default_factory=list, max_length=50)


@router.post("/indexes/{index_id}/search/overview", summary="Stream an AI overview of search results")
async def search_overview(index_id: str, body: OverviewRequest) -> StreamingResponse:
    """Stream a short, cited summary of ``doc_ids`` for ``q``. Accepts hex id or slug."""
    if not overview_service.is_enabled():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI Overview is off — enable it in Settings → Chat Model.",
        )
    meta = await _resolve_index(index_id)
    if meta is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Index not found")

    if meta.is_external:
        # External docs live in a shared collection under the index's filter, with
        # their own field names — fetch in scope and map to title/content/url.
        src = overview_service.DocSource(
            collection=meta.external_collection,
            scope_filter=meta.external_filter,
            query_by=meta.external_query_by,
            normalize=lambda d: _map_external_doc(d, meta),
            external=True,
        )
    else:
        src = overview_service.DocSource(collection=ts.collection_name(meta.id))

    async def generate():
        async for event in overview_service.overview_stream(meta.id, src, body.q, body.doc_ids):
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
