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
}

/** Connector configuration stored in the ``connectors`` JSONB column on an index. */
export interface ConnectorConfig {
  type: 'rest' | 'sitemap' | 'external';
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
}

/** Form values for a sitemap crawler connector. */
export interface SitemapConnectorConfig {
  url: string;
  max_pages: number;
  content_selector: string;
  include_pattern: string;
  exclude_pattern: string;
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

/** A JS transform extension from the global library (mirrors backend extensions table). */
export interface ExtensionMeta {
  id: string;
  name: string;
  description: string;
  code: string;
  created_at: string;
  updated_at: string;
}

/**
 * An extension assigned to a specific index.
 * Extends ``ExtensionMeta`` with ``run_order`` (execution sequence, lower runs first)
 * and ``enabled`` (can be toggled off without removing the assignment).
 */
export interface IndexExtension extends ExtensionMeta {
  run_order: number;
  enabled: boolean;
}

/**
 * One entry in the model catalogue returned by GET /v1/settings.
 * ``num_dim`` is the vector dimension the model produces (0 for the "custom"
 * sentinel whose dimension is user-supplied).  ``provider`` is one of
 * ``"voyage"``, ``"openai"``, or ``"custom"``.
 */
export interface EmbedModelOption {
  id: string;
  label: string;
  description: string;
  num_dim: number;
  provider: string;
}

/**
 * Full settings payload returned by GET /v1/settings and PUT /v1/settings.
 *
 * ``dim_changed`` is only present on the PUT response; it is ``true`` when the
 * newly selected model produces vectors of a different dimension than the previous
 * model, meaning existing indexes need a full rebuild.
 *
 * Custom-provider fields (``custom_endpoint``, ``custom_model_name``,
 * ``custom_num_dim``) are populated when the active model is "custom".
 * ``openai_api_key_set`` is ``true`` when an OpenAI key is stored — the key
 * itself is never returned by the API.
 */
export interface AppSettings {
  embed_model: string;
  embed_model_num_dim: number;
  available_models: EmbedModelOption[];
  dim_changed?: boolean;
  openai_api_key_set?: boolean;
  rexbert_endpoint?: string | null;
  rexbert_api_key_set?: boolean;
  custom_endpoint?: string | null;
  custom_model_name?: string | null;
  custom_num_dim?: number | null;
}

/** Which top-level panel is currently visible in App.svelte. */
export type View = 'dashboard' | 'detail' | 'create' | 'configure' | 'extensions' | 'settings';

/** Global UI state held in the ``appState`` Svelte store. */
export interface AppState {
  view: View;
  selectedIndexId: string | null;
  detailTab: 'overview' | 'documents' | 'search' | 'api';
}
