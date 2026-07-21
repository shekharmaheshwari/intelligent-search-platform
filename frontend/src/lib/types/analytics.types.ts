export interface TopQuery {
  query:       string;
  count:       number;
  avg_results: number;
}

export interface ZeroResultQuery {
  query: string;
  count: number;
}

export interface VolumePoint {
  date:   string; // ISO date string YYYY-MM-DD
  count:  number; // total search events
  clicks: number; // click events
}

export interface TopFacet {
  field: string;
  value: string;
  count: number;
}

export interface AnalyticsSummary {
  total_searches:    number;
  unique_queries:    number;
  ctr:               number; // 0–1, fraction of searches with at least one click
  zero_result_rate:  number; // 0–1, fraction of searches with no clicks (proxy for relevance gaps)
  avg_results:       number;
}

export interface AnalyticsData {
  summary:      AnalyticsSummary;
  top_queries:  TopQuery[];
  zero_results: ZeroResultQuery[];
  volume:       VolumePoint[];
  top_facets:   TopFacet[];
}

export interface AnalyticsState {
  data:    AnalyticsData | null;
  loading: boolean;
  error:   string;
  indexId: string | null;
  range:   '7d' | '30d' | '90d';
}
