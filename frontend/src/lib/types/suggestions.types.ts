/**
 * DTO types for the suggestions feature.
 *
 * These mirror the backend ``Suggestion`` and ``SuggestionsListResponse`` Pydantic models.
 * The ``boost`` field controls tier placement: 1 = promoted, 0 = organic, -1 = suppressed.
 */

/**
 * A single autocomplete suggestion entry stored in the suggestions sub-collection.
 *
 * ``boost`` is the editorial signal that determines display tier:
 *   1  → PROMOTED (appears first)
 *   0  → ORGANIC  (natural ranking)
 *  -1  → SUPPRESSED (hidden from users)
 *
 * ``mode`` controls what happens when the user selects the suggestion:
 *   "search" (default) → run the query through ISP search
 *   "link"             → navigate directly to ``url``
 *
 * ``url`` is required when mode is "link".
 * ``featured_doc_ids`` pins one or more documents to the top positions when mode is "search".
 */
export interface Suggestion {
  id: string;
  query: string;
  popularity: number;
  boost: number;
  type: 'query' | 'catalog';
  category?: string;
  boosted?: boolean;
  mode?: 'link' | 'search';
  url?: string;
  featured_doc_ids?: string[];
}

/**
 * Paginated response from GET /v1/indexes/{id}/suggestions.
 * ``total`` is the unfiltered count across all pages.
 */
export interface SuggestionsResponse {
  suggestions: Suggestion[];
  total: number;
  page: number;
  per_page: number;
}
