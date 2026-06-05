from __future__ import annotations
from typing import Literal, Optional, Union
from pydantic import BaseModel, Field


class RestConnectorConfig(BaseModel):
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
    type: Literal["sitemap"] = "sitemap"
    url: str
    max_pages: int = Field(default=50, ge=1, le=500)
    content_selector: str = ""
    include_pattern: str = ""
    exclude_pattern: str = ""


ConnectorConfig = Union[RestConnectorConfig, SitemapConnectorConfig]


class IndexCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""


class ConnectorAdd(BaseModel):
    config: Union[RestConnectorConfig, SitemapConnectorConfig] = Field(discriminator="type")


class IndexMeta(BaseModel):
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
    index_id: str
    state: str = "idle"      # idle | queued | running | done | error
    total: int = 0
    indexed: int = 0
    error: str = ""


class SearchRequest(BaseModel):
    q: str
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=10, ge=1, le=50)
    mode: str = "hybrid"


class DocumentItem(BaseModel):
    id: str
    title: str
    content: str
    url: str = ""
    source_type: str = ""
    indexed_at: int = 0
