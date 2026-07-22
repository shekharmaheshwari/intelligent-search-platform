import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { analyticsStore } from '../../../src/lib/stores/analytics-store';
import { analyticsRepository } from '../../../src/lib/repository/AnalyticsRepository';
import type { AnalyticsData } from '../../../src/lib/types';

const mockData: AnalyticsData = {
  summary:      { total_searches: 85, unique_queries: 40, ctr: 0.19, zero_result_rate: 0.81, avg_results: 0 },
  top_queries:  [{ query: 'iphone', count: 50, avg_results: 0 }],
  zero_results: [{ query: 'samsung s30', count: 20 }],
  volume:       [{ date: '2026-07-20', count: 85, clicks: 16 }],
  top_facets:   [],
};

beforeEach(() => analyticsStore.reset());
afterEach(() => vi.restoreAllMocks());

describe('analytics-store', () => {
  describe('initial state', () => {
    it('starts with null data', () => {
      expect(get(analyticsStore).data).toBeNull();
    });

    it('starts with loading false', () => {
      expect(get(analyticsStore).loading).toBe(false);
    });

    it('starts with empty error', () => {
      expect(get(analyticsStore).error).toBe('');
    });

    it('starts with null indexId', () => {
      expect(get(analyticsStore).indexId).toBeNull();
    });

    it('starts with 30d range', () => {
      expect(get(analyticsStore).range).toBe('30d');
    });
  });

  describe('load', () => {
    it('sets loading true while fetching', async () => {
      let loadingDuringFetch = false;
      vi.spyOn(analyticsRepository, 'getAnalytics').mockImplementation(async () => {
        loadingDuringFetch = get(analyticsStore).loading;
        return mockData;
      });

      await analyticsStore.load('idx-1');
      expect(loadingDuringFetch).toBe(true);
    });

    it('sets data and clears loading on success', async () => {
      vi.spyOn(analyticsRepository, 'getAnalytics').mockResolvedValue(mockData);

      await analyticsStore.load('idx-1');

      const state = get(analyticsStore);
      expect(state.loading).toBe(false);
      expect(state.data?.summary.total_searches).toBe(85);
      expect(state.error).toBe('');
    });

    it('stores the indexId in state', async () => {
      vi.spyOn(analyticsRepository, 'getAnalytics').mockResolvedValue(mockData);

      await analyticsStore.load('idx-abc');

      expect(get(analyticsStore).indexId).toBe('idx-abc');
    });

    it('uses provided range, not the default', async () => {
      const spy = vi.spyOn(analyticsRepository, 'getAnalytics').mockResolvedValue(mockData);

      await analyticsStore.load('idx-1', '7d');

      expect(spy).toHaveBeenCalledWith('idx-1', '7d');
      expect(get(analyticsStore).range).toBe('7d');
    });

    it('sets error and clears loading on failure', async () => {
      vi.spyOn(analyticsRepository, 'getAnalytics').mockRejectedValue(new Error('404 Not Found'));

      await analyticsStore.load('idx-bad');

      const state = get(analyticsStore);
      expect(state.loading).toBe(false);
      expect(state.data).toBeNull();
      expect(state.error).toBe('404 Not Found');
    });

    it('clears previous error on new load', async () => {
      vi.spyOn(analyticsRepository, 'getAnalytics')
        .mockRejectedValueOnce(new Error('500 error'))
        .mockResolvedValueOnce(mockData);

      await analyticsStore.load('idx-1');
      expect(get(analyticsStore).error).toBe('500 error');

      await analyticsStore.load('idx-1');
      expect(get(analyticsStore).error).toBe('');
    });
  });

  describe('setRange', () => {
    it('updates range in state', () => {
      vi.spyOn(analyticsRepository, 'getAnalytics').mockResolvedValue(mockData);
      analyticsStore.setRange('7d');
      expect(get(analyticsStore).range).toBe('7d');
    });

    it('triggers a reload when indexId is set', async () => {
      vi.spyOn(analyticsRepository, 'getAnalytics').mockResolvedValue(mockData);
      await analyticsStore.load('idx-1', '30d');

      const spy = vi.spyOn(analyticsRepository, 'getAnalytics').mockResolvedValue(mockData);
      analyticsStore.setRange('7d');

      await vi.waitFor(() => expect(spy).toHaveBeenCalledWith('idx-1', '7d'));
    });

    it('does not trigger a reload when indexId is null', () => {
      const spy = vi.spyOn(analyticsRepository, 'getAnalytics');
      analyticsStore.setRange('90d');
      expect(spy).not.toHaveBeenCalled();
    });
  });

  describe('reset', () => {
    it('clears all state back to initial values', async () => {
      vi.spyOn(analyticsRepository, 'getAnalytics').mockResolvedValue(mockData);
      await analyticsStore.load('idx-1');

      analyticsStore.reset();

      const state = get(analyticsStore);
      expect(state.data).toBeNull();
      expect(state.loading).toBe(false);
      expect(state.error).toBe('');
      expect(state.indexId).toBeNull();
      expect(state.range).toBe('30d');
    });
  });
});
