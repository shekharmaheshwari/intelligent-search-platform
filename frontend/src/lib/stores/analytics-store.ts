// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

import { StoreBase } from './StoreBase';
import type { AnalyticsState } from '../types';
import { analyticsRepository } from '../repository/AnalyticsRepository';

class AnalyticsStore extends StoreBase<AnalyticsState> {
  constructor() {
    super({ data: null, loading: false, error: '', indexId: null, range: '30d' });
  }

  async load(indexId: string, range?: AnalyticsState['range']): Promise<void> {
    const r = range ?? this.getValue().range;
    this.update(s => ({ ...s, loading: true, error: '', indexId, range: r }));
    try {
      const data = await analyticsRepository.getAnalytics(indexId, r);
      this.update(s => ({ ...s, loading: false, data }));
    } catch (e: any) {
      this.update(s => ({ ...s, loading: false, error: e.message ?? 'Failed to load analytics' }));
    }
  }

  setRange(range: AnalyticsState['range']): void {
    this.update(s => ({ ...s, range }));
    const { indexId } = this.getValue();
    if (indexId) this.load(indexId, range);
  }

  reset(): void {
    this.set({ data: null, loading: false, error: '', indexId: null, range: '30d' });
  }
}

export const analyticsStore = new AnalyticsStore();
