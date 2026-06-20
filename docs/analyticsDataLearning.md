# Analytics-Driven Learning — ISP Ranking Feedback Loop

> **Status: Implemented** (Phases 1–3 complete; Phase 4 query-level metrics available via `GET /v1/analytics/summary`)

## Goal

ISP learns from user behaviour (search clicks, result engagement) and feeds those signals back into the ranking model — without re-embedding or rebuilding indexes.

ISP already has the key hook: `popularity` and `is_featured` are top-level Typesense fields updated via `PATCH /v1/indexes/{id}/documents/{doc_id}` without touching embeddings. The analytics system wires real usage signals into those fields automatically.

---

## Architecture Overview

```
Search results displayed
        │
        ▼
POST /v1/analytics/impressions   ← one batch request per search page
  items: [{doc_id, rank}, ...]
        │
User clicks a product card
        │
        ▼
POST /v1/analytics/click         ← one request per click
  {doc_id, rank, query, ...}
        │
        ▼
search_events (PostgreSQL)
  query | index_id | doc_id | rank | clicked | session_id | created_at
        │
        ▼
Aggregation job (POST /v1/analytics/aggregate)
  — position_weight(rank) = 1 / log2(rank + 2)
  — click contributes weight × 1.0
  — impression contributes weight × 0.1  (10× less than a click)
  — normalise to 0–1,000,000 popularity score per index
        │
        ▼
update_document_fields(col, doc_id, {popularity: N})
        │
        ▼
Typesense: popularity field updated (no re-embed)
        │
        ▼
RRF score boosted on next search
  final_score += min(popularity, 1_000_000) / 2_000_000   → up to +0.5
```

---

## Implementation

### Phase 1 — Capture Events ✅

**What:** Log every search impression and click from both the Management UI and the PLP.

**New database table:**

```sql
CREATE TABLE IF NOT EXISTS search_events (
    id          BIGSERIAL PRIMARY KEY,
    index_id    VARCHAR(12) REFERENCES indexes(id) ON DELETE CASCADE,
    session_id  VARCHAR(64),
    query       TEXT,
    doc_id      VARCHAR(128),
    rank        INTEGER,         -- 0-based position in result list
    clicked     BOOLEAN DEFAULT FALSE,
    source      VARCHAR(32),     -- "mgmt_ui" | "plp"
    created_at  BIGINT           -- Unix timestamp ms
);

CREATE INDEX ON search_events (index_id, doc_id, created_at);
CREATE INDEX ON search_events (created_at);
```

**API endpoints:**

```bash
# Batch impressions — one request per search, all visible docs
POST /v1/analytics/impressions
{
  "index_id":   "tablets",
  "session_id": "sess_xyz",
  "query":      "galaxy tab",
  "source":     "plp",
  "items": [{"doc_id": "abc123", "rank": 0}, {"doc_id": "def456", "rank": 1}]
}

# Single click — fired when user opens a product card
POST /v1/analytics/click
{
  "index_id":   "tablets",
  "session_id": "sess_xyz",
  "query":      "galaxy tab",
  "doc_id":     "abc123",
  "rank":       0,
  "source":     "plp"
}
```

Both return `204 No Content` and are fire-and-forget (errors never surface to the user).

**Implemented in:**
- `backend/src/isp/routers/analytics.py` — `POST /v1/analytics/impressions`, `POST /v1/analytics/click`
- `backend/src/isp/services/domain/analytics_service.py` — `log_event()`, `log_events_batch()`
- `backend/src/isp/services/infra/database.py` — `search_events` table DDL
- `plp/src/lib/repository/catalogRepository.ts` — `logImpressions()`, `logClick()`
- `plp/src/lib/controller/catalogController.ts` — batch impressions after search, `notifyClick()` export
- `plp/src/components/ProductCard.svelte` — calls `notifyClick()` on card click
- `plp/src/components/ProductGrid.svelte` — passes `rank`, `tab`, `query` props

---

### Phase 2 — Aggregate Signals ✅

**What:** A background job computes a `popularity` score per document from raw click events.

**Scoring formula — impressions and clicks both contribute:**

```
position_weight(rank)  = 1 / log2(rank + 2)      # rank 0 = 1.0, rank 9 ≈ 0.29

contribution(event)    = position_weight × 1.0    # click  (full signal)
                       = position_weight × 0.1    # impression (1/10th of click)

raw_score(doc)         = SUM(contribution) across all events in the last 30 days

normalised_popularity(doc) =
    round( raw_score(doc) / max_raw_score_in_index * 1_000_000 )
```

Every document shown in search results builds popularity over time. Clicks count 10× more than bare impressions — a single click at rank 0 equals 10 impressions at the same position. Position discounting ensures high-rank signals are worth more than the same action buried on page 3.

**Example scores with one search session (3 docs shown, 1 clicked):**

| Document | Events | raw_score | popularity |
|---|---|---|---|
| Doc A (rank 0) | impression + click | `1.0×0.1 + 1.0×1.0 = 1.1` | 1,000,000 |
| Doc B (rank 1) | impression only | `0.63×0.1 = 0.063` | 57,357 |
| Doc C (rank 2) | impression only | `0.50×0.1 = 0.050` | 45,455 |

**Trigger options (choose one):**

| Option | When | Trade-off |
|--------|------|-----------|
| On-demand endpoint `POST /v1/analytics/aggregate` | Manual / scheduled externally | Simple, no background thread |
| FastAPI `BackgroundTasks` after N new events | Near real-time | Adds overhead per request |
| APScheduler cron inside isp-api | Hourly, automatic | Self-contained, no external scheduler |

Implemented as **on-demand endpoint** (`POST /v1/analytics/aggregate`). Promote to a cron job once traffic warrants it.

**Implemented in:**
- `backend/src/isp/routers/analytics.py` — `POST /v1/analytics/aggregate?index_id=<slug-or-id>&days=30`
- `backend/src/isp/services/domain/analytics_service.py` — `aggregate_popularity()`, `apply_popularity()`; weights controlled by `_CLICK_WEIGHT = 1.0` and `_IMPRESSION_WEIGHT = 0.1` constants at the top of the file

---

### Phase 3 — Apply Scores to Typesense ✅

**What:** Write aggregated scores back to Typesense using the existing partial-update path.

```python
# Pseudocode — runs inside aggregation job
for doc_id, new_popularity in aggregated_scores.items():
    await typesense_client.update_document_fields(
        col_name=collection_name(index_id),
        doc_id=doc_id,
        fields={"popularity": new_popularity}
    )
```

- No re-embedding required.
- No index rebuild required.
- Takes effect on the next search request.
- Existing hand-set `is_featured` values are untouched (different field).

**Implemented in:**
- `backend/src/isp/services/domain/analytics_service.py` — `apply_popularity()`, calls `ts.update_document_fields()`

---

### Phase 4 — Query-Level Analytics ✅

**What:** Surface query health metrics via API.

**Metrics to track:**

| Metric | Definition | Action |
|--------|-----------|--------|
| Zero-result rate | Queries returning 0 hits / total queries | Trigger alert; review connector coverage |
| Low-CTR queries | Queries with impressions but no clicks | Candidates for synonym expansion or content gaps |
| Top queries | Most frequent queries per index | Inform extension field priorities |
| Query reformulation rate | Same session: query → new query within 30 s | Signals poor first-result quality |

**Endpoint:**

```bash
curl "http://localhost:8001/v1/analytics/summary?index_id=tablets&days=30"
# {
#   "total_searches": 1420,
#   "zero_result_rate": 0.04,
#   "top_queries": [{"q": "galaxy tab", "count": 312}, ...],
#   "low_ctr_queries": [{"q": "android tablet"}]
# }
```

**Implemented in:**
- `backend/src/isp/routers/analytics.py` — `GET /v1/analytics/summary`
- `backend/src/isp/services/domain/analytics_service.py` — `get_summary()`

---

## Data Retention

| Table | Retention | Reason |
|-------|-----------|--------|
| `search_events` (raw) | 90 days rolling | Raw events grow fast; beyond 90 days marginal signal value |
| Aggregated `popularity` in Typesense | Indefinite | Already a small scalar per doc |

A nightly cleanup job:

```sql
DELETE FROM search_events WHERE created_at < (EXTRACT(EPOCH FROM NOW()) - 90*86400) * 1000;
```

---

## Privacy Considerations

- `session_id` should be an anonymous ephemeral token — no PII, no user ID.
- `query` text is stored; ensure it does not contain PII before logging (e.g. filter patterns that look like emails or phone numbers).
- No cross-session linkage is required for the scoring formula.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/src/isp/routers/analytics.py` | New router: `POST /v1/analytics/event`, `POST /v1/analytics/aggregate`, `GET /v1/analytics/summary` |
| `backend/src/isp/services/domain/analytics_service.py` | `log_event`, `aggregate_popularity`, `apply_popularity`, `get_summary`, `cleanup_old_events` |
| `backend/src/isp/services/infra/database.py` | `search_events` table + 2 indexes added to `init_db()` |
| `backend/src/isp/main.py` | Register analytics router |
| `plp/src/lib/repository/catalogRepository.ts` | `logEvent()` — fire-and-forget fetch |
| `plp/src/lib/controller/catalogController.ts` | `SESSION_ID`, impression events in `loadProducts`, `notifyClick()` export |
| `plp/src/components/ProductCard.svelte` | Accepts `rank`/`tab`/`query` props; calls `notifyClick()` on click |
| `plp/src/components/ProductGrid.svelte` | Passes `rank`, `tab`, `query` to each `ProductCard` |
| `plp/src/lib/types.ts` | `AnalyticsEvent` interface |

---

## Open Questions

1. **Aggregation frequency** — hourly is fine for PLP; real-time matters less than correctness.
2. **Cold start** — new documents have no click history; existing hand-set `popularity` values should be preserved until the doc accumulates enough signal (e.g. minimum 10 impressions threshold before overwriting).
3. **Negative signals** — clicks on poor results inflate scores; consider dwell time or conversion signals if available from the PLP.
4. **Multi-index normalisation** — popularity is normalised per-index, so scores are not comparable across indexes. This is intentional (each index has its own catalog).
