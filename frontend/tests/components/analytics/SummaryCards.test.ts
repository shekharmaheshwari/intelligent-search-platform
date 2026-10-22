// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import SummaryCards from '../../../src/components/analytics/SummaryCards.svelte';
import type { AnalyticsSummary } from '../../../src/lib/types';

const base: AnalyticsSummary = {
  total_searches:   1000,
  unique_queries:   250,
  ctr:              0.25,
  zero_result_rate: 0.40,
  avg_results:      0,
};

describe('SummaryCards', () => {
  it('renders total searches formatted', () => {
    const { getByText } = render(SummaryCards, { summary: base });
    expect(getByText('1,000')).toBeTruthy();
  });

  it('renders unique queries formatted', () => {
    const { getByText } = render(SummaryCards, { summary: base });
    expect(getByText('250')).toBeTruthy();
  });

  it('renders CTR as percentage', () => {
    const { getByText } = render(SummaryCards, { summary: base });
    expect(getByText('25.0%')).toBeTruthy();
  });

  it('renders no-click rate as percentage', () => {
    const { getByText } = render(SummaryCards, { summary: base });
    expect(getByText('40.0%')).toBeTruthy();
  });

  it('applies good class to CTR when >= 30%', () => {
    const { container } = render(SummaryCards, { summary: { ...base, ctr: 0.35 } });
    const ctrValue = container.querySelectorAll('.stat-value')[2];
    expect(ctrValue.classList.contains('good')).toBe(true);
  });

  it('does not apply good class to CTR when < 30%', () => {
    const { container } = render(SummaryCards, { summary: { ...base, ctr: 0.10 } });
    const ctrValue = container.querySelectorAll('.stat-value')[2];
    expect(ctrValue.classList.contains('good')).toBe(false);
  });

  it('applies danger class to no-click rate when > 50%', () => {
    const { container } = render(SummaryCards, { summary: { ...base, zero_result_rate: 0.75 } });
    const noClickValue = container.querySelectorAll('.stat-value')[3];
    expect(noClickValue.classList.contains('danger')).toBe(true);
  });

  it('does not apply danger class to no-click rate when <= 50%', () => {
    const { container } = render(SummaryCards, { summary: { ...base, zero_result_rate: 0.40 } });
    const noClickValue = container.querySelectorAll('.stat-value')[3];
    expect(noClickValue.classList.contains('danger')).toBe(false);
  });

  it('renders all four stat cards', () => {
    const { container } = render(SummaryCards, { summary: base });
    expect(container.querySelectorAll('.stat-card')).toHaveLength(4);
  });

  it('renders zero values without errors', () => {
    const zero: AnalyticsSummary = { total_searches: 0, unique_queries: 0, ctr: 0, zero_result_rate: 0, avg_results: 0 };
    const { getAllByText } = render(SummaryCards, { summary: zero });
    // both CTR and no-click rate render as 0.0% when all stats are zero
    expect(getAllByText('0.0%')).toHaveLength(2);
  });
});
