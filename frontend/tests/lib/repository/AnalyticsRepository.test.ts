import { describe, it, expect, vi, afterEach } from 'vitest';
import { analyticsRepository } from '../../../src/lib/repository/AnalyticsRepository';
import type { AnalyticsData } from '../../../src/lib/types';

const mockData: AnalyticsData = {
  summary:      { total_searches: 100, unique_queries: 40, ctr: 0.2, zero_result_rate: 0.8, avg_results: 0 },
  top_queries:  [{ query: 'test', count: 10, avg_results: 0 }],
  zero_results: [{ query: 'nothing', count: 5 }],
  volume:       [{ date: '2026-07-20', count: 50, clicks: 10 }],
  top_facets:   [],
};

function mockFetch(data: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok:     status >= 200 && status < 300,
    status,
    json:   async () => data,
    text:   async () => JSON.stringify(data),
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('AnalyticsRepository', () => {
  describe('getAnalytics', () => {
    it('calls the correct URL with indexId and range', async () => {
      const fetch = mockFetch(mockData);
      vi.stubGlobal('fetch', fetch);

      await analyticsRepository.getAnalytics('idx-123', '30d');

      expect(fetch).toHaveBeenCalledOnce();
      const [url] = fetch.mock.calls[0];
      expect(url).toContain('/indexes/idx-123/analytics');
      expect(url).toContain('range=30d');
    });

    it('defaults range to 30d when not specified', async () => {
      const fetch = mockFetch(mockData);
      vi.stubGlobal('fetch', fetch);

      await analyticsRepository.getAnalytics('idx-123');

      const [url] = fetch.mock.calls[0];
      expect(url).toContain('range=30d');
    });

    it('uses 7d range when specified', async () => {
      const fetch = mockFetch(mockData);
      vi.stubGlobal('fetch', fetch);

      await analyticsRepository.getAnalytics('idx-abc', '7d');

      const [url] = fetch.mock.calls[0];
      expect(url).toContain('range=7d');
    });

    it('uses 90d range when specified', async () => {
      const fetch = mockFetch(mockData);
      vi.stubGlobal('fetch', fetch);

      await analyticsRepository.getAnalytics('idx-abc', '90d');

      const [url] = fetch.mock.calls[0];
      expect(url).toContain('range=90d');
    });

    it('returns the parsed JSON response', async () => {
      vi.stubGlobal('fetch', mockFetch(mockData));

      const result = await analyticsRepository.getAnalytics('idx-123', '30d');

      expect(result.summary.total_searches).toBe(100);
      expect(result.volume).toHaveLength(1);
      expect(result.top_queries[0].query).toBe('test');
    });

    it('throws an error on non-ok response', async () => {
      vi.stubGlobal('fetch', mockFetch({ detail: 'Not Found' }, 404));

      await expect(analyticsRepository.getAnalytics('bad-idx', '30d')).rejects.toThrow('404');
    });

    it('sends Content-Type application/json header', async () => {
      const fetch = mockFetch(mockData);
      vi.stubGlobal('fetch', fetch);

      await analyticsRepository.getAnalytics('idx-123', '30d');

      const [, opts] = fetch.mock.calls[0];
      expect(opts.headers['Content-Type']).toBe('application/json');
    });
  });
});
