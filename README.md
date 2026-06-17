# Intelligent Search Platform (ISP)

A self-contained search backend that indexes content from REST APIs and sitemaps into [Typesense](https://typesense.org/), generates 1024-dim vector embeddings via [Voyage AI](https://www.voyageai.com/), and exposes hybrid (lexical + semantic) search. Ships with a Svelte management UI, a JavaScript extension pipeline for field normalisation and merchandising, and public API endpoints for external consumers.

---

## Quick Start

```bash
# 1. Copy env template and add your Voyage AI key
cp backend/.env.example backend/.env

# 2. Start everything (db → api → ui)
./deploy-all.sh
```

| Service | URL |
|---|---|
| ISP Admin UI | http://localhost:3001 |
| ISP API + Swagger | http://localhost:8001/docs |

---

## Architecture

```
Browser (Admin UI / External App)
  └─► isp-ui :3001 (Astro + Svelte)
        └─► isp-api :8001 (FastAPI)
              ├─► isp-db :5432      (PostgreSQL — metadata, connectors, extensions)
              ├─► typesense :8108   (documents + 1024-dim vectors)
              ├─► isp-redis :6379   (L2 embedding cache + distributed job lock)
              └─► Voyage AI (cloud — embeddings)
```

---

## Core Concepts

| Term | Meaning |
|---|---|
| **Index** | A named data source (e.g. *Products*, *Blog*). Each index maps to one Typesense collection. |
| **Connector** | Config telling ISP where to fetch data: REST endpoint or sitemap URL. |
| **Ingest / Rebuild** | Background job: fetch → extensions → embed → upsert. Rebuild drops and recreates the collection; ingest appends. |
| **Extension** | JavaScript `transform(doc)` function run in a V8 isolate before indexing. Chained by `run_order`. |
| **External index** | ISP view over a pre-existing Typesense collection that ISP did not create. |
| **Editorial boost** | Per-document ranking signals (`popularity`, `is_featured`, `boost_queries_equal`, `boost_queries_contains`) stored as top-level Typesense fields and applied after RRF fusion. |
| **intent_label** | Free-text field on each index that maps it to a PLP tab (e.g. `device`, `accessory`, `smartwatch`, `tablet`). Drives self-configuring tab intent detection — no hardcoded keywords or collection IDs. |
| **slug** | Human-readable URL alias for an index (e.g. `tablets`). Auto-generated from the index name on creation and back-filled for existing indexes on first startup after upgrade. All index routes accept either a slug or a hex ID — both always work. |

---

## Hybrid Search

ISP fuses BM25 lexical and cosine-similarity semantic results using **Reciprocal Rank Fusion**:

```
score(d) = 0.7 / (rank_lexical(d) + 60)
         + 0.3 / (rank_semantic(d) + 60)
```

After RRF, four editorial signals are added:

| Signal | Boost | Set by |
|---|---|---|
| `is_featured` | +0.50 | Edit modal — Featured toggle |
| `popularity` | up to +0.50 | Edit modal or extension at index time |
| `boost_queries_equal` | +0.50 | Edit modal or `merchandising_rules.js` |
| `boost_queries_contains` | +0.30 | Edit modal or `merchandising_rules.js` |

`boost_queries_equal` fires when the search query exactly matches a registered term; `boost_queries_contains` fires when the query contains it as a substring — enabling SKU-level query-aware merchandising without reindexing.

---

## Extension System

Extensions are JavaScript files in `backend/extensions/*.js`. ISP seeds them into the database on startup and auto-assigns them to indexes based on the `@assign` header.

```js
// @name       My API Mapper
// @run_order  0
// @assign     external

function transform(doc) {
  doc.display    = doc.productName;
  doc.url        = 'https://example.com/product/' + doc.slug;
  doc.price      = parseFloat(doc.msrp || 0);
  doc.popularity = Math.max(0, 1000 - (doc.rank || 999) * 10);
  return doc;
}
```

Each extension runs inside a **PyMiniRacer V8 isolate** — no filesystem, no network, pure ECMAScript only. Chain multiple extensions by assigning them increasing `run_order` values. ISP ships three example extensions:

| `run_order` | File | Purpose |
|---|---|---|
| 0 | `catalog_mapper.js` | Maps vendor API fields → ISP schema |
| 1 | `version_score.js` | Adjusts `popularity` for new-badge items |
| 2 | `merchandising_rules.js` | Stamps `boost_queries_equal` / `boost_queries_contains` by SKU |

---

## Public API

External applications should use the `/public` endpoints which strip the `vec` embedding field (~4 KB per document) before responding. Ranking is identical.

All `{id}` path segments accept either a **slug** (human-readable, e.g. `tablets`) or a **hex ID** (e.g. `9ed56100f220`). Both forms always work and resolve to the same index.

```bash
# Search by slug — consumer-friendly URL
curl -X POST http://localhost:8001/v1/indexes/tablets/search/public \
  -H "Content-Type: application/json" \
  -d '{"q": "galaxy tab", "mode": "hybrid", "page": 1, "per_page": 10}'

# Equivalent — hex ID form still works
curl -X POST http://localhost:8001/v1/indexes/9ed56100f220/search/public \
  -H "Content-Type: application/json" \
  -d '{"q": "galaxy tab", "mode": "hybrid", "page": 1, "per_page": 10}'

# List documents — no vec in response
curl http://localhost:8001/v1/indexes/tablets/documents/public?page=1&per_page=20
```

---

## Catalog / PLP API

A set of higher-level endpoints designed for Product Listing Pages. These aggregate across all ISP-managed collections and do not require callers to know individual index IDs.

### Tab Intent Detection

Classifies a search query into the most semantically relevant tab using Voyage AI embeddings. Reads `intent_label` from the database at request time — no hardcoded keywords or collection IDs.

```bash
curl 'http://localhost:8001/v1/intent/tab?q=galaxy+watch'
# {"tab":"smartwatch","confidence":0.29,"scores":{"device":0.71,"accessory":0.74,"smartwatch":0.75,"tablet":0.66}}
```

| Response field | Description |
|---|---|
| `tab` | Winning `intent_label`, or `null` when intent cannot be determined (empty query, no labels configured, embedding failure) |
| `confidence` | Winning score / sum of all scores; `0.0` when `tab` is `null` |
| `scores` | Per-label vector similarity scores |

To add a new tab, create an index with `intent_label` set — the intent endpoint self-configures automatically. See [Tab Intent Detection](docs/tabIntent.md) for the full scoring algorithm.

### Product Search

Cross-collection hybrid search filtered by `source_type`:

```bash
curl -X POST http://localhost:8001/v1/catalog \
  -H "Content-Type: application/json" \
  -d '{"q": "samsung", "type": "device", "page": 1, "per_page": 24, "search_mode": "hybrid"}'
```

### Facets

Aggregate manufacturer, category, and price-range facets across all collections for a given type:

```bash
curl 'http://localhost:8001/v1/catalog/facets?type=device'
```

### Autocomplete

Typo-tolerant title suggestions across all collections:

```bash
curl 'http://localhost:8001/v1/suggest?q=sams&limit=8'
```

---

## Analytics

ISP records every search impression and product click in a `search_events` PostgreSQL table and uses them to automatically boost popular documents — no re-embedding or rebuild required.

### Event ingestion

The PLP uses two separate endpoints — one batch call per search for impressions, and one call per click:

```bash
# All impressions from one search — one request regardless of result count
curl -X POST http://localhost:8001/v1/analytics/impressions \
  -H "Content-Type: application/json" \
  -d '{
    "index_id":   "tablets",
    "session_id": "anon-uuid-per-page-load",
    "query":      "galaxy tab",
    "source":     "plp",
    "items": [
      {"doc_id": "327cfc86774f41c3", "rank": 0},
      {"doc_id": "2c8075f4777968b8", "rank": 1}
    ]
  }'
# 204 No Content

# Single click when the user opens a product card
curl -X POST http://localhost:8001/v1/analytics/click \
  -H "Content-Type: application/json" \
  -d '{
    "index_id":   "tablets",
    "session_id": "anon-uuid-per-page-load",
    "query":      "galaxy tab",
    "doc_id":     "327cfc86774f41c3",
    "rank":       0,
    "source":     "plp"
  }'
# 204 No Content
```

`index_id` accepts a slug or hex ID. Unknown indexes are silently dropped.

### Aggregation

Run after collecting signals to compute popularity scores and write them to Typesense:

```bash
curl -X POST "http://localhost:8001/v1/analytics/aggregate?index_id=tablets&days=30"
# {"index_id":"9ed56100f220","slug":"tablets","updated":3}
```

Scoring formula — both impressions and clicks contribute, clicks count 10× more:

```
position_weight(rank)  = 1 / log2(rank + 2)       # rank 0 = 1.0, rank 9 ≈ 0.29
contribution(event)    = position_weight × 1.0     # click
                       = position_weight × 0.1     # impression
raw_score(doc)         = SUM(contribution) across all events in the window
popularity(doc)        = round(raw_score / max_raw_score × 1,000,000)
```

The resulting `popularity` field is consumed by the post-RRF ranker:

```
final_score += min(popularity, 1_000_000) / 2_000_000   → up to +0.5
```

No re-embedding, no rebuild — scores take effect on the next search request.

### Query health

```bash
curl "http://localhost:8001/v1/analytics/summary?index_id=tablets&days=30"
# {
#   "total_searches": 142,
#   "zero_result_rate": 0.04,
#   "top_queries": [{"q": "galaxy tab", "count": 38}, ...],
#   "low_ctr_queries": [{"q": "android tablet", ...}]
# }
```

| Field | Description |
|---|---|
| `total_searches` | Distinct (session_id, query) pairs with any impression |
| `zero_result_rate` | Fraction of searches with no clicks — proxy for relevance gaps |
| `top_queries` | Up to 10 most frequent queries |
| `low_ctr_queries` | Up to 10 queries with impressions but zero clicks — content gap candidates |

---

## API Reference

Full Swagger UI at **http://localhost:8001/docs**

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/v1/indexes` | List / create sources |
| GET/DELETE | `/v1/indexes/{id}` | Get / delete a source |
| POST | `/v1/indexes/{id}/ingest/rest` | Append from REST API |
| POST | `/v1/indexes/{id}/ingest/sitemap` | Append from sitemap |
| POST | `/v1/indexes/{id}/rebuild` | Full rebuild (drop + re-index) |
| GET | `/v1/indexes/{id}/status` | Poll job state |
| GET | `/v1/indexes/{id}/documents` | List documents (includes `vec`) |
| GET | `/v1/indexes/{id}/documents/public` | List documents (no `vec`) |
| PATCH | `/v1/indexes/{id}/documents/{doc_id}` | Update editorial fields without re-indexing |
| POST | `/v1/indexes/{id}/search` | Hybrid/lexical/semantic search (includes `vec`) |
| POST | `/v1/indexes/{id}/search/public` | Same search (no `vec`) |
| GET/POST | `/v1/extensions` | Global extension library |
| GET/POST | `/v1/indexes/{id}/extensions` | Per-index extension assignment |
| GET/PUT | `/v1/settings` | Embedding model config |
| GET | `/v1/content/{slug}/search` | Public slug-based search |
| GET | `/v1/intent/tab` | Classify query into a catalog tab via intent_label |
| POST | `/v1/catalog` | Cross-collection product search |
| GET | `/v1/catalog/facets` | Aggregate facets for a source type |
| GET | `/v1/suggest` | Autocomplete suggestions |
| POST | `/v1/analytics/impressions` | Batch-log all result impressions from one search (204, fire-and-forget) |
| POST | `/v1/analytics/click` | Log a single product click (204, fire-and-forget) |
| POST | `/v1/analytics/aggregate` | Aggregate impression+click signals → write popularity scores to Typesense |
| GET | `/v1/analytics/summary` | Query health metrics: zero-result rate, top queries, low-CTR queries |

The `{id}` path segment in all routes accepts either a **slug** (e.g. `tablets`) or a **hex ID** (e.g. `9ed56100f220`). Slug lookup is tried first; hex ID is the fallback.

Ingest and rebuild endpoints return **HTTP 409** if a job is already running for that index.

---

## Environment Variables

```env
# backend/.env
TYPESENSE_HOST=typesense
TYPESENSE_PORT=8108
TYPESENSE_API_KEY=local-dev-key-change-me
DATABASE_URL=postgresql://isp:isp-dev-password@isp-db:5432/isp
VOYAGE_API_KEY=<your-voyage-key>          # optional — falls back to lexical-only
REDIS_URL=redis://isp-redis:6379/0
EXTENSIONS_DIR=/app/extensions
HOST=0.0.0.0
PORT=8001
```

---

## Deployment Scripts

```bash
./deploy-all.sh      # full stack: db → api → ui
./deploy-api.sh      # rebuild + restart backend only (use after any Python or .js change)
./deploy-ui.sh       # rebuild + restart frontend only
./deploy-db.sh       # start + wait for PostgreSQL
```

---

## Multi-Instance Deployment

ISP is safe to run behind a load balancer. A Redis-backed distributed lock prevents two instances from rebuilding the same index concurrently. The lock is acquired inside the background task (never in the HTTP handler), extended by a heartbeat every 5 minutes, and released in a `finally` block. A crashed container releases its stale locks automatically on restart.

---

## Docs

- [Application Overview](docs/01-application-overview.md)
- [Typesense Integration](docs/02-typesense-integration.md)
- [Database Schema](docs/03-database.md)
- [Codebase Structure](docs/04-codebase.md)
- [Component Integrations](docs/05-integrations.md)
- [Backend Architecture](docs/backend_architecture.md)
- [Score Boost Reference](docs/scoreBoost.md)
- [Tab Intent Detection](docs/tabIntent.md)
