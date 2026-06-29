# ISP Code Walkthrough

A top-to-bottom guide to how the Intelligent Search Platform is structured, how data flows through it, and where to find each piece of logic.

---

## Stack at a glance

| Layer | Technology | Port |
|---|---|---|
| Admin UI | Astro 4 + Svelte 4 + TypeScript | 3001 |
| API | FastAPI + asyncpg (Python 3.12) | 8001 |
| Search engine | Typesense 0.25 | 8108 |
| Relational DB | PostgreSQL 16 | 5432 |
| Embedding cache | Redis 7 | 6379 |
| Secrets store | HashiCorp Vault (local) / AWS Secrets Manager (prod) | 8200 |

Everything runs in Docker Compose on a shared `isp-net` network. The UI container proxies `/api/*` → `isp-api:8001` so the browser only ever talks to one origin.

---

## Repository layout

```
intelligent-search-platform/
├── backend/
│   └── src/isp/
│       ├── auth/              # OAuth2 middleware, JWT utils, provider adapters
│       ├── models/            # Pydantic request/response models
│       ├── routers/           # FastAPI route handlers (one file per domain)
│       ├── services/
│       │   ├── domain/        # Business logic (index, extension, analytics, settings)
│       │   └── infra/         # External integrations (DB, Typesense, Redis, connectors)
│       ├── config.py          # All settings via pydantic-settings
│       └── main.py            # App entry point, lifespan, middleware registration
├── frontend/
│   └── src/
│       ├── components/        # Svelte UI components
│       ├── lib/
│       │   ├── api.ts         # Typed HTTP client
│       │   ├── repository/    # Raw API calls per domain
│       │   ├── services/      # Domain mapping (API DTO → app model)
│       │   ├── stores/        # Svelte writable stores (global state)
│       │   └── types/         # TypeScript interfaces
│       └── pages/index.astro  # Single entry page, mounts <App>
├── docs/                      # All documentation
├── vault/                     # Vault config and entrypoint for local secrets
├── docker-compose.yml
└── deploy-*.sh                # Per-service deploy scripts
```

---

## Backend

### Startup sequence (`main.py`)

`main.py` is the single entry point. On every startup, before the first request is served, the FastAPI lifespan handler runs these steps in order:

```
1. bootstrap_secrets()          — fetch secrets from Vault/AWS/env and inject into os.environ
2. job_lock.release_stale()     — clear any Redis locks left by a prior container crash
3. init_db()                    — CREATE TABLE IF NOT EXISTS for all PostgreSQL tables
4. seed_slugs()                 — back-fill human-readable slugs for old indexes
5. load_embed_model()           — restore the operator-chosen embedding model from DB
6. _ensure_catalog_collection() — create the shared Typesense catalog collection if missing
7. ts.migrate_isp_fields()      — add any new fields to existing ISP collections
8. _seed_extensions_from_files()— scan backend/extensions/*.js and upsert into DB
```

After lifespan completes, CORS middleware is applied, `AuthMiddleware` is conditionally registered, and all routers are mounted.

### Config (`config.py`)

All settings are in one `Settings` class backed by pydantic-settings. Every setting has a safe default so the app boots with only a `.env` pointing at Docker Compose services. Auth, embedding provider, Redis, and connector credentials are all controlled here.

### Routers

Each file in `routers/` is a FastAPI `APIRouter` mounted at `/v1` in `main.py`:

| Router | Prefix | Responsibility |
|---|---|---|
| `indexes.py` | `/v1/indexes` | Create, list, delete indexes; trigger rebuild/ingest |
| `search.py` | `/v1/indexes/{id}/search` | Hybrid search (lexical + semantic) |
| `catalog.py` | `/v1/catalog` | Unified catalog search for PLP |
| `content.py` | `/v1/indexes/{id}/documents` | Document listing and detail |
| `extensions.py` | `/v1/extensions` | JS extension CRUD and index assignment |
| `analytics.py` | `/v1/analytics` | Impression and click event ingestion |
| `settings.py` | `/v1/settings` | Embedding model, API keys, custom config |
| `auth.py` | `/auth` | OAuth2 login, callback, logout, `/auth/me` |
| `api_keys.py` | `/v1/indexes/{id}/api-keys` | Per-index API key management |

Routers delegate all business logic to `services/domain/`. They never touch the database or Typesense directly.

### Services — domain layer (`services/domain/`)

| File | What it owns |
|---|---|
| `index_service.py` | Index CRUD, slug generation, ingest state, Typesense collection lifecycle |
| `extension_service.py` | Extension upsert, index assignment, enable/disable |
| `analytics_service.py` | Search event storage and click-through rate queries |
| `settings_service.py` | Embedding model selection, custom endpoint config, persistence in `app_settings` |

### Services — infrastructure layer (`services/infra/`)

| File | What it owns |
|---|---|
| `database.py` | asyncpg pool (lazy singleton), `init_db()`, all SQL schemas |
| `typesense_client.py` | Typesense client (lazy singleton), collection create/delete, search, upsert, migrate |
| `embedding_service.py` | Multi-provider embedding (Voyage AI, OpenAI, RexBERT, custom), two-tier cache |
| `extension_runner.py` | V8 JS sandbox via PyMiniRacer; applies extension chain to document batches |
| `secrets.py` | Provider-based secret abstraction (env / Vault / AWS) |
| `job_lock.py` | Redis-backed distributed lock preventing concurrent rebuilds on the same index |
| `rest_connector.py` | Fetches documents from a REST API endpoint |
| `sitemap_connector.py` | Crawls a sitemap XML and fetches page content |
| `gsheet_connector.py` | Reads rows from a Google Sheet via the Sheets API |

---

## Data model (PostgreSQL)

All tables are created idempotently by `init_db()`. No migration tool — new columns are added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

| Table | Purpose |
|---|---|
| `indexes` | One row per ISP source — name, connector config (JSONB), Typesense collection name, external source flags |
| `ingest_status` | Live progress for the running rebuild job (state, total, indexed, error) |
| `extensions` | JS extension code and metadata |
| `index_extensions` | Many-to-many join: which extensions are assigned to which index, with `run_order` and `enabled` |
| `app_settings` | Key-value store for operator settings (active embedding model, custom endpoint, etc.) |
| `search_events` | Every search impression and click event (used for analytics and future learning) |
| `api_keys` | Per-index API key hashes (SHA-256, never plaintext) |
| `deleted_splits` | Tombstones for ingest split keys — prevents re-indexing deleted document ranges |

---

## Key flows

### 1. Ingest / Rebuild

Triggered by `POST /v1/indexes/{id}/rebuild`. The handler starts a FastAPI `BackgroundTask`:

```
BackgroundTask
  └── connector.fetch_all_documents()        # REST / Sitemap / GSheet
        ↓ raw docs
  └── _inject_isp_type(docs)                 # adds _isp_type field
        ↓
  └── apply_extensions(docs, extensions)     # V8 JS sandbox per doc
        ↓ transformed docs
  └── embedding_service.embed_texts(titles)  # Voyage AI / OpenAI / RexBERT
        ↓ vectors
  └── ts.upsert_batch(collection, docs)      # Typesense upsert
        ↓
  └── ingest_status updated throughout       # progress visible in UI
```

For **external (catalog write-back) indexes**, the same pipeline runs but the final upsert goes to the shared `catalog` Typesense collection instead of a private `isp_{id}` collection. The `_generic_catalog_mapper` extension normalises field names before upsert.

A Redis `job_lock` prevents two rebuild jobs running concurrently on the same index. Locks are auto-released after the job completes or on next startup if the container crashed mid-ingest.

### 2. Search

`POST /v1/indexes/{id}/search` or `/v1/indexes/{id}/search/public`:

```
Request (q, mode, page, per_page)
  └── _resolve_index(id_or_slug)             # hex id OR human-readable slug
        ↓ IndexMeta
  └── embedding_service.embed_query(q)       # L1 LRU cache → L2 Redis → Voyage/OpenAI API
        ↓ vec (1024-dim float[])
  └── ts.search_with_vec(collection, q, vec) # Typesense hybrid: BM25 + ANN via RRF
        ↓ results
  └── version_boost applied if enabled       # adds recency score to _text_match_score
        ↓
Response (hits, total, page)
```

For **external indexes**, `ts.search_external` scopes the same RRF query to the index's filter expression (e.g. `type:=device`) within the shared catalog collection.

**Mode options:**
- `hybrid` (default) — BM25 + semantic vector search, merged via Reciprocal Rank Fusion
- `semantic` — vector-only
- `lexical` — BM25 only, no embedding call

### 3. JS Extensions

Extensions are JavaScript functions that transform documents before they are indexed. They run inside a V8 isolate (PyMiniRacer) — no filesystem, no network, no Node.js APIs.

```
// Example extension structure
// @name Catalog Mapper
// @description Normalise fields for catalog write-back
// @assign external
// @run_order 10

function transform(doc) {
  doc.display = doc.title || doc.name;
  doc.popularity = doc.salesRank || 0;
  return doc;
}
```

Annotations in the leading comment block control auto-assignment (`@assign external|all`) and execution order (`@run_order N`). Extensions in `backend/extensions/*.js` are seeded into the DB on startup — committing a new `.js` file is all that is needed to deploy an extension.

### 4. Embedding cache

Two-tier cache in `embedding_service.py`:

```
embed_query(q)
  └── L1: LRUCache (2048 slots, in-process)   — zero latency, per-pod
        ↓ miss
  └── L2: Redis (24h TTL)                      — shared across pods, survives restarts
        ↓ miss
  └── Provider API (Voyage AI / OpenAI / etc.) — network call
        ↓ vector
  └── write L2 (struct.pack bytes, ~4KB/vec)
  └── write L1
```

Cache keys include the model name so switching models never serves stale vectors.

---

## Frontend

### Data flow (7 layers)

```
Component
  └── Store (Svelte writable)
        └── Service (domain mapping)
              └── Repository (raw API calls)
                    └── api.ts (fetch wrapper + auth headers)
                          └── Backend API
```

### `api.ts`

Single typed HTTP client. All requests go through `req<T>()` which:
- Sets `Content-Type: application/json`
- Attaches `Authorization: Bearer <token>` when `PUBLIC_AUTH_ENABLED=true`
- On 401: clears the stored JWT and redirects to `/auth/login`
- On non-2xx: throws with status + body text

`BASE` is set at Astro build time via `PUBLIC_API_URL` (falls back to `http://localhost:8001/v1`).

### Repositories (`lib/repository/`)

Thin wrappers around `api.ts`. One file per domain (indexes, extensions, settings). No data transformation — returns raw API DTOs.

### Services (`lib/services/`)

Map API DTOs to typed app models. Each domain has a `model.ts` (types), `mapper.ts` (DTO → model), and `Service.ts` (orchestration). No UI or state management here.

### Stores (`lib/stores/`)

Svelte writable stores that hold global state. All mutations go through action methods — state is always replaced, never mutated in place.

| Store | State it holds |
|---|---|
| `indexes-store.ts` | Full index list, loading flag, error |
| `extensions-store.ts` | Extension list for the active index |
| `settings-store.ts` | App-wide settings (embed model, API keys) |
| `app-state-store.ts` | Active index id, active panel, UI navigation state |
| `auth-store.ts` | JWT token, expiry-aware read/write to localStorage |
| `theme-store.ts` | Light / dark theme preference (persisted to localStorage) |

### Key components (`src/components/`)

| Component | Role |
|---|---|
| `layout/App.svelte` | Root shell — sidebar + main panel layout |
| `layout/Header.svelte` | Top bar with index selector and nav |
| `sources/Dashboard.svelte` | Index list overview |
| `sources/IndexDetail.svelte` | Detail view for one index (tabs: Search, Documents, Rebuild, Connector, Extensions) |
| `sources/RebuildPanel.svelte` | Connector config form + rebuild trigger + live progress |
| `sources/SearchPanel.svelte` | Admin search UI for testing queries |
| `sources/DocumentTable.svelte` | Paginated document browser |
| `extensions/ExtensionsPanel.svelte` | Extension library and per-index assignment |
| `settings/SettingsPanel.svelte` | Embedding model selector, API key config |
| `auth/LoginGate.svelte` | Wraps the whole app; transparent when auth is off |

---

## Auth

See [`docs/auth-approach.md`](auth-approach.md) for the full reference.

Short version: `AuthMiddleware` sits in front of all `/v1/*` admin routes when `AUTH_ENABLED=true`. PLP-facing routes (`/catalog`, `/analytics`, `/intent`, `/search/public`) are always open. The OAuth2 flow issues an HS256 JWT stored in `localStorage` and attached as `Bearer` on every API call.

---

## Secrets

See [`docs/secrets-strategy.md`](secrets-strategy.md) for the full reference.

Short version: `bootstrap_secrets()` runs before any ISP module loads `config.py`. It fetches all `_SECRET_KEYS` from the active provider (`env` / `vault` / `aws`) and injects them into `os.environ`. Everything else reads from `os.environ` as normal — no code changes between environments.

---

## Connectors

Three connector types, all returning `list[dict]` that feed the same ingest pipeline:

| Connector | Config fields | How it fetches |
|---|---|---|
| `rest_connector.py` | `url`, `method`, `body`, `headers`, `items_path`, `pagination` | HTTP GET/POST, JSONPath item extraction, optional cursor/offset pagination |
| `sitemap_connector.py` | `sitemap_url`, `include_patterns`, `exclude_patterns`, `user_agent`, `max_pages` | Parses sitemap XML, fetches each URL, extracts meta title/description/content |
| `gsheet_connector.py` | `spreadsheet_id`, `sheet_name`, `header_row` | Google Sheets API v4 via service account JSON |

---

## Ports reference

| Service | Local port | Internal host |
|---|---|---|
| Admin UI | 3001 | isp-ui:80 |
| API | 8001 | isp-api:8001 |
| Typesense | 8108 | typesense:8108 |
| PostgreSQL | 5432 | isp-db:5432 |
| Redis | 6379 | isp-redis:6379 |
| Vault | 8200 | isp-vault:8200 |

---

## References

> Citations below are sourced from training knowledge (not live browsing). DOI and arXiv links are provided where known.

1. **Fowler, M.** (2002). *Patterns of Enterprise Application Architecture*. Addison-Wesley. ISBN: 0-321-12742-0 — Service Layer, Repository, and Data Mapper patterns; the five backend layers (Config → Models → Router → Domain Service → Infra Service) and the seven frontend layers (Types → Repository → Mapper → Service → Store → Component → Helper) both follow Fowler's layered architecture.

2. **Cormack, G. V., Clarke, C. L. A., & Buettcher, S.** (2009). "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods." *Proceedings of SIGIR 2009*, pp. 758–759. DOI: [10.1145/1571941.1572114](https://doi.org/10.1145/1571941.1572114) — RRF (K=60) used in `search_with_vec` for hybrid BM25 + ANN ranking.

3. **Robertson, S., & Zaragoza, H.** (2009). "The Probabilistic Relevance Framework: BM25 and Beyond." *Foundations and Trends in Information Retrieval*, 3(4), 333–389. DOI: [10.1561/1500000019](https://doi.org/10.1561/1500000019) — BM25 is Typesense's default full-text ranking function, used as the 0.7-weight leg of hybrid search.

4. **Devlin, J., Chang, M.-W., Lee, K., & Toutanova, K.** (2019). "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding." *Proceedings of NAACL-HLT 2019*. arXiv: [1810.04805](https://arxiv.org/abs/1810.04805) — Transformer architecture underlying Voyage AI voyage-3 embeddings (1024-dim) used for semantic ANN retrieval.

5. **Hardt, D. (Ed.)** (2012). "The OAuth 2.0 Authorization Framework." *RFC 6749*, IETF. DOI: [10.17487/RFC6749](https://doi.org/10.17487/RFC6749) — Authorization Code flow used by `AuthMiddleware` + `/auth/*` endpoints; HS256 JWT issued as session token.

6. **Kleppmann, M.** (2017). *Designing Data-Intensive Applications*. O'Reilly. ISBN: 978-1-449-37332-0 — Distributed locks (Chapter 8) and single-writer consistency (Chapter 9); ISP's Redis `SET NX EX` job lock prevents concurrent rebuilds across multiple API pods.

7. **Merkel, D.** (2014). "Docker: Lightweight Linux Containers for Consistent Development and Deployment." *Linux Journal*, 2014(239). ISSN: 1075-3583 — Docker containers run all six ISP services (`isp-api`, `isp-ui`, `isp-db`, `typesense`, `isp-redis`, `isp-vault`) on the shared `isp-net` Docker network.

8. **Jones, M., Bradley, J., & Sakimura, N.** (2015). "JSON Web Token (JWT)." *RFC 7519*, IETF. DOI: [10.17487/RFC7519](https://doi.org/10.17487/RFC7519) — JWT format; `auth-store.ts` stores the 8-hour HS256 JWT in `localStorage` and attaches it as `Authorization: Bearer <token>` on every API call.
