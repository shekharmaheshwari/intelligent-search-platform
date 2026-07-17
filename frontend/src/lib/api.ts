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
  RestConnectorConfig, SitemapConnectorConfig, GSheetConnectorConfig,
  ExtensionMeta, IndexExtension, AppSettings,
  Suggestion, SuggestionsResponse,
  MappingConfig, FieldEntry, FieldConfig,
} from './types';

const BASE = import.meta.env.PUBLIC_API_URL || 'http://localhost:8001/v1';
const AUTH_ENABLED = import.meta.env.PUBLIC_AUTH_ENABLED === 'true';
const API_ROOT = BASE.replace('/v1', '');

/**
 * Core fetch wrapper.  Throws an ``Error`` with the status code and response
 * body text on any non-2xx response so callers can surface the message directly.
 *
 * When PUBLIC_AUTH_ENABLED=true it reads the JWT from localStorage and attaches
 * it as ``Authorization: Bearer``.  On 401 it clears the stored token and
 * redirects to the backend login endpoint so the OAuth flow restarts.
 *
 * @param path  Path relative to ``BASE`` (must start with ``/``).
 * @param opts  Standard ``RequestInit`` options merged with default JSON headers.
 */
async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...opts.headers as Record<string, string>,
  };

  if (AUTH_ENABLED) {
    const token = typeof localStorage !== 'undefined'
      ? localStorage.getItem('isp-auth-token')
      : null;
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${path}`, { ...opts, headers });

  if (res.status === 401 && AUTH_ENABLED) {
    localStorage.removeItem('isp-auth-token');
    window.location.href = `${API_ROOT}/auth/login`;
    throw new Error('Session expired — redirecting to login');
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return undefined as T;
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

  /**
   * Create a new ISP index.  Pass ``is_external: true`` with the external
   * collection details to create a catalog write-back source that shares the
   * pre-existing Typesense collection rather than owning its own.
   */
  createIndex: (
    name: string,
    description: string,
    opts?: {
      is_external?: boolean;
      external_collection?: string;
      external_filter?: string;
      external_query_by?: string;
      external_title_field?: string;
      external_url_field?: string;
    }
  ) =>
    req<IndexMeta>('/indexes', {
      method: 'POST',
      body: JSON.stringify({ name, description, ...opts }),
    }),

  /** Fetch the metadata for a single index by id. */
  getIndex: (id: string) => req<IndexMeta>(`/indexes/${id}`),

  /** Delete an index and all its Typesense documents. */
  deleteIndex: (id: string) =>
    req<void>(`/indexes/${id}`, { method: 'DELETE' }),

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

  /**
   * Start a Google Sheets ingest job that reads rows from the specified spreadsheet.
   * Returns immediately with ``state=queued``; poll ``getStatus`` to track progress.
   */
  ingestGSheet: (id: string, cfg: GSheetConnectorConfig) =>
    req<IngestStatus>(`/indexes/${id}/ingest/gsheet`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  /** Paginated listing of all documents in an index, sorted newest first. */
  listDocuments: (id: string, page = 1, perPage = 20) =>
    req<SearchResult>(`/indexes/${id}/documents?page=${page}&per_page=${perPage}`),

  /**
   * Partially update a document's editorial signals.
   * Only the supplied fields are changed — the vector and all other content are untouched.
   *
   * @param popularity       Search boost score (0 = neutral, higher = boosted).
   * @param isFeatured       Whether to pin this document as a featured result.
   * @param boostEqual       Queries that must exactly match to boost this document.
   * @param boostContains    Query substrings that trigger a softer boost.
   */
  updateDocument: (
    indexId: string,
    docId: string,
    popularity: number,
    isFeatured: boolean,
    boostEqual: string[],
    boostContains: string[],
  ) =>
    req<{ ok: boolean }>(`/indexes/${indexId}/documents/${docId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        popularity,
        is_featured: isFeatured,
        boost_queries_equal: boostEqual,
        boost_queries_contains: boostContains,
      }),
    }),

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
  rebuild: (id: string, cfg: RestConnectorConfig | SitemapConnectorConfig | GSheetConnectorConfig) =>
    req<IngestStatus>(`/indexes/${id}/rebuild`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  /** Persist sitemap connector config without triggering a crawl. */
  saveSitemapConnector: (id: string, cfg: SitemapConnectorConfig) =>
    req<IndexMeta>(`/indexes/${id}/connectors/sitemap`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  /** Persist Google Sheets connector config without triggering an ingest. */
  saveGsheetConnector: (id: string, cfg: GSheetConnectorConfig) =>
    req<IndexMeta>(`/indexes/${id}/connectors/gsheet`, {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  /**
   * Set (or clear) the per-index embedding model override.
   *
   * Pass an empty string to revert to the global default.
   * The index must be rebuilt after changing to a model with a different
   * vector dimension, or semantic search will return incorrect results.
   */
  setIndexEmbedModel: (id: string, modelId: string) =>
    req<IndexMeta>(`/indexes/${id}/embed-model`, {
      method: 'PUT',
      body: JSON.stringify({ model_id: modelId }),
    }),

  /** Enable or disable the version recency boost for an index. */
  setVersionBoost: (id: string, enabled: boolean) =>
    req<IndexMeta>(`/indexes/${id}/version-boost`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    }),

  /**
   * Set or clear the intent label for an index.
   *
   * The intent label scopes typeahead suggestions and drives tab intent detection.
   * No rebuild is required — the value is read at query time, not at ingest time.
   */
  saveIntentLabel: (id: string, intentLabel: string) =>
    req<IndexMeta>(`/indexes/${id}/intent-label`, {
      method: 'PUT',
      body: JSON.stringify({ intent_label: intentLabel }),
    }),

  /**
   * Save ``field_map`` and ``value_map`` onto the existing connector config.
   *
   * Both maps are persisted together in one call — the caller must include the
   * current values of whichever map it is *not* editing so they are not clobbered.
   * A rebuild is required for the changes to apply to already-indexed documents.
   */
  saveMapping: (id: string, cfg: MappingConfig) =>
    req<IndexMeta>(`/indexes/${id}/connectors/mapping`, {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),

  // ── Extensions library ─────────────────────────────────────────────────────

  /** Fetch all extensions from the global library, newest first. */
  listExtensions: () => req<ExtensionMeta[]>('/extensions'),

  /** Create a new JS extension in the global library. */
  createExtension: (name: string, description: string, code: string, ext_type: 'transform' | 'pre_processor' = 'transform') =>
    req<ExtensionMeta>('/extensions', {
      method: 'POST',
      body: JSON.stringify({ name, description, code, ext_type }),
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

  // ── Suggestions ───────────────────────────────────────────────────────────

  /**
   * Paginated list of autocomplete suggestions for an index.
   *
   * @param indexId  The index whose suggestions to fetch.
   * @param params   Optional filters: ``q`` (substring match), ``page``, ``per_page``.
   */
  listSuggestions: (indexId: string, params?: { q?: string; page?: number; per_page?: number }) => {
    // Build query string only from params that were explicitly supplied.
    const p = new URLSearchParams();
    if (params?.q !== undefined && params.q !== '') p.set('q', params.q);
    if (params?.page !== undefined) p.set('page', String(params.page));
    if (params?.per_page !== undefined) p.set('per_page', String(params.per_page));
    const qs = p.toString();
    return req<SuggestionsResponse>(`/indexes/${indexId}/suggestions${qs ? `?${qs}` : ''}`);
  },

  /**
   * Update the boost tier of an existing suggestion.
   * Pass 1 to promote, 0 to normalise, -1 to suppress.
   */
  updateSuggestionBoost: (indexId: string, suggestionId: string, boost: number) =>
    req<Suggestion>(`/indexes/${indexId}/suggestions/${suggestionId}`, {
      method: 'PUT',
      body: JSON.stringify({ boost }),
    }),

  /**
   * Update any combination of editable suggestion fields in one PUT.
   *
   * Used by the edit modal to persist mode, url, featured_doc_ids, and boost
   * in a single request.  Only the supplied keys are sent — the backend merges
   * them onto the stored document.
   */
  updateSuggestion: (
    indexId: string,
    suggestionId: string,
    fields: { boost?: number; mode?: string; url?: string; featured_doc_ids?: string[] },
  ) =>
    req<Suggestion>(`/indexes/${indexId}/suggestions/${suggestionId}`, {
      method: 'PUT',
      body: JSON.stringify(fields),
    }),

  /**
   * Create a new manual suggestion entry for an index.
   *
   * @param data.query     The autocomplete text shown to users.
   * @param data.boost     Initial tier: 1 = promoted, 0 = organic, -1 = suppressed.
   * @param data.category  Optional category the suggestion belongs to.
   */
  createSuggestion: (indexId: string, data: { query: string; boost: number; category?: string }) =>
    req<Suggestion>(`/indexes/${indexId}/suggestions`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  /**
   * Delete a suggestion permanently.
   * Uses raw ``fetch`` (not ``req``) because the backend returns 204 No Content.
   */
  deleteSuggestion: (indexId: string, suggestionId: string) =>
    fetch(`${BASE}/indexes/${indexId}/suggestions/${suggestionId}`, { method: 'DELETE' }),

  // ── Application settings ───────────────────────────────────────────────────

  /**
   * Fetch current application settings including the active embedding model
   * and the full catalogue of available models.
   */
  getSettings: () => req<AppSettings>('/settings'),

  /**
   * Save the RexBERT endpoint and optional API key without changing the global model.
   *
   * Use this when the global model is voyage/openai but one or more indexes use a
   * per-index RexBERT override.  The credentials must be stored so the embedding
   * service can reach the RexBERT server at ingest and search time.
   */
  saveRexbertConfig: (endpoint: string, apiKey?: string) =>
    req<AppSettings>('/settings/rexbert-config', {
      method: 'PUT',
      body: JSON.stringify({ endpoint, api_key: apiKey ?? null }),
    }),

  /**
   * Update the active embedding model and any associated provider config.
   *
   * The change is applied immediately at runtime — no restart required.
   *
   * @param modelId         One of the ``id`` values from ``AppSettings.available_models``.
   * @param openaiApiKey    Required the first time an OpenAI model is selected;
   *                        omit to retain the previously stored key.
   * @param customEndpoint  Full URL of the custom OpenAI-compatible endpoint
   *                        (required when modelId is ``"custom"``).
   * @param customApiKey    Bearer token for the custom endpoint (optional).
   * @param customModelName Model name sent in the custom endpoint request body
   *                        (required when modelId is ``"custom"``).
   * @param customNumDim    Vector dimension returned by the custom endpoint
   *                        (required when modelId is ``"custom"``).
   * @returns The updated settings plus ``dim_changed: true`` when the new model
   *          produces vectors of a different dimension, signalling that all
   *          indexes must be rebuilt before semantic search will work.
   */
  updateSettings: (
    modelId: string,
    opts?: {
      openaiApiKey?: string;
      rexbertEndpoint?: string;
      rexbertApiKey?: string;
      customEndpoint?: string;
      customApiKey?: string;
      customModelName?: string;
      customNumDim?: number;
    }
  ) =>
    req<AppSettings>('/settings', {
      method: 'PUT',
      body: JSON.stringify({
        model_id:          modelId,
        openai_api_key:    opts?.openaiApiKey    ?? null,
        rexbert_endpoint:  opts?.rexbertEndpoint ?? null,
        rexbert_api_key:   opts?.rexbertApiKey   ?? null,
        custom_endpoint:   opts?.customEndpoint  ?? null,
        custom_api_key:    opts?.customApiKey    ?? null,
        custom_model_name: opts?.customModelName ?? null,
        custom_num_dim:    opts?.customNumDim    ?? null,
      }),
    }),

  /**
   * Update one or more customer-context personalisation flags.
   * Only the fields you supply are changed — partial updates are safe.
   */
  updateCustomerContext: (flags: {
    enabled?: boolean;
    vectorBlendingEnabled?: boolean;
    affinityBoostEnabled?: boolean;
    viewedPenaltyEnabled?: boolean;
  }) =>
    req<AppSettings>('/settings/customer-context', {
      method: 'PUT',
      body: JSON.stringify({
        enabled:                 flags.enabled,
        vector_blending_enabled: flags.vectorBlendingEnabled,
        affinity_boost_enabled:  flags.affinityBoostEnabled,
        viewed_penalty_enabled:  flags.viewedPenaltyEnabled,
      }),
    }),

  getChatConfig: () =>
    req<import('./types/settings.types').ChatConfig>('/settings/chat'),

  updateChatConfig: (opts: {
    provider:  string;
    model:     string;
    apiKey?:   string;
    endpoint?: string;
  }) =>
    req<import('./types/settings.types').ChatConfig>('/settings/chat', {
      method: 'PUT',
      body: JSON.stringify({
        provider: opts.provider,
        model:    opts.model,
        api_key:  opts.apiKey  ?? null,
        endpoint: opts.endpoint ?? null,
      }),
    }),

  getAffinityDimensions: (indexId: string) =>
    req<{ dimensions: import('./types').AffinityDimension[]; viewed_item_policy: import('./types').ViewedItemPolicy }>(`/indexes/${indexId}/affinity-dimensions`),

  updateAffinityDimensions: (
    indexId: string,
    dimensions: import('./types').AffinityDimension[],
    viewed_item_policy: import('./types').ViewedItemPolicy,
  ) =>
    req<{ dimensions: import('./types').AffinityDimension[]; viewed_item_policy: import('./types').ViewedItemPolicy }>(`/indexes/${indexId}/affinity-dimensions`, {
      method: 'PUT',
      body: JSON.stringify({ dimensions, viewed_item_policy }),
    }),

  rediscoverAffinityDimensions: (indexId: string) =>
    req<{ dimensions: import('./types').AffinityDimension[]; viewed_item_policy: import('./types').ViewedItemPolicy }>(`/indexes/${indexId}/affinity-dimensions/discover`, {
      method: 'POST',
    }),

  getBoostRules: (indexId: string) =>
    req<{ rules: import('./types').BoostRule[] }>(`/indexes/${indexId}/boost-rules`),

  updateBoostRules: (indexId: string, rules: import('./types').BoostRule[]) =>
    req<{ rules: import('./types').BoostRule[] }>(`/indexes/${indexId}/boost-rules`, {
      method: 'PUT',
      body: JSON.stringify({ rules }),
    }),

  getBoostRuleFields: (indexId: string) =>
    req<{ fields: string[] }>(`/indexes/${indexId}/boost-rules/fields`),

  getFields: (indexId: string) =>
    req<{ fields: FieldEntry[] }>(`/indexes/${indexId}/fields`),

  updateField: (indexId: string, fieldName: string, patch: Partial<FieldConfig>) =>
    req<FieldEntry>(`/indexes/${indexId}/fields/${encodeURIComponent(fieldName)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
};
