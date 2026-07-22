# Analytics Dashboard

> **Status: Implemented** — frontend tab + backend endpoint fully wired.

The analytics dashboard gives you a per-index view of search behaviour: how many searches are happening, what users are searching for, and whether they are finding what they need (measured by click-through rate).

---

## What it shows

| Section | What it measures |
|---|---|
| **Summary cards** | Total searches, unique queries, CTR, no-click rate |
| **Searches & Clicks chart** | Daily search volume vs click volume as a dual line graph |
| **Top Searches** | Most frequent queries ranked by count |
| **Zero Results** | Queries where users never clicked anything (relevance gaps) |

### CTR vs No-Click Rate

- **CTR** (click-through rate) = searches where at least one result was clicked ÷ total searches. Higher is better.
- **No-Click Rate** = 1 − CTR. A persistently high no-click rate (> 50%) signals a relevance problem — users are not finding what they searched for.

> Note: ISP does not yet store the raw result count per search, so `avg_results` is always 0. True zero-result detection (no documents returned) requires a `results_count` field in `search_events`, which is planned for a future release.

---

## Backend endpoint

```
GET /v1/indexes/{index_id}/analytics?range=7d|30d|90d
```

**Auth:** requires a valid JWT Bearer token (same session token used by the ISP dashboard).

**Response shape:**

```json
{
  "summary": {
    "total_searches":   85,
    "unique_queries":   40,
    "ctr":              0.1882,
    "zero_result_rate": 0.8118,
    "avg_results":      0.0
  },
  "top_queries": [
    { "query": "iphone 15 case", "count": 164, "avg_results": 0.0 }
  ],
  "zero_results": [
    { "query": "samsung charger", "count": 120 }
  ],
  "volume": [
    { "date": "2026-07-18", "count": 649, "clicks": 12 }
  ],
  "top_facets": []
}
```

**How aggregations work:**

1. Fetch all `search_events` rows for the index in the requested window (`created_at >= cutoff_ms`).
2. Group by `(session_id, query)` to get distinct search sessions.
3. A session is "clicked" if any row in that `(session_id, query)` group has `clicked = true`.
4. CTR = clicked sessions ÷ total sessions.
5. Daily volume (`count`) = total event rows per day; daily `clicks` = rows where `clicked = true`.
6. Top queries = sorted by raw event count descending, top 20.
7. Zero results list = queries where no session ever clicked, top 20.

---

## Frontend architecture

The analytics module is fully decoupled from the rest of the ISP frontend. It does not share stores, repositories, or types with any other feature.

```
App.svelte
  passes indexes={idxState.indexes}
    ↓
AnalyticsPanel.svelte       UI: index picker, range tabs, loading/error, composes sub-components
  calls analyticsController
    ↓
analyticsController.ts      Business logic: init / changeIndex / changeRange / teardown
  calls analyticsStore
    ↓
analytics-store.ts          State: { data, loading, error, indexId, range }
  calls analyticsRepository
    ↓
AnalyticsRepository.ts      HTTP: GET /v1/indexes/{id}/analytics?range=…
```

### Sub-components

| File | Receives | Renders |
|---|---|---|
| `SummaryCards.svelte` | `AnalyticsSummary` | 4 stat cards |
| `SearchVolume.svelte` | `VolumePoint[]` | SVG dual line chart |
| `TopSearches.svelte` | `TopQuery[]` | Ranked query table |
| `ZeroResults.svelte` | `ZeroResultQuery[]` | No-click query table |

Each sub-component is prop-only — no store imports, no side effects. All data flows down from `AnalyticsPanel`.

### Types

Defined in `src/lib/types/analytics.types.ts`, exported through `src/lib/types/index.ts`.

```typescript
interface AnalyticsSummary {
  total_searches:   number;
  unique_queries:   number;
  ctr:              number;  // 0–1
  zero_result_rate: number;  // 0–1
  avg_results:      number;
}

interface VolumePoint {
  date:   string;  // YYYY-MM-DD
  count:  number;  // total search events
  clicks: number;  // click events
}
```

---

## Auth middleware

The endpoint lives at `/v1/indexes/{id}/analytics` which falls under the `/v1/` JWT-protected prefix. The frontend `AnalyticsRepository` reads `isp-auth-token` from `localStorage` and passes it as `Authorization: Bearer <token>` when `PUBLIC_AUTH_ENABLED=true`.

The existing analytics ingestion endpoints (`/v1/analytics/impressions`, `/v1/analytics/click`) remain public — they are called by the PLP storefront, not the dashboard.

---

## Data source

All analytics are computed from the `search_events` PostgreSQL table, populated by the PLP via `POST /v1/analytics/impressions` and `POST /v1/analytics/click`.

```sql
search_events (
  index_id   TEXT,
  session_id TEXT,
  query      TEXT,
  doc_id     TEXT,
  rank       INT,
  clicked    BOOLEAN,
  source     TEXT,
  created_at BIGINT   -- Unix milliseconds
)
```

See [analyticsDataLearning.md](./analyticsDataLearning.md) for how click signals feed back into Typesense ranking via the popularity aggregation pipeline.

---

## Extending the dashboard

**To add a new metric:**
1. Add the field to `AnalyticsSummary` or create a new interface in `analytics.types.ts`.
2. Compute it in `analytics.py` → `index_analytics()` and include it in the response.
3. Add a prop to the relevant sub-component (or create a new one in `src/components/analytics/`).
4. Wire the new component into `AnalyticsPanel.svelte`.

No other files need to change — the controller and store are data-shape agnostic.

**To add true zero-result detection:**
Add a `results_count INT` column to `search_events`, populate it from the Typesense `found` field in the impressions endpoint, and use `results_count = 0` instead of `clicked = false` as the zero-result signal.
