/** Mode + magnitude for one item-interaction sub-policy. */
export interface ItemPolicy {
  mode:  'penalise' | 'neutral' | 'boost';
  value: number;
}

/**
 * Per-index policy controlling how viewed and purchased items are scored.
 * Stored in ``indexes.viewed_item_policy``.
 *
 * viewed:    item seen but not bought — active interest, need unfulfilled.
 * purchased: item bought (durable good) — need satisfied, reduce re-surfacing.
 */
export interface ViewedItemPolicy {
  viewed:    ItemPolicy;
  purchased: ItemPolicy;
}

/**
 * One discovered affinity dimension for an index.
 * Stored in ``indexes.affinity_dimensions`` and used by the personalisation engine.
 */
export interface AffinityDimension {
  field:        string;
  label:        string;
  boost:        number;
  enabled:      boolean;
  values:       string[];
  derived_from: string | null;
}

/** One criterion in a boost rule — field + operator + value. */
export interface BoostCriterion {
  field:    string;
  operator: 'equals' | 'contains' | 'regex';
  value:    string;
  /** Display title for id-field targets picked via Browse. Persisted so title survives page reload. Backend ignores it. */
  _title?:  string;
}

/**
 * One operator-defined merchandising boost rule.
 * trigger: ALL must match (AND) — activates the rule. Supports __search_term__ field.
 * targets: ANY one must match (OR) — selects which documents get the boost.
 * Rules without a __search_term__ trigger fire on browse; those with one fire on search.
 * Stored in ``indexes.boost_rules``.
 */
export interface BoostRule {
  id:       string;
  label:    string;
  enabled:  boolean;
  boost:    number;       // 0.00–1.00 (clamped server-side)
  trigger:  BoostCriterion[];
  targets:  BoostCriterion[];
}

/**
 * Full metadata for one ISP source, mirroring the backend ``IndexMeta`` Pydantic model.
 * ``is_external=true`` means ISP reads/writes a pre-existing Typesense collection;
 * ``collection_name`` is empty for external sources, ``isp_{id}`` for managed ones.
 */
export interface IndexMeta {
  id: string;
  name: string;
  description: string;
  created_at: string;
  collection_name: string;
  connectors: ConnectorConfig[];
  doc_count: number;
  is_external?: boolean;
  external_collection?: string;
  external_filter?: string;
  external_query_by?: string;
  external_title_field?: string;
  external_url_field?: string;
  /** Per-index embedding model override. Empty string means "use the global default". */
  embed_model?: string;
  /** When true, newer product generations receive a small relevance boost at search time. */
  version_boost?: boolean;
  /** Inferred intent category label (e.g. "product_search") used to pre-fill the suggestions category field. */
  intent_label?: string | null;
  /** Affinity dimensions discovered after the last rebuild. Drives per-index personalisation. */
  affinity_dimensions?: AffinityDimension[];
}

/** Connector configuration stored in the ``connectors`` JSONB column on an index. */
export interface ConnectorConfig {
  type: 'rest' | 'sitemap' | 'gsheet' | 'external';
  url?: string;
  [key: string]: unknown;
}

/** Form values for a REST API connector, sent to POST /v1/indexes/{id}/ingest/rest or rebuild. */
export interface RestConnectorConfig {
  url: string;
  method: string;
  headers: Record<string, string>;
  body?: string;
  result_path?: string;
  id_field?: string;
  title_field?: string;
  content_field?: string;
  url_field?: string;
  pre_processor?: string;
}

/** Form values for a Google Sheets connector. */
export interface GSheetConnectorConfig {
  type: 'gsheet';
  spreadsheet_id: string;
  sheet_name: string;
  id_column: string;
  title_column: string;
  content_column: string;
  url_column: string;
  /** Maps column headers to top-level ISP document field names for additional fields beyond the four core fields. */
  field_map: Record<string, string>;
  /** When true, rows where every column is empty are silently skipped. */
  skip_empty_rows: boolean;
  /** 1-based row number that contains column headers. */
  header_row: number;
}

/** Form values for a sitemap crawler connector. */
export interface SitemapConnectorConfig {
  url: string;
  max_pages: number;
  content_selector: string;
  /** URL must contain at least one of these substrings. Empty array = no filter. */
  include_patterns: string[];
  /** URL must not contain any of these substrings. Empty array = no filter. */
  exclude_patterns: string[];
  /** Maps HTML meta tag names (e.g. "article-author") to top-level ISP document field names (e.g. "author"). Promoted fields become filterable in Typesense. */
  meta_fields: Record<string, string>;
  /** Override the default 'ISP-Crawler/1.0' User-Agent. Use a browser UA for sites that block automated crawlers. */
  user_agent: string;
}

/**
 * Live state of an ingest or rebuild background job, polled every ~1.2 s.
 * ``state`` progresses: idle → queued → running → done | error.
 */
export interface IngestStatus {
  index_id: string;
  state: 'idle' | 'queued' | 'running' | 'done' | 'error';
  total: number;
  indexed: number;
  error: string;
}

/** Paginated response from the search and list-documents endpoints. */
export interface SearchResult {
  hits: Array<{ document: DocumentItem }>;
  found: number;
  page: number;
}

/** Canonical search result document shape returned by the backend. */
export interface DocumentItem {
  id: string;
  title: string;
  content: string;
  url: string;
  source_type: string;
  indexed_at: number;
  metadata?: string;
  [key: string]: unknown;
}

/**
 * Connector mapping config sent to ``PUT /v1/indexes/{id}/connectors/mapping``.
 * ``field_map`` renames source keys before indexing; ``value_map`` rewrites field
 * values after renaming; ``static_fields`` injects constant key→value pairs into
 * every document regardless of source data.  All maps are applied at ingest time,
 * not at query time.
 */
export interface MappingConfig {
  field_map:     Record<string, string>;
  value_map:     Record<string, Record<string, string>>;
  /** Constant key→value pairs stamped onto every document at ingest time. */
  static_fields: Record<string, string>;
}
