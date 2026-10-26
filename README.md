

https://github.com/user-attachments/assets/872dd46a-3499-40af-bafa-2981f07faf5c













# Intelligent Search Platform (ISP)

A self-contained search backend that indexes content from REST APIs and sitemaps into [Typesense](https://typesense.org/), generates vector embeddings with a pluggable model ([Voyage AI](https://www.voyageai.com/) by default; OpenAI, RexBERT, or any OpenAI-compatible endpoint selectable in the UI), and exposes hybrid (lexical + semantic) search. Ships with a Svelte management UI, a JavaScript extension pipeline for field normalisation and merchandising, and public API endpoints for external consumers.

---

## Quick Start

```bash
# 1. Copy env templates, then fill in VOYAGE_API_KEY / OPENAI_API_KEY in backend/.env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env

# 2. Start everything (db → api → ui)
./deploy-all.sh
```

Login is **off by default** — no Google account or OAuth setup is needed to run locally. Google Sheets ingestion and SSO are optional add-ons (see [Environment Variables](#environment-variables)).

> `backend/.env` and `frontend/.env` are gitignored. Only the `.env.example` templates are committed — never put real keys in them.

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
              ├─► typesense :8108   (documents + vectors)
              ├─► isp-redis :6379   (L2 embedding cache + distributed job lock)
              ├─► isp-vault :8200   (HashiCorp Vault — local secrets store)
              └─► Embedding provider (Voyage AI default · OpenAI · RexBERT · custom)
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
| **Google Sheets connector** | Connector type `gsheet` — fetches rows from a Google Spreadsheet using a service account. Column mapping, field renames, and row-id generation are all configured at ingest time; no schema changes required. |

---

## Workspace Setup

### First-time setup

```bash
# 1. Copy env templates
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env

# 2. Start all containers (includes Vault on first run — auto-inits itself)
docker-compose up -d

# 3. Populate secrets into Vault (run once; reads root token automatically)
./vault/setup-secrets.sh

# 4. Copy the printed VAULT_TOKEN into backend/.env and set SECRET_PROVIDER=vault
#    (or leave SECRET_PROVIDER=env and set secrets directly in backend/.env)

# 5. Start the application
./deploy-all.sh
```

### Google Sheets connector setup

A service account JSON key is required to index content from Google Sheets.

```bash
# Store the key file in a standard location
mkdir -p ~/.config/isp
cp /path/to/your-service-account-key.json ~/.config/isp/gsheet-service-account.json

# vault/setup-secrets.sh reads from that path automatically and writes it to Vault.
# If using SECRET_PROVIDER=env instead, paste the raw JSON into backend/.env:
# GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
```

See [Google Sheets Connector](docs/gsheet-connector.md) for sharing the spreadsheet with the service account email.

### Vault (local secrets store)

`isp-vault` runs as a Docker container with file-backed persistent storage. Secrets survive restarts and recompose cycles via the `isp-vault-data` Docker volume.

| File | Purpose |
|---|---|
| `vault/config/vault.hcl` | Vault server config — file storage, TCP listener, UI enabled |
| `vault/entrypoint.sh` | Auto-init on first run; auto-unseal on every restart |
| `vault/setup-secrets.sh` | One-time script to populate all ISP secrets |

```bash
# Check Vault status
vault status -address=http://localhost:8200

# Read root token (needed if you lose backend/.env)
docker exec isp-vault cat /vault/data/.root_token

# Re-run setup to rotate a secret
./vault/setup-secrets.sh
```

See [Secrets Strategy](docs/secrets-strategy.md) for Vault ↔ AWS Secrets Manager migration details.

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

Classifies a search query into the most semantically relevant tab using the active embedding model. Reads `intent_label` from the database at request time — no hardcoded keywords or collection IDs.

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
| POST | `/v1/indexes/{id}/connectors/gsheet` | Save Google Sheets connector config |
| POST | `/v1/indexes/{id}/ingest/gsheet` | Append from Google Sheets |
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

# ── Core services ─────────────────────────────────────────────────────────────
TYPESENSE_HOST=typesense
TYPESENSE_PORT=8108
TYPESENSE_API_KEY=local-dev-key-change-me
DATABASE_URL=postgresql://isp:isp-dev-password@isp-db:5432/isp
VOYAGE_API_KEY=                           # only if using a Voyage embedding model (the default)
OPENAI_API_KEY=                           # optional — chat LLM + OpenAI embeddings (can also be set in the UI)
REDIS_URL=redis://isp-redis:6379/0
EXTENSIONS_DIR=/app/extensions
HOST=0.0.0.0
PORT=8001
LOG_LEVEL=INFO

# ── Secrets provider ──────────────────────────────────────────────────────────
# "env"   → read secrets directly from this file (default, no extra setup)
# "vault" → read from local HashiCorp Vault (run ./vault/setup-secrets.sh first)
# "aws"   → read from AWS Secrets Manager (set AWS_REGION; uses IAM Role in prod)
SECRET_PROVIDER=env

# Required when SECRET_PROVIDER=vault
# VAULT_ADDR=http://isp-vault:8200
# VAULT_TOKEN=<printed by vault/setup-secrets.sh>

# Required when SECRET_PROVIDER=aws
# AWS_REGION=us-east-1

# ── Optional: Google Sheets connector ─────────────────────────────────────────
# Only needed to ingest from Google Sheets (and only when SECRET_PROVIDER=env).
# Raw service-account JSON on one line.
# GOOGLE_SERVICE_ACCOUNT_JSON=

# ── Optional: OAuth2 / OIDC SSO ───────────────────────────────────────────────
# Disabled by default — the API and UI work without login.
AUTH_ENABLED=false
# OAUTH_PROVIDER=google            # google | github | microsoft
# OAUTH_CLIENT_ID=
# OAUTH_CLIENT_SECRET=
# OAUTH_ALLOWED_DOMAIN=            # e.g. yourcompany.com — empty = any account
# OAUTH_REDIRECT_URI=http://localhost:8001/auth/callback
# OAUTH_FRONTEND_URL=http://localhost:3001
# JWT_SECRET=                      # generate with: openssl rand -hex 32
```

```env
# frontend/.env
PUBLIC_AUTH_ENABLED=false          # must match AUTH_ENABLED in backend/.env
# PUBLIC_OAUTH_PROVIDER=google     # google | github — sign-in button label
```

**Enabling SSO:** set `AUTH_ENABLED=true` and fill in the `OAUTH_*` / `JWT_SECRET` values in `backend/.env`, set `PUBLIC_AUTH_ENABLED=true` in `frontend/.env`, then rebuild the UI (`PUBLIC_*` values are baked in at build time). See [Auth Approaches](docs/auth-approaches.md).

See [Secrets Strategy](docs/secrets-strategy.md) for full details on Vault and AWS Secrets Manager setup.

### Embedding models

The embedding model is chosen in the admin UI (**Settings**), not in `.env`. The choice is saved in the `app_settings` table and takes effect without a restart. You only need the key for the provider you pick:

| Provider | Models | Vector dims | Key / config |
|---|---|---|---|
| Voyage AI (default) | `voyage-3`, `voyage-3-large`, `voyage-code-3`, `voyage-finance-2` | 1024 | `VOYAGE_API_KEY` in `backend/.env` (env only) |
| | `voyage-3-lite` | 512 | 〃 |
| OpenAI | `text-embedding-3-small` | 1536 | Settings UI, falls back to `OPENAI_API_KEY` |
| RexBERT | `rexbert-micro`, `rexbert-mini` | 128 / 256 | Endpoint + optional key in Settings UI; can also be set per index |
| Custom | Any OpenAI-compatible endpoint | user-defined | Endpoint, key, model name and dimension in Settings UI |

**Example — using the default Voyage model.** Get a key from the [Voyage AI dashboard](https://dash.voyageai.com/) and add it to `backend/.env`, then restart the API (`docker-compose up -d isp-api`):

```env
# backend/.env
VOYAGE_API_KEY=pa-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   # placeholder — use your own key
```

For any other provider, leave `VOYAGE_API_KEY` blank and pick the model and enter its key in **Settings** instead.

- **No key = lexical only.** If the active provider has no key, embeddings are skipped and search falls back to lexical. Since Voyage is the default, a fresh install without `VOYAGE_API_KEY` runs lexical-only until you add the key or switch models.
- **Changing dimension requires a rebuild.** Typesense collections are created with a fixed vector size, so switching between models with different dims (e.g. `voyage-3` 1024 → `text-embedding-3-small` 1536) breaks semantic search on existing indexes until each is fully rebuilt. The settings API flags this with `dim_changed: true`.

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

## Telemetry

ISP sends an anonymous ping when the API starts and once a day after that, so the project can see how many installs are active, which providers people use, and which organisations' networks they run on. It is **on by default** and easy to turn off.

**Exactly what ISP sends** — nothing else:

| Field | Example |
|---|---|
| `event` | `platform_start` or `heartbeat` |
| `install_id` | random UUID generated on first start (stored in your own Postgres) |
| `version` | `0.1.0` |
| `os`, `arch`, `python` | `linux`, `arm64`, `3.12` |
| `typesense_version` | `30.2` |
| `embedding_provider`, `chat_provider` | `voyage`, `groq` — provider type only, never keys |
| `index_count_bucket` | `0`, `1-10`, `11-50`, `50+` |

**Added by the collector:** the collector ([`telemetry-worker/`](telemetry-worker/), a Cloudflare Worker) sees your IP address like any HTTP request. It does **not** store the IP, but it stores what Cloudflare derives from it, next to your `install_id`:

| Stored field | Example |
|---|---|
| `org` | network owner, e.g. `T-Mobile USA, Inc.` or `Comcast Cable` |
| `asn` | that network's ASN, e.g. `21928` |
| `country` | `US` |
| `received_at` | ping timestamp |

**Never sent or stored:** documents, queries, index names, URLs, hostnames, API keys, emails, or the IP address itself.

**Turn it off** with either of these in `backend/.env`, then restart the API:

```env
TELEMETRY_ENABLED=false
# or the cross-tool standard:
DO_NOT_TRACK=1
```

Telemetry is also skipped automatically under CI and tests. The API log states on every start whether it is on or off. Implementation: [`telemetry.py`](backend/src/isp/services/infra/telemetry.py).

---

## Docs

### Architecture & codebase
- [Application Overview](docs/01-application-overview.md)
- [Typesense Integration](docs/02-typesense-integration.md)
- [Database Schema](docs/03-database.md)
- [Codebase Structure](docs/04-codebase.md)
- [Component Integrations](docs/05-integrations.md)
- [Backend Architecture](docs/backend_architecture.md)
- [Frontend Data Layer Architecture](docs/data-layer-architecture2.md)

### Features
- [Score Boost Reference](docs/scoreBoost.md)
- [Tab Intent Detection](docs/tabIntent.md)
- [Analytics-Driven Learning](docs/analyticsDataLearning.md)
- [Sitemap Indexing](docs/sitemapIndexingDetails.md)
- [Google Sheets Connector](docs/gsheet-connector.md)

### Operations & security
- [Secrets Strategy](docs/secrets-strategy.md) — `SECRET_PROVIDER=env|vault|aws`, Vault ↔ AWS migration
- [Auth Approaches](docs/auth-approaches.md) — OAuth2 / OIDC SSO implemented (Approach 3); set `AUTH_ENABLED=true` to activate
- [Deploy to AWS EC2](docs/deploy-aws-ec2.md)
- [AWS EC2 + Docker Compose](docs/aws-EC2-Docker-Compose.md)

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
