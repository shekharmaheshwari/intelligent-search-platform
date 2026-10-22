// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import {
  applyEditToDoc,
  getIsFeatured,
  getPopularity,
  getBoostQueries,
} from '../../../src/components/sources/SourceContent.helpers';
import type { DocumentItem } from '../../../src/lib/types';

function doc(overrides: Partial<DocumentItem> = {}): DocumentItem {
  return {
    id: 'd1',
    title: 'Galaxy A57 5G',
    content: '',
    url: '',
    source_type: 'device',
    indexed_at: 0,
    is_featured: false,
    popularity: 0,
    metadata: JSON.stringify({ price: 499 }),
    ...overrides,
  } as DocumentItem;
}

describe('applyEditToDoc', () => {
  it('re-opening the edit modal reflects a just-saved Featured toggle', () => {
    // Regression: the optimistic update only wrote metadata.is_featured, but
    // getIsFeatured reads the top-level field first — so the toggle showed OFF
    // on the next edit even though the save succeeded.
    const saved = applyEditToDoc(doc({ is_featured: false }), {
      popularity: 0,
      isFeatured: true,
      boostEqual: [],
      boostContains: [],
    });
    expect(getIsFeatured(saved)).toBe(true);
  });

  it('re-opening the edit modal reflects a just-saved Score', () => {
    const saved = applyEditToDoc(doc({ popularity: 0 }), {
      popularity: 750,
      isFeatured: false,
      boostEqual: [],
      boostContains: [],
    });
    expect(getPopularity(saved)).toBe(750);
  });

  it('writes both the top-level field and the metadata blob', () => {
    const saved = applyEditToDoc(doc(), {
      popularity: 42,
      isFeatured: true,
      boostEqual: [],
      boostContains: [],
    });
    expect(saved.is_featured).toBe(true);
    expect(saved.popularity).toBe(42);
    expect(JSON.parse(saved.metadata as string)).toMatchObject({
      is_featured: true,
      popularity: 42,
      price: 499, // existing metadata preserved
    });
  });

  it('persists boost-query tags and returns fresh array copies', () => {
    const eq = ['iphone 16'];
    const ct = ['apple'];
    const saved = applyEditToDoc(doc(), {
      popularity: 0,
      isFeatured: false,
      boostEqual: eq,
      boostContains: ct,
    });
    expect(getBoostQueries(saved, 'boost_queries_equal')).toEqual(['iphone 16']);
    expect(getBoostQueries(saved, 'boost_queries_contains')).toEqual(['apple']);
    expect(saved.boost_queries_equal).not.toBe(eq); // copied, not aliased
  });

  it('turning Featured back off is reflected on re-open', () => {
    const on = applyEditToDoc(doc({ is_featured: true }), {
      popularity: 0, isFeatured: true, boostEqual: [], boostContains: [],
    });
    const off = applyEditToDoc(on, {
      popularity: 0, isFeatured: false, boostEqual: [], boostContains: [],
    });
    expect(getIsFeatured(off)).toBe(false);
  });

  it('tolerates a document with no metadata blob', () => {
    const saved = applyEditToDoc(doc({ metadata: undefined }), {
      popularity: 5, isFeatured: true, boostEqual: [], boostContains: [],
    });
    expect(getIsFeatured(saved)).toBe(true);
    expect(getPopularity(saved)).toBe(5);
  });
});
