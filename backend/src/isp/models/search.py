"""
Pydantic models for search requests and document results.

``DocumentItem`` is the canonical shape returned by search, list-documents,
and the public content API.  ``SearchRequest`` is the POST body accepted by
every ``/search`` endpoint.
"""
from __future__ import annotations
from pydantic import BaseModel, Field


class CustomerAction(BaseModel):
    """
    A single customer interaction event passed in ``action_history``.

    ISP uses these events to personalise ranking: affinity scores are derived
    from category/manufacturer/type signals, and embedding vectors of recently
    viewed items are blended into the query vector to steer semantic search
    toward products the customer actually engages with.

    ``type`` values and their weights:
      - ``purchase`` (3×) — strongest intent signal
      - ``cart``     (2×) — clear interest
      - ``view``     (1×) — browsing signal
      - ``search``   (0.5×) — query intent (no product chosen yet)
      - ``category`` (0.3×) — broad interest signal
    """
    type:         str         = "view"
    item_id:      str | None  = None   # Typesense doc id — used for vector blending
    item_type:    str | None  = None   # "device" | "accessory"
    category:     str | None  = None
    manufacturer: str | None  = None
    query:        str | None  = None   # past search query (type="search")
    weight:       float       = 1.0    # caller-supplied multiplier for recent / high-priority actions
    timestamp:    int | None  = None   # Unix ms — set by client; used for time-decay (Phase 2)


class SearchRequest(BaseModel):
    """Request body for the search endpoint."""
    q: str
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=10, ge=1, le=50)
    mode: str = "hybrid"
    manufacturer: list[str] | str | None = None
    """Filter results to these manufacturers (single value or list for multi-select)."""
    category: list[str] | str | None = None
    """Filter results to these category slugs (single value or list for multi-select)."""
    action_history: list[CustomerAction] = Field(
        default_factory=list,
        description=(
            "Recent customer actions used to personalise ranking. "
            "Viewed-item vectors are blended into the query embedding; "
            "category and manufacturer signals boost matching documents after RRF."
        ),
    )
    pinned_hits: list[str] = Field(
        default_factory=list,
        description="Doc IDs to force to the top of results in order (position 1, 2, …).",
    )


class DocumentItem(BaseModel):
    """Canonical search result shape returned to the frontend and content API."""
    id: str
    title: str
    content: str
    url: str = ""
    source_type: str = ""
    indexed_at: int = 0
