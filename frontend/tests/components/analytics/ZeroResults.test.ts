// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import ZeroResults from '../../../src/components/analytics/ZeroResults.svelte';
import type { ZeroResultQuery } from '../../../src/lib/types';

const queries: ZeroResultQuery[] = [
  { query: 'samsung s30',      count: 120 },
  { query: 'iphone 20 pro',    count: 80  },
];

describe('ZeroResults', () => {
  it('shows success message when no zero-result queries', () => {
    const { getByText } = render(ZeroResults, { queries: [] });
    expect(getByText('All searches returned results ✓')).toBeTruthy();
  });

  it('shows badge with query count when queries exist', () => {
    const { getByText } = render(ZeroResults, { queries });
    expect(getByText('2 queries returning nothing')).toBeTruthy();
  });

  it('renders each query in the table', () => {
    const { getByText } = render(ZeroResults, { queries });
    expect(getByText('samsung s30')).toBeTruthy();
    expect(getByText('iphone 20 pro')).toBeTruthy();
  });

  it('renders rank numbers starting at 1', () => {
    const { getByText } = render(ZeroResults, { queries });
    expect(getByText('1')).toBeTruthy();
    expect(getByText('2')).toBeTruthy();
  });

  it('formats count with locale string', () => {
    const { getByText } = render(ZeroResults, { queries });
    expect(getByText('120')).toBeTruthy();
    expect(getByText('80')).toBeTruthy();
  });

  it('shows hint text when queries exist', () => {
    const { getByText } = render(ZeroResults, { queries });
    expect(getByText('Add synonyms or new content to fix these gaps.')).toBeTruthy();
  });

  it('does not show hint when no queries', () => {
    const { queryByText } = render(ZeroResults, { queries: [] });
    expect(queryByText('Add synonyms or new content to fix these gaps.')).toBeNull();
  });

  it('does not show badge when no queries', () => {
    const { queryByText } = render(ZeroResults, { queries: [] });
    expect(queryByText(/queries returning nothing/)).toBeNull();
  });
});
