// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { StoreBase } from './StoreBase';
import { suggestionsService } from '../services/suggestions/SuggestionsService';
import type { Suggestion } from '../types';

interface SuggestionsState {
  suggestions: Suggestion[];
  total:       number;
  loading:     boolean;
  error:       string;
}

class SuggestionsStore extends StoreBase<SuggestionsState> {
  constructor() {
    super({ suggestions: [], total: 0, loading: false, error: '' });
  }

  async load(
    indexId: string,
    opts: { q?: string; page?: number; per_page?: number } = {},
  ): Promise<void> {
    this.update(s => ({ ...s, loading: true, error: '' }));
    try {
      const res = await suggestionsService.listSuggestions(indexId, opts);
      this.update(s => ({ ...s, suggestions: res.suggestions, total: res.total, loading: false }));
    } catch (e) {
      this.update(s => ({ ...s, error: String(e), loading: false }));
    }
  }

  async create(
    indexId: string,
    data: { query: string; boost: number; category?: string },
  ): Promise<void> {
    await suggestionsService.createSuggestion(indexId, data);
  }

  async updateSuggestion(
    indexId: string,
    id: string,
    fields: { boost?: number; mode?: string; url?: string; featured_doc_ids?: string[] },
  ): Promise<Suggestion> {
    return suggestionsService.updateSuggestion(indexId, id, fields);
  }

  async delete(indexId: string, id: string): Promise<void> {
    await suggestionsService.deleteSuggestion(indexId, id);
  }

  reset(): void {
    this.set({ suggestions: [], total: 0, loading: false, error: '' });
  }
}

export const suggestionsStore = new SuggestionsStore();
