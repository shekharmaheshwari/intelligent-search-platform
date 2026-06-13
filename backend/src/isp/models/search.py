"""
Pydantic models for search requests and document results.

``DocumentItem`` is the canonical shape returned by search, list-documents,
and the public content API.  ``SearchRequest`` is the POST body accepted by
every ``/search`` endpoint.
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Request body for the search endpoint."""
    q: str
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=10, ge=1, le=50)
    mode: str = "hybrid"


class DocumentItem(BaseModel):
    """Canonical search result shape returned to the frontend and content API."""
    id: str
    title: str
    content: str
    url: str = ""
    source_type: str = ""
    indexed_at: int = 0
