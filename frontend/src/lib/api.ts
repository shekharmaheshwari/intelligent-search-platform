/**
 * Typed HTTP client for the ISP backend API.
 *
 * All methods go through the single ``req`` helper which sets JSON headers,
 * throws on non-2xx responses, and returns the parsed JSON body.
 * ``BASE`` is set at Astro build time via the ``PUBLIC_API_URL`` env var;
 * it falls back to ``http://localhost:8001/v1`` for local development.
 */
import type {
  IndexMeta, IngestStatus, SearchResult,
  RestConnectorConfig, SitemapConnectorConfig,
  ExtensionMeta, IndexExtension,
} from './types';

const BASE = import.meta.env.PUBLIC_API_URL || 'http://localhost:8001/v1';

/**
 * Core fetch wrapper.  Throws an ``Error`` with the status code and response
 * body text on any non-2xx response so callers can surface the message directly.
 * @param path  Path relative to ``BASE`` (must start with ``/``).
 * @param opts  Standard ``RequestInit`` options merged with default JSON headers.
 */
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
  /** Fetch the full list of ISP sources from the backend. */
  listIndexes: () => req<IndexMeta[]>('/indexes'),

  /**
   * Trigger auto-discovery of external Typesense collections not yet tracked by ISP.
   * Safe to call repeatedly — already-known collections are ignored.
   */
  discoverIndexes: () => req<IndexMeta[]>('/indexes/discover', { method: 'POST' }),

  /** Create a new empty ISP index (name + description only; no connector yet). */
  createIndex: (name: string, description: string) =>
    req<IndexMeta>('/indexes', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    }),

  /** Fetch the metadata for a single index by id. */
  getIndex: (id: string) => req<IndexMeta>(`/indexes/${id}`),

  /**
   * Delete an index and all its Typesense documents.
   * Uses raw ``fetch`` (not ``req``) because 204 No Content has no JSON body.
   */
  deleteIndex: (id: string) =>
    fetch(`${BASE}/indexes/${id}`, { method: 'DELETE' }),

  /** Poll the current ingest/rebuild job state for an index. */
  getStatus: (id: string) => req<IngestStatus>(`/indexes/${id}/status`),

  /**
   * Start a REST ingest job that appends documents to the index.
   * Returns immediately with ``state=queued``; poll ``getStatus`` to track progress.
   */
  ingestRest: (id: string, cfg: RestConnectorConfig) =>
    req<IngestStatus>(`/indexes/${id}/ingest/rest`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  /**
   * Start a sitemap crawl job that appends pages to the index.
   * Returns immediately with ``state=queued``; poll ``getStatus`` to track progress.
   */
  ingestSitemap: (id: string, cfg: SitemapConnectorConfig) =>
    req<IngestStatus>(`/indexes/${id}/ingest/sitemap`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  /** Paginated listing of all documents in an index, sorted newest first. */
  listDocuments: (id: string, page = 1, perPage = 20) =>
    req<SearchResult>(`/indexes/${id}/documents?page=${page}&per_page=${perPage}`),

  /**
   * Search documents in an index.
   * @param mode  ``hybrid`` (default) | ``lexical`` | ``semantic``
   */
  search: (id: string, q: string, mode: string, page = 1, perPage = 10) =>
    req<SearchResult>(`/indexes/${id}/search`, {
      method: 'POST',
      body: JSON.stringify({ q, mode, page, per_page: perPage }),
    }),

  /**
   * Full rebuild: drop all existing documents and re-fetch from the connector.
   * For catalog write-back sources this runs the full extension pipeline.
   */
  rebuild: (id: string, cfg: RestConnectorConfig) =>
    req<IngestStatus>(`/indexes/${id}/rebuild`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  // ── Extensions library ─────────────────────────────────────────────────────

  /** Fetch all extensions from the global library, newest first. */
  listExtensions: () => req<ExtensionMeta[]>('/extensions'),

  /** Create a new JS transform extension in the global library. */
  createExtension: (name: string, description: string, code: string) =>
    req<ExtensionMeta>('/extensions', {
      method: 'POST',
      body: JSON.stringify({ name, description, code }),
    }),

  /** Partially update an extension. Pass only the fields that should change. */
  updateExtension: (id: string, fields: Partial<Pick<ExtensionMeta, 'name' | 'description' | 'code'>>) =>
    req<ExtensionMeta>(`/extensions/${id}`, {
      method: 'PUT',
      body: JSON.stringify(fields),
    }),

  /**
   * Permanently delete an extension from the library.
   * Any index assignments for this extension are cascade-deleted by the backend.
   */
  deleteExtension: (id: string) =>
    fetch(`${BASE}/extensions/${id}`, { method: 'DELETE' }),

  // ── Per-index extension assignment ─────────────────────────────────────────

  /** Fetch extensions assigned to an index, ordered by run_order. */
  listIndexExtensions: (indexId: string) =>
    req<IndexExtension[]>(`/indexes/${indexId}/extensions`),

  /**
   * Assign an extension to an index (or update an existing assignment).
   * @param runOrder  Execution order — lower numbers run first.
   * @param enabled   Whether the extension is active; false skips it without removing.
   */
  assignExtension: (indexId: string, extensionId: string, runOrder: number, enabled: boolean) =>
    req<{ ok: boolean }>(`/indexes/${indexId}/extensions`, {
      method: 'POST',
      body: JSON.stringify({ extension_id: extensionId, run_order: runOrder, enabled }),
    }),

  /**
   * Remove an extension assignment from an index.
   * The extension remains in the global library and can be re-assigned later.
   */
  unassignExtension: (indexId: string, extensionId: string) =>
    fetch(`${BASE}/indexes/${indexId}/extensions/${extensionId}`, { method: 'DELETE' }),
};
