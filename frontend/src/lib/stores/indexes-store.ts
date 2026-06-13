import { StoreBase } from './StoreBase';
import { indexService } from '../services/indexes/IndexService';
import type { IndexMeta } from '../types';

interface IndexesState {
  /** Full list of ISP indexes kept in sync with the backend. */
  indexes: IndexMeta[];
  /** True while the initial or refresh fetch is in flight. */
  loading: boolean;
  /** Last error message; ``null`` when the last operation succeeded. */
  error: string | null;
}

/**
 * Global store for the ISP index list.
 *
 * All components that need the list of indexes subscribe to this store.
 * Mutations (load, delete, upsert) go through action methods so the state
 * is always replaced with new references — never mutated in place.
 */
class IndexesStore extends StoreBase<IndexesState> {
  constructor() {
    super({ indexes: [], loading: false, error: null });
  }

  /**
   * Fetch the full index list from the backend and replace the store state.
   * Sets ``loading`` while the request is in flight and clears it on completion.
   */
  async load(): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: null }));
    try {
      const domain = await indexService.listIndexes();
      const dtos = domain.map(d => ({
        id: d.id,
        name: d.name,
        description: d.description,
        created_at: d.createdAt,
        collection_name: d.collectionName,
        connectors: d.connectors,
        doc_count: d.docCount,
        is_external: d.isExternal,
        external_collection: d.externalCollection,
        external_filter: d.externalFilter,
        external_query_by: d.externalQueryBy,
        external_title_field: d.externalTitleField,
        external_url_field: d.externalUrlField,
      } as IndexMeta));
      this.update(s => ({ ...s, indexes: dtos, loading: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  /**
   * Trigger auto-discovery of external Typesense collections not yet tracked by ISP.
   * Returns the raw ``IndexMeta`` list so callers can decide whether to reload.
   */
  async discover(): Promise<IndexMeta[]> {
    const domain = await indexService.discoverIndexes();
    return domain.map(d => ({
      id: d.id,
      name: d.name,
      description: d.description,
      created_at: d.createdAt,
      collection_name: d.collectionName,
      connectors: d.connectors,
      doc_count: d.docCount,
      is_external: d.isExternal,
      external_collection: d.externalCollection,
      external_filter: d.externalFilter,
      external_query_by: d.externalQueryBy,
      external_title_field: d.externalTitleField,
      external_url_field: d.externalUrlField,
    } as IndexMeta));
  }

  /**
   * Delete an index and optimistically remove it from the store.
   * Throws on HTTP error so callers can surface the message.
   */
  async deleteIndex(id: string): Promise<void> {
    await indexService.deleteIndex(id);
    this.update(s => ({ ...s, indexes: s.indexes.filter(i => i.id !== id) }));
  }

  /**
   * Insert or update a single index in the list without a full reload.
   * Used to reflect changes returned by create/update API calls.
   */
  upsertIndex(index: IndexMeta): void {
    this.update(s => {
      const exists = s.indexes.some(i => i.id === index.id);
      return {
        ...s,
        indexes: exists
          ? s.indexes.map(i => (i.id === index.id ? { ...i, ...index } : i))
          : [...s.indexes, index],
      };
    });
  }
}

export const indexesStore = new IndexesStore();
