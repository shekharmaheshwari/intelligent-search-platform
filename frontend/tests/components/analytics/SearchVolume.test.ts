// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import SearchVolume from '../../../src/components/analytics/SearchVolume.svelte';
import type { VolumePoint } from '../../../src/lib/types';

const volume: VolumePoint[] = [
  { date: '2026-07-17', count: 120, clicks: 15 },
  { date: '2026-07-18', count: 80,  clicks: 8  },
  { date: '2026-07-19', count: 200, clicks: 30 },
];

describe('SearchVolume', () => {
  it('shows empty state when volume is empty', () => {
    const { getByText } = render(SearchVolume, { volume: [] });
    expect(getByText('No data yet')).toBeTruthy();
  });

  it('renders an SVG chart when data is provided', () => {
    const { container } = render(SearchVolume, { volume });
    expect(container.querySelector('svg')).toBeTruthy();
  });

  it('renders two polylines for searches and clicks', () => {
    const { container } = render(SearchVolume, { volume });
    expect(container.querySelectorAll('polyline')).toHaveLength(2);
  });

  it('renders searches legend dot', () => {
    const { container } = render(SearchVolume, { volume });
    expect(container.querySelector('.leg-dot.searches')).toBeTruthy();
  });

  it('renders clicks legend dot', () => {
    const { container } = render(SearchVolume, { volume });
    expect(container.querySelector('.leg-dot.clicks')).toBeTruthy();
  });

  it('shows total search count in legend label', () => {
    const { container } = render(SearchVolume, { volume });
    // total count = 120 + 80 + 200 = 400
    const labels = container.querySelectorAll('.leg-label');
    expect(labels[0].textContent).toContain('400');
  });

  it('shows total click count in legend label', () => {
    const { container } = render(SearchVolume, { volume });
    // total clicks = 15 + 8 + 30 = 53
    const labels = container.querySelectorAll('.leg-label');
    expect(labels[1].textContent).toContain('53');
  });

  it('renders area fills for both series', () => {
    const { container } = render(SearchVolume, { volume });
    expect(container.querySelectorAll('polygon')).toHaveLength(2);
  });

  it('renders gradient defs', () => {
    const { container } = render(SearchVolume, { volume });
    expect(container.querySelector('defs')).toBeTruthy();
  });

  it('searches polyline has searches class', () => {
    const { container } = render(SearchVolume, { volume });
    expect(container.querySelector('polyline.searches')).toBeTruthy();
  });

  it('clicks polyline has clicks class', () => {
    const { container } = render(SearchVolume, { volume });
    expect(container.querySelector('polyline.clicks')).toBeTruthy();
  });
});
