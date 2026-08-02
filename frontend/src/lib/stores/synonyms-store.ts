import { StoreBase } from './StoreBase';
import { api } from '../api';
import type { Synonym } from '../types';

interface SynonymsState {
  synonyms: Synonym[];
  loading:  boolean;
  saving:   boolean;
  error:    string;
}

class SynonymsStore extends StoreBase<SynonymsState> {
  constructor() {
    super({ synonyms: [], loading: false, saving: false, error: '' });
  }

  async load(indexId: string): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: '' }));
    try {
      const res = await api.getSynonyms(indexId);
      this.update(s => ({ ...s, synonyms: res.synonyms, loading: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  async upsert(indexId: string, synonymId: string, synonyms: string[], root?: string): Promise<void> {
    this.update(s => ({ ...s, saving: true, error: '' }));
    try {
      const saved = await api.upsertSynonym(indexId, synonymId, synonyms, root);
      this.update(s => ({
        ...s,
        synonyms: [...s.synonyms.filter(x => x.id !== saved.id), saved],
        saving: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), saving: false }));
    }
  }

  async remove(indexId: string, synonymId: string): Promise<void> {
    this.update(s => ({ ...s, saving: true, error: '' }));
    try {
      await api.deleteSynonym(indexId, synonymId);
      this.update(s => ({
        ...s,
        synonyms: s.synonyms.filter(x => x.id !== synonymId),
        saving: false,
      }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), saving: false }));
    }
  }

  reset(): void {
    this.set({ synonyms: [], loading: false, saving: false, error: '' });
  }
}

export const synonymsStore = new SynonymsStore();
