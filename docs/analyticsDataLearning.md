# Analytics-Driven Learning — ISP Ranking Feedback Loop

## Goal

Update ISP to learn from user behaviour (search clicks, result engagement) and feed those signals back into the ranking model — without re-embedding or rebuilding indexes.

ISP already has the key hook: `popularity` and `is_featured` are top-level Typesense fields updated via `PATCH /v1/indexes/{id}/documents/{doc_id}` without touching embeddings. The plan wires real usage signals into those fields automatically.

---

## Architecture Overview

```
User clicks result
        │
        ▼
POST /v1/analytics/event
        │
        ▼
search_events (PostgreSQL)
  query | index_id | doc_id | rank | clicked | session_id | timestamp
        │
        ▼
Aggregation job (background / cron)
  — CTR per doc per index (rolling 30-day window)
  — position-discounted click weight
  — normalise to 0–1,000,000 popularity score
        │
        ▼
update_document_fields(col, doc_id, {popularity: N})
        │
        ▼
Typesense: popularity field updated (no re-embed)
        │
        ▼
RRF score boosted on next search
  final_score += min(popularity, 1_000_000) / 2_000_000
```

---

## Phased Implementation Plan

### Phase 1 — Capture Events

**What:** Log every search impression and click from both the Management UI and the T-Mobile PLP.

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

**New API endpoint:**

```
POST /v1/analytics/event
Body: {
  "index_id":   "abc123",
  "session_id": "sess_xyz",
  "query":      "iphone 15 pro",
  "doc_id":     "d4f9a1...",
  "rank":        2,
  "clicked":    true,
  "source":     "plp"
}
```

Returns `204 No Content`.

**Frontend changes:**
- `SourceContent.svelte` — fire event on document click/expand.
- T-Mobile PLP (`tmo-plp`) — fire event on product card click.

---

### Phase 2 — Aggregate Signals

**What:** A background job computes a `popularity` score per document from raw click events.

**Scoring formula:**

```
position_weight(rank) = 1 / log2(rank + 2)   # higher rank = lower weight

raw_score(doc) = SUM of position_weight(rank)
                 for each clicked event on doc
                 in the last 30 days

normalised_popularity(doc) =
    round( raw_score(doc) / max_raw_score_in_index * 1_000_000 )
```

Position discounting ensures clicks on rank-1 results count more than clicks buried on page 3.

**Trigger options (choose one):**

| Option | When | Trade-off |
|--------|------|-----------|
| On-demand endpoint `POST /v1/analytics/aggregate` | Manual / scheduled externally | Simple, no background thread |
| FastAPI `BackgroundTasks` after N new events | Near real-time | Adds overhead per request |
| APScheduler cron inside isp-api | Hourly, automatic | Self-contained, no external scheduler |

Recommended starting point: **on-demand endpoint**, promoted to cron once traffic warrants it.

---

### Phase 3 — Apply Scores to Typesense

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

---

### Phase 4 — Query-Level Analytics

**What:** Surface query health metrics in the Management UI.

**Metrics to track:**

| Metric | Definition | Action |
|--------|-----------|--------|
| Zero-result rate | Queries returning 0 hits / total queries | Trigger alert; review connector coverage |
| Low-CTR queries | Queries with impressions but no clicks | Candidates for synonym expansion or content gaps |
| Top queries | Most frequent queries per index | Inform extension field priorities |
| Query reformulation rate | Same session: query → new query within 30 s | Signals poor first-result quality |

**New endpoint:**

```
GET /v1/analytics/summary?index_id=abc123&days=30
Response: {
  "total_searches": 1420,
  "zero_result_rate": 0.04,
  "top_queries": [{"q": "iphone 15", "count": 312}, ...],
  "low_ctr_queries": [{"q": "blue phone case", "ctr": 0.02}, ...]
}
```

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

## Files to Create / Modify

| File | Change |
|------|--------|
| `backend/src/isp/routers/analytics.py` | New router: event ingest + aggregate + summary endpoints |
| `backend/src/isp/services/analytics_service.py` | PostgreSQL CRUD for `search_events`; aggregation logic |
| `backend/src/isp/main.py` | Register analytics router; add `search_events` table to `init_db()` |
| `frontend/src/lib/api.ts` | Add `logEvent()` method |
| `frontend/src/components/SourceContent.svelte` | Fire click events |
| `tmo-plp` (separate repo) | Fire click events on product card interaction |

---

## Open Questions

1. **Aggregation frequency** — hourly is fine for PLP; real-time matters less than correctness.
2. **Cold start** — new documents have no click history; existing hand-set `popularity` values should be preserved until the doc accumulates enough signal (e.g. minimum 10 impressions threshold before overwriting).
3. **Negative signals** — clicks on poor results inflate scores; consider dwell time or conversion signals if available from the PLP.
4. **Multi-index normalisation** — popularity is normalised per-index, so scores are not comparable across indexes. This is intentional (each index has its own catalog).
