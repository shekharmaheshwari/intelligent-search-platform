// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import type { Suggestion } from '../../lib/types';

export function filterByBoostTier(suggestions: Suggestion[], boost: -1 | 0 | 1): Suggestion[] {
  return suggestions.filter(s => s.boost === boost);
}

export function canPrev(page: number): boolean {
  return page > 1;
}

export function canNext(page: number, perPage: number, total: number): boolean {
  return page * perPage < total;
}

export function buildSavePayload(
  boost:          number,
  mode:           'link' | 'search',
  editUrl:        string,
  featuredDocIds: string[],
): { boost: number; mode: 'link' | 'search'; url?: string; featured_doc_ids?: string[] } {
  return {
    boost,
    mode,
    url:              mode === 'link'   ? editUrl.trim() || undefined : undefined,
    featured_doc_ids: mode === 'search' ? featuredDocIds              : undefined,
  };
}

export function addFeaturedId(ids: string[], id: string): string[] {
  const trimmed = id.trim();
  return trimmed && !ids.includes(trimmed) ? [...ids, trimmed] : ids;
}

export function removeFeaturedId(
  ids:    string[],
  titles: Record<string, string>,
  id:     string,
): { ids: string[]; titles: Record<string, string> } {
  const { [id]: _, ...rest } = titles;
  return { ids: ids.filter(d => d !== id), titles: rest };
}
