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
}

export interface ConnectorConfig {
  type: 'rest' | 'sitemap' | 'external';
  url?: string;
  [key: string]: unknown;
}

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

export interface SitemapConnectorConfig {
  url: string;
  max_pages: number;
  content_selector: string;
  include_pattern: string;
  exclude_pattern: string;
}

export interface IngestStatus {
  index_id: string;
  state: 'idle' | 'queued' | 'running' | 'done' | 'error';
  total: number;
  indexed: number;
  error: string;
}

export interface SearchResult {
  hits: Array<{ document: DocumentItem }>;
  found: number;
  page: number;
}

export interface DocumentItem {
  id: string;
  title: string;
  content: string;
  url: string;
  source_type: string;
  indexed_at: number;
}

export interface ExtensionMeta {
  id: string;
  name: string;
  description: string;
  code: string;
  created_at: string;
  updated_at: string;
}

export interface IndexExtension extends ExtensionMeta {
  run_order: number;
  enabled: boolean;
}

export type View = 'dashboard' | 'detail' | 'create' | 'configure' | 'extensions';

export interface AppState {
  view: View;
  selectedIndexId: string | null;
  detailTab: 'overview' | 'documents' | 'search' | 'api';
}
