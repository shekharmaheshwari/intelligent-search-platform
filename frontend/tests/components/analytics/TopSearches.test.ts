// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import TopSearches from '../../../src/components/analytics/TopSearches.svelte';
import type { TopQuery } from '../../../src/lib/types';

const queries: TopQuery[] = [
  { query: 'iphone case',  count: 500, avg_results: 12.0 },
  { query: 'samsung buds', count: 200, avg_results: 2.5  },
  { query: 'airpods',      count: 100, avg_results: 0.0  },
];

describe('TopSearches', () => {
  it('shows empty state when no queries', () => {
    const { getByText } = render(TopSearches, { queries: [] });
    expect(getByText('No search data yet')).toBeTruthy();
  });

  it('shows query count in header', () => {
    const { getByText } = render(TopSearches, { queries });
    expect(getByText('3 queries')).toBeTruthy();
  });

  it('renders each query in the table', () => {
    const { getByText } = render(TopSearches, { queries });
    expect(getByText('iphone case')).toBeTruthy();
    expect(getByText('samsung buds')).toBeTruthy();
    expect(getByText('airpods')).toBeTruthy();
  });

  it('renders rank numbers starting at 1', () => {
    const { getByText } = render(TopSearches, { queries });
    expect(getByText('1')).toBeTruthy();
    expect(getByText('2')).toBeTruthy();
    expect(getByText('3')).toBeTruthy();
  });

  it('formats count with locale string', () => {
    const { getByText } = render(TopSearches, { queries });
    expect(getByText('500')).toBeTruthy();
  });

  it('formats avg_results to one decimal place', () => {
    const { getByText } = render(TopSearches, { queries });
    expect(getByText('12.0')).toBeTruthy();
    expect(getByText('2.5')).toBeTruthy();
  });

  it('applies low class to avg_results when < 3', () => {
    const { container } = render(TopSearches, { queries });
    const cells = container.querySelectorAll('td.num.low');
    expect(cells.length).toBeGreaterThan(0);
  });

  it('does not apply low class to avg_results when >= 3', () => {
    const { container } = render(TopSearches, { queries: [{ query: 'test', count: 10, avg_results: 5 }] });
    const lowCells = container.querySelectorAll('td.num.low');
    expect(lowCells.length).toBe(0);
  });

  it('shows 0 queries in header when empty', () => {
    const { getByText } = render(TopSearches, { queries: [] });
    expect(getByText('0 queries')).toBeTruthy();
  });
});
