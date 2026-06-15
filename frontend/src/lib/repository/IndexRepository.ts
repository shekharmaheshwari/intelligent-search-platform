import type {
  IndexMeta, IngestStatus, SearchResult,
  RestConnectorConfig, SitemapConnectorConfig,
} from '../types';
import { api } from '../api';

/**
 * Repository for all index-related HTTP calls.
 *
 * Each method delegates directly to the ``api`` client and returns the raw DTO
 * unchanged.  No mapping, no business logic, no store access.
 */
export class IndexRepository {
  /** Fetch the full list of ISP indexes. */
  listIndexes(): Promise<IndexMeta[]> {
    return api.listIndexes();
  }

  /** Trigger auto-discovery of external Typesense collections not yet tracked by ISP. */
  discoverIndexes(): Promise<IndexMeta[]> {
    return api.discoverIndexes();
  }

  /**
   * Create a new ISP index.
   *
   * @param name         Display name for the index.
   * @param description  Optional human-readable description.
   * @param opts         Extra fields for catalog write-back / external sources.
   */
  createIndex(
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
  ): Promise<IndexMeta> {
    return api.createIndex(name, description, opts);
  }

  /** Fetch a single index by id. */
  getIndex(id: string): Promise<IndexMeta> {
    return api.getIndex(id);
  }

  /**
   * Delete an index and all its Typesense documents.
   * Returns the raw ``Response`` because the backend returns 204 No Content.
   */
  deleteIndex(id: string): Promise<Response> {
    return api.deleteIndex(id);
  }

  /** Poll the current ingest/rebuild job state for an index. */
  getStatus(id: string): Promise<IngestStatus> {
    return api.getStatus(id);
  }

  /**
   * Start a REST ingest job that appends documents to the index.
   * Returns immediately with ``state=queued``; callers must poll ``getStatus``.
   */
  ingestRest(id: string, cfg: RestConnectorConfig): Promise<IngestStatus> {
    return api.ingestRest(id, cfg);
  }

  /**
   * Start a sitemap crawl job that appends pages to the index.
   * Returns immediately with ``state=queued``; callers must poll ``getStatus``.
   */
  ingestSitemap(id: string, cfg: SitemapConnectorConfig): Promise<IngestStatus> {
    return api.ingestSitemap(id, cfg);
  }

  /** Paginated listing of all documents in an index, sorted newest first. */
  listDocuments(id: string, page?: number, perPage?: number): Promise<SearchResult> {
    return api.listDocuments(id, page, perPage);
  }

  /**
   * Partially update a document's editorial signals.
   *
   * @param popularity      Search boost score (0 = neutral, higher = boosted).
   * @param isFeatured      Whether to pin this document as a featured result.
   * @param boostEqual      Queries that must exactly match to boost this document.
   * @param boostContains   Query substrings that trigger a softer boost.
   */
  updateDocument(
    indexId: string,
    docId: string,
    popularity: number,
    isFeatured: boolean,
    boostEqual: string[],
    boostContains: string[],
  ): Promise<{ ok: boolean }> {
    return api.updateDocument(indexId, docId, popularity, isFeatured, boostEqual, boostContains);
  }

  /**
   * Search documents in an index.
   *
   * @param mode  ``hybrid`` (default) | ``lexical`` | ``semantic``
   */
  search(id: string, q: string, mode: string, page?: number, perPage?: number): Promise<SearchResult> {
    return api.search(id, q, mode, page, perPage);
  }

  /**
   * Full rebuild: drop all existing documents and re-fetch from the connector.
   * Callers must poll ``getStatus`` to track progress.
   */
  rebuild(id: string, cfg: RestConnectorConfig): Promise<IngestStatus> {
    return api.rebuild(id, cfg);
  }

  /**
   * Set (or clear) the per-index embedding model override.
   *
   * @param modelId  Model id string (e.g. ``"voyage-3"``).
   *                 Pass ``""`` to revert to the global default.
   */
  setIndexEmbedModel(id: string, modelId: string): Promise<IndexMeta> {
    return api.setIndexEmbedModel(id, modelId);
  }

  /** Enable or disable the version recency boost for an index. */
  setVersionBoost(id: string, enabled: boolean): Promise<IndexMeta> {
    return api.setVersionBoost(id, enabled);
  }
}

export const indexRepository = new IndexRepository();
