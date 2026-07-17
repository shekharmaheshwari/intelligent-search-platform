import { indexRepository, type IndexRepository } from '../../repository/IndexRepository';
import { mapIndexMetaToIndex, mapIndexesToDomain } from './index.mapper';
import type { Index, IngestStatus, SearchResult } from './index.model';
import type { IndexMeta, RestConnectorConfig, SitemapConnectorConfig, GSheetConnectorConfig, MappingConfig, FieldEntry, FieldConfig } from '../../types';

/**
 * Service for all index-domain operations.
 *
 * Calls the repository, applies the mapper, and returns domain models.
 * No direct HTTP — all network I/O goes through ``IndexRepository``.
 */
export class IndexService {
  constructor(private readonly repo: IndexRepository) {}

  /** Load all ISP indexes as domain models. */
  async listIndexes(): Promise<Index[]> {
    const dtos = await this.repo.listIndexes();
    return mapIndexesToDomain(dtos);
  }

  /** Trigger auto-discovery of external Typesense collections and return the newly found ones. */
  async discoverIndexes(): Promise<Index[]> {
    const dtos = await this.repo.discoverIndexes();
    return mapIndexesToDomain(dtos);
  }

  /**
   * Create a new ISP index and return it as a domain model.
   *
   * @param name        Display name.
   * @param description Optional description.
   * @param opts        Extra fields for catalog write-back (external) sources.
   */
  async createIndex(
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
  ): Promise<Index> {
    const dto = await this.repo.createIndex(name, description, opts);
    return mapIndexMetaToIndex(dto);
  }

  /** Fetch a single index by id as a domain model. */
  async getIndex(id: string): Promise<Index> {
    const dto = await this.repo.getIndex(id);
    return mapIndexMetaToIndex(dto);
  }

  /**
   * Delete an index and all its Typesense documents.
   * Resolves when the delete is confirmed; throws on HTTP error.
   */
  async deleteIndex(id: string): Promise<void> {
    await this.repo.deleteIndex(id);
  }

  /** Poll the current ingest/rebuild job state. Returns the raw ``IngestStatus`` DTO (no mapping needed). */
  getStatus(id: string): Promise<IngestStatus> {
    return this.repo.getStatus(id);
  }

  /**
   * Start a REST ingest job. Returns immediately with ``state=queued``.
   * Poll ``getStatus`` to track progress.
   */
  ingestRest(id: string, cfg: RestConnectorConfig): Promise<IngestStatus> {
    return this.repo.ingestRest(id, cfg);
  }

  /**
   * Start a sitemap crawl job. Returns immediately with ``state=queued``.
   * Poll ``getStatus`` to track progress.
   */
  ingestSitemap(id: string, cfg: SitemapConnectorConfig): Promise<IngestStatus> {
    return this.repo.ingestSitemap(id, cfg);
  }

  /**
   * Start a Google Sheets ingest job. Returns immediately with ``state=queued``.
   * Poll ``getStatus`` to track progress.
   */
  ingestGSheet(id: string, cfg: GSheetConnectorConfig): Promise<IngestStatus> {
    return this.repo.ingestGSheet(id, cfg);
  }

  /** Return a paginated page of documents from the index. */
  listDocuments(id: string, page?: number, perPage?: number): Promise<SearchResult> {
    return this.repo.listDocuments(id, page, perPage);
  }

  /**
   * Partially update a document's editorial signals.
   *
   * @param popularity      Boost score (0 = neutral).
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
    return this.repo.updateDocument(indexId, docId, popularity, isFeatured, boostEqual, boostContains);
  }

  /**
   * Search documents in an index.
   *
   * @param mode  ``hybrid`` | ``lexical`` | ``semantic``
   */
  search(id: string, q: string, mode: string, page?: number, perPage?: number): Promise<SearchResult> {
    return this.repo.search(id, q, mode, page, perPage);
  }

  /**
   * Full rebuild: drop all existing documents and re-fetch from the connector.
   * Returns immediately with ``state=queued``; poll ``getStatus`` to track progress.
   */
  rebuild(id: string, cfg: RestConnectorConfig | SitemapConnectorConfig | GSheetConnectorConfig): Promise<IngestStatus> {
    return this.repo.rebuild(id, cfg);
  }

  /** Persist sitemap connector config without triggering a crawl. */
  saveSitemapConnector(id: string, cfg: SitemapConnectorConfig): Promise<IndexMeta> {
    return this.repo.saveSitemapConnector(id, cfg);
  }

  /** Persist Google Sheets connector config without triggering an ingest. */
  saveGsheetConnector(id: string, cfg: GSheetConnectorConfig): Promise<IndexMeta> {
    return this.repo.saveGsheetConnector(id, cfg);
  }

  /**
   * Set (or clear) the per-index embedding model override and return the updated domain model.
   *
   * @param modelId  Model id string (e.g. ``"voyage-3"``).
   *                 Pass ``""`` to revert to the global default.
   */
  async setIndexEmbedModel(id: string, modelId: string): Promise<Index> {
    const dto = await this.repo.setIndexEmbedModel(id, modelId);
    return mapIndexMetaToIndex(dto);
  }

  /** Enable or disable the version recency boost for an index. */
  async setVersionBoost(id: string, enabled: boolean): Promise<Index> {
    const dto = await this.repo.setVersionBoost(id, enabled);
    return mapIndexMetaToIndex(dto);
  }

  /**
   * Set or clear the intent label for an index and return the updated domain model.
   *
   * No rebuild is required — the label is consumed at query time and suggestion-seeding
   * time, not at ingest time.
   */
  async saveIntentLabel(indexId: string, intentLabel: string): Promise<Index> {
    const dto = await this.repo.saveIntentLabel(indexId, intentLabel);
    return mapIndexMetaToIndex(dto);
  }

  /**
   * Save field renames (``field_map``) and value overrides (``value_map``) and return
   * the updated domain model.
   *
   * Both maps are sent in one request.  The caller must supply the full current state
   * of both maps so neither is clobbered when only one section is being edited.
   * A rebuild is required for changes to apply to already-indexed documents.
   */
  async saveMapping(indexId: string, cfg: MappingConfig): Promise<Index> {
    const dto = await this.repo.saveMapping(indexId, cfg);
    return mapIndexMetaToIndex(dto);
  }

  getFields(indexId: string): Promise<{ fields: FieldEntry[] }> {
    return this.repo.getFields(indexId);
  }

  updateField(indexId: string, fieldName: string, patch: Partial<FieldConfig>): Promise<FieldEntry> {
    return this.repo.updateField(indexId, fieldName, patch);
  }
}

export const indexService = new IndexService(indexRepository);
