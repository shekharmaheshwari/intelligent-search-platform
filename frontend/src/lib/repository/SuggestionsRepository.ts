import { api } from '../api';
import type { Suggestion, SuggestionsResponse } from '../types';

/**
 * Repository for all suggestions-related HTTP calls.
 *
 * Each method delegates directly to the ``api`` client and returns the raw DTO
 * unchanged.  No mapping, no business logic, no store access.
 */
export class SuggestionsRepository {
  /**
   * Fetch a paginated, optionally filtered list of suggestions for an index.
   *
   * @param indexId  The index to query.
   * @param params   Optional ``q`` substring filter, ``page``, and ``per_page``.
   */
  listSuggestions(
    indexId: string,
    params?: { q?: string; page?: number; per_page?: number },
  ): Promise<SuggestionsResponse> {
    return api.listSuggestions(indexId, params);
  }

  /**
   * Update the boost value of a single suggestion.
   * Callers pass 1, 0, or -1 to promote, normalise, or suppress respectively.
   */
  updateSuggestionBoost(indexId: string, suggestionId: string, boost: number): Promise<Suggestion> {
    return api.updateSuggestionBoost(indexId, suggestionId, boost);
  }

  /**
   * Create a new manual suggestion entry for an index.
   *
   * @param data.query     Text shown in the autocomplete dropdown.
   * @param data.boost     Initial tier: 1 = promoted, 0 = organic, -1 = suppressed.
   * @param data.category  Optional category string.
   */
  createSuggestion(
    indexId: string,
    data: { query: string; boost: number; category?: string },
  ): Promise<Suggestion> {
    return api.createSuggestion(indexId, data);
  }

  /**
   * Permanently delete a suggestion.
   * Converts the backend's 204 No Content response to ``void``.
   */
  async deleteSuggestion(indexId: string, suggestionId: string): Promise<void> {
    await api.deleteSuggestion(indexId, suggestionId);
  }
}

export const suggestionsRepository = new SuggestionsRepository();
