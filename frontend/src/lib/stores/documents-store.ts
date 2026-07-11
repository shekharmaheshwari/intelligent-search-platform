import { StoreBase } from './StoreBase';
import { indexService } from '../services/indexes/IndexService';
import type { DocumentItem } from '../types';

interface DocumentsState {
  docs:    DocumentItem[];
  found:   number;
  page:    number;
  loading: boolean;
  error:   string;
}

class DocumentsStore extends StoreBase<DocumentsState> {
  constructor() {
    super({ docs: [], found: 0, page: 1, loading: false, error: '' });
  }

  async load(indexId: string, opts: { page?: number; perPage?: number } = {}): Promise<void> {
    const page    = opts.page    ?? 1;
    const perPage = opts.perPage ?? 20;
    this.update(s => ({ ...s, loading: true, error: '' }));
    try {
      const res = await indexService.listDocuments(indexId, page, perPage);
      this.update(s => ({
        ...s,
        docs:    res.hits.map(h => h.document),
        found:   res.found,
        page,
        loading: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  async search(
    indexId: string,
    q: string,
    opts: { mode?: string; page?: number; perPage?: number } = {},
  ): Promise<void> {
    const mode    = opts.mode    ?? 'hybrid';
    const page    = opts.page    ?? 1;
    const perPage = opts.perPage ?? 10;
    this.update(s => ({ ...s, loading: true, error: '' }));
    try {
      const res = await indexService.search(indexId, q, mode, page, perPage);
      this.update(s => ({
        ...s,
        docs:    res.hits.map(h => h.document),
        found:   res.found,
        page,
        loading: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  async updateDocument(
    indexId: string,
    docId:   string,
    popularity:    number,
    isFeatured:    boolean,
    boostEqual:    string[],
    boostContains: string[],
  ): Promise<{ ok: boolean }> {
    return indexService.updateDocument(indexId, docId, popularity, isFeatured, boostEqual, boostContains);
  }

  reset(): void {
    this.set({ docs: [], found: 0, page: 1, loading: false, error: '' });
  }
}

export const documentsStore = new DocumentsStore();
