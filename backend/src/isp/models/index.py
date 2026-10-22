# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Pydantic models for the index/source domain.

``RestConnectorConfig``, ``SitemapConnectorConfig``, and ``GSheetConnectorConfig``
are persisted as JSON in the ``connectors`` JSONB column — new fields must be
optional or have defaults.
``IndexMeta`` mirrors the ``indexes`` table 1-to-1; keep them in sync when
altering the schema.
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
    body: str = ""
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
    value_map: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "Per-field value substitutions applied after field_map renames. "
            "Outer key = field name, inner key = raw value, inner value = replacement. "
            "Unmatched values pass through unchanged."
        ),
    )
    static_fields: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Static key-value pairs stamped onto every document at ingest time. "
            "Applied after field_map and value_map. Useful for injecting a category "
            "field when the source data does not include one."
        ),
    )
    pre_processor: str = Field(
        default="",
        description=(
            "Optional JavaScript that runs on the full raw API response before "
            "result_path extraction. Must define "
            "``function pre_process(response) { ... return response; }``. "
            "Use this to reshape non-standard response structures, cross-reference "
            "sibling arrays, or inject computed fields before per-item extensions run."
        ),
    )


class SitemapConnectorConfig(BaseModel):
    """Configuration for a sitemap-based crawler data source."""
    type: Literal["sitemap"] = "sitemap"
    url: str
    max_pages: int = Field(default=50, ge=1, le=10000)
    content_selector: Optional[str] = ""
    include_patterns: list[str] = Field(default_factory=list, description="URL must contain at least one of these substrings. Empty list = no filter.")
    exclude_patterns: list[str] = Field(default_factory=list, description="URL must not contain any of these substrings. Empty list = no filter.")
    meta_fields: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Maps HTML meta tag names (e.g. 'article-author') to top-level ISP "
            "document field names (e.g. 'author').  Promoted fields are stored "
            "alongside title/content/url and become filterable in Typesense."
        ),
    )
    user_agent: Optional[str] = Field(default="", description="Override the default 'ISP-Crawler/1.0' User-Agent header sent with every crawl request. Use a browser UA string for sites that block automated crawlers.")
    static_fields: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Static key-value pairs stamped onto every document at ingest time. "
            "Applied after field_map and value_map. Useful for injecting a category "
            "field when the source data does not include one."
        ),
    )


class GSheetConnectorConfig(BaseModel):
    """
    Configuration for a Google Sheets data source connector.

    Credentials are loaded at ingest time from the ``GOOGLE_SERVICE_ACCOUNT_JSON``
    environment variable (JSON string) — they are never stored in the database.
    All fields other than ``spreadsheet_id`` have defaults so existing connector
    records stored in the JSONB column remain valid after future field additions.
    """
    type: Literal["gsheet"] = "gsheet"
    spreadsheet_id: str
    sheet_name: str = "Sheet1"
    id_column: str = ""
    title_column: str = ""
    content_column: str = ""
    url_column: str = ""
    field_map: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional column header → ISP field renames applied before indexing. "
            "Key = column header as it appears in the sheet, value = ISP field name to use. "
            "Set value to '' to drop a column entirely."
        ),
    )
    skip_empty_rows: bool = True
    header_row: int = Field(default=1, ge=1, description="1-based row index containing column headers.")
    static_fields: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Static key-value pairs stamped onto every document at ingest time. "
            "Applied after field_map and value_map. Useful for injecting a category "
            "field when the source data does not include one."
        ),
    )


class JsonFileConnectorConfig(BaseModel):
    """
    Configuration for a local or remote JSON file data source.

    The file may be a local path accessible inside the container (e.g.
    ``/data/products.json``) or an ``https://`` URL.  ``root_key`` is the
    top-level dict key whose value is the array of items; leave empty to
    auto-detect the first non-empty array value.
    """
    type: Literal["json_file"] = "json_file"
    path: str
    root_key: str = ""
    id_field: str = "id"
    title_field: str = "title"
    content_field: str = "content"
    url_field: str = "url"
    static_fields: dict[str, str] = Field(default_factory=dict)


ConnectorConfig = Union[RestConnectorConfig, SitemapConnectorConfig, GSheetConnectorConfig, JsonFileConnectorConfig]


class IndexCreate(BaseModel):
    """Request body for creating a new ISP index."""
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    embed_model: str = ""  # empty string means "use the global default at ingest time"
    intent_label: str = ""  # semantic category for intent detection
    slug: str = ""  # URL-friendly identifier; auto-generated from name if omitted


class ConnectorAdd(BaseModel):
    """Wraps a connector config for the add-connector endpoint."""
    config: Union[RestConnectorConfig, SitemapConnectorConfig, GSheetConnectorConfig, JsonFileConnectorConfig] = Field(discriminator="type")


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
    is_external: bool = False
    external_collection: str = ""
    external_filter: str = ""
    external_query_by: str = "display"
    external_title_field: str = "display"
    external_url_field: str = ""
    embed_model: str = ""  # empty string means "use the global default at ingest/search time"
    version_boost: bool = False  # when True, newer product generations rank higher in search results
    intent_label: str = ""  # semantic category for intent detection (e.g. "device", "accessory", "smartwatch", "tablet")
    slug: str = ""  # URL-friendly identifier enabling /v1/indexes/{slug}/... resolution
    # Affinity dimensions discovered at rebuild time — drives generic personalisation scoring.
    # Each entry: {"field": str, "label": str, "boost": float, "enabled": bool, "values": list[str], "derived_from": str|None}
    affinity_dimensions: list[dict] = []
    # Controls how viewed/purchased items are scored per sub-policy.
    # viewed: item seen but not bought (active interest); purchased: need fulfilled.
    viewed_item_policy: dict = {
        "viewed":    {"mode": "penalise", "value": 0.05},
        "purchased": {"mode": "penalise", "value": 0.30},
    }
    # Operator-defined merchandising boost rules.
    # Each entry: {"id": str, "label": str, "enabled": bool, "boost": float,
    #              "criteria": [{"field": str, "operator": str, "value": str}, ...]}
    boost_rules: list[dict] = []
    # Operator-configured field-level search/facet properties.
    # Persisted here so they survive rebuilds (which recreate the Typesense collection).
    # Each entry: {"name": str, "facet": bool, "multi_value": bool, "searchable": bool}
    field_config: list[dict] = []
    # Per-index chat persona: how the chat assistant refers to items, whether it compares
    # them, and any additional operator-written instructions. Unset keys fall back to
    # defaults (see chat/service.py _CHAT_PERSONA_DEFAULTS) — {} behaves identically to
    # today's fixed prompt.
    chat_persona_config: dict = {}
    # Cache of resolved superlative-query concepts ("cheapest" -> price concept)
    # to this index's actual field names. Write-through cache — populated lazily
    # the first time a concept is resolved live, not recomputed on every request.
    # {} is always safe: chat/service.py falls back to live resolution when a
    # concept isn't (yet) in the cache. See docs/superlativePriceDesign.md.
    superlative_field_map: dict = {}


class IngestStatus(BaseModel):
    """Tracks the live state of an ingest or rebuild background job."""
    index_id: str
    state: str = "idle"      # idle | queued | running | done | error
    total: int = 0
    indexed: int = 0
    error: str = ""


class IntentLabelUpdate(BaseModel):
    """Request body for setting or clearing the intent label on an index."""
    intent_label: str


class MappingConfig(BaseModel):
    """
    Request body for saving field renames, value overrides, and static fields onto a connector.

    ``field_map`` and ``value_map`` are REST-specific; they are ignored for sitemap and gsheet
    connectors.  ``static_fields`` applies to every connector type — constant key-value pairs
    stamped onto every document at ingest time, overriding any same-named field from the source.

    Sending empty dicts clears any previously saved entries.
    """
    field_map:     dict[str, str]            = {}
    value_map:     dict[str, dict[str, str]] = {}
    static_fields: dict[str, str]            = {}
