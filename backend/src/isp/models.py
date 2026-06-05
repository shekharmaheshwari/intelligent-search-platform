"""
Pydantic data models shared across routers and services.

Design notes:
- ``RestConnectorConfig`` is persisted as JSON in the ``connectors`` JSONB column,
  so field additions must be backward-compatible (always optional or have defaults).
- ``IndexMeta`` mirrors the ``indexes`` table 1-to-1; keep them in sync when altering
  the database schema.
- ``DocumentItem`` is the canonical search result shape; external docs are adapted to
  it in ``_map_external_doc``.
"""
from __future__ import annotations
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field


class RestConnectorConfig(BaseModel):
    """
    Configuration for a REST API data source connector.

    Stored in the ``connectors`` JSONB column so the Rebuild panel can
    pre-populate all fields without the user re-entering them.
    """
    type: Literal["rest"] = "rest"
    url: str
    method: str = "GET"
    headers: dict[str, str] = {}
    body: str = ""           # raw JSON string for POST/PUT body
    result_path: str = ""
    id_field: str = "id"
    title_field: str = "title"
    content_field: str = "content"
    url_field: str = "url"
    field_map: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional source→target field renames applied before indexing. "
            "Key = field name in the API response, value = field name to store in ISP. "
            "Use the special target names 'title', 'content', 'url', 'id' to override "
            "the core field shortcuts above. Any other target name is stored as an "
            "extra field on the document. Set a value to '' to drop that field entirely."
        ),
    )


class SitemapConnectorConfig(BaseModel):
    """Configuration for a sitemap-based crawler data source."""
    type: Literal["sitemap"] = "sitemap"
    url: str
    max_pages: int = Field(default=50, ge=1, le=500)
    content_selector: str = ""
    include_pattern: str = ""
    exclude_pattern: str = ""


ConnectorConfig = Union[RestConnectorConfig, SitemapConnectorConfig]


class IndexCreate(BaseModel):
    """Request body for creating a new ISP index."""
    name: str = Field(min_length=1, max_length=80)
    description: str = ""


class ConnectorAdd(BaseModel):
    """Wraps a connector config for the add-connector endpoint."""
    config: Union[RestConnectorConfig, SitemapConnectorConfig] = Field(discriminator="type")


class IndexMeta(BaseModel):
    """
    Full metadata for one ISP source (one row in the ``indexes`` table).

    For ISP-managed sources ``collection_name`` is ``isp_{id}`` and all
    ``external_*`` fields are empty.  For external sources ``is_external=True``,
    ``collection_name`` is empty, and ``external_collection`` + ``external_filter``
    identify the Typesense collection and which documents belong to this source.
    """
    id: str
    name: str
    description: str
    created_at: str
    doc_count: int = 0
    last_ingested: Optional[str] = None
    status: str = "ready"
    connectors: list[dict] = []
    collection_name: str = ""
    # External (pre-existing Typesense collection) fields
    is_external: bool = False
    external_collection: str = ""   # actual Typesense collection name
    external_filter: str = ""       # e.g. "type:=phone"
    external_query_by: str = "display"
    # Field mapping for external collections
    external_title_field: str = "display"
    external_url_field: str = ""


class IngestStatus(BaseModel):
    """Tracks the live state of an ingest or rebuild background job."""
    index_id: str
    state: str = "idle"      # idle | queued | running | done | error
    total: int = 0
    indexed: int = 0
    error: str = ""


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
