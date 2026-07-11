import { suggestionsRepository } from '../../repository/SuggestionsRepository';
import type { Suggestion, SuggestionsResponse } from '../../types';

/**
 * Service for suggestions orchestration.
 *
 * Suggestions DTOs are already UI-friendly (no snake_case → camelCase transformation
 * required), so this service is a thin delegation layer over the repository.
 * It exists to satisfy the architecture contract that components never call
 * repositories directly.
 */
class SuggestionsService {
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
    return suggestionsRepository.listSuggestions(indexId, params);
  }

  /**
   * Set the boost tier on an existing suggestion.
   * Pass 1 to promote, 0 to normalise, -1 to suppress.
   */
  updateSuggestionBoost(indexId: string, suggestionId: string, boost: number): Promise<Suggestion> {
    return suggestionsRepository.updateSuggestionBoost(indexId, suggestionId, boost);
  }

  /**
   * Persist any combination of editable suggestion fields in one call.
   *
   * Supply only the fields you want to change; the backend merges them onto
   * the stored document.  ``url`` is only meaningful when ``mode`` is "link";
   * ``featured_doc_ids`` is only meaningful when ``mode`` is "search".
   */
  updateSuggestion(
    indexId: string,
    suggestionId: string,
    fields: { boost?: number; mode?: string; url?: string; featured_doc_ids?: string[] },
  ): Promise<Suggestion> {
    return suggestionsRepository.updateSuggestion(indexId, suggestionId, fields);
  }

  /**
   * Create a new manual suggestion entry for an index.
   *
   * @param data.query     Text shown in the autocomplete dropdown.
   * @param data.boost     Initial tier: 1 = promoted, 0 = organic, -1 = suppressed.
   * @param data.category  Optional category string (pre-filled from index intent label).
   */
  createSuggestion(
    indexId: string,
    data: { query: string; boost: number; category?: string },
  ): Promise<Suggestion> {
    return suggestionsRepository.createSuggestion(indexId, data);
  }

  /** Permanently delete a suggestion by id. */
  deleteSuggestion(indexId: string, suggestionId: string): Promise<void> {
    return suggestionsRepository.deleteSuggestion(indexId, suggestionId);
  }
}

export const suggestionsService = new SuggestionsService();
