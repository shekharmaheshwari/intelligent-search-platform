import type {
  IndexMeta, IngestStatus, SearchResult,
  RestConnectorConfig, SitemapConnectorConfig,
} from './types';

const BASE = import.meta.env.PUBLIC_API_URL || 'http://localhost:8001/v1';

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...opts.headers as Record<string, string> },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listIndexes: () => req<IndexMeta[]>('/indexes'),

  discoverIndexes: () => req<IndexMeta[]>('/indexes/discover', { method: 'POST' }),

  createIndex: (name: string, description: string) =>
    req<IndexMeta>('/indexes', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    }),

  getIndex: (id: string) => req<IndexMeta>(`/indexes/${id}`),

  deleteIndex: (id: string) =>
    fetch(`${BASE}/indexes/${id}`, { method: 'DELETE' }),

  getStatus: (id: string) => req<IngestStatus>(`/indexes/${id}/status`),

  ingestRest: (id: string, cfg: RestConnectorConfig) =>
    req<IngestStatus>(`/indexes/${id}/ingest/rest`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  ingestSitemap: (id: string, cfg: SitemapConnectorConfig) =>
    req<IngestStatus>(`/indexes/${id}/ingest/sitemap`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  listDocuments: (id: string, page = 1, perPage = 20) =>
    req<SearchResult>(`/indexes/${id}/documents?page=${page}&per_page=${perPage}`),

  search: (id: string, q: string, mode: string, page = 1, perPage = 10) =>
    req<SearchResult>(`/indexes/${id}/search`, {
      method: 'POST',
      body: JSON.stringify({ q, mode, page, per_page: perPage }),
    }),

  rebuild: (id: string, cfg: RestConnectorConfig) =>
    req<IngestStatus>(`/indexes/${id}/rebuild`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),
};
