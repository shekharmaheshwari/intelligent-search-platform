# Intelligent Search Platform — Full Codebase Documentation

> **Scope** — This document is the single authoritative reference for the ISP codebase.
> It covers architecture, data flow, every backend module and service, every frontend
> component, the extension system, environment configuration, the database schema,
> and deployment.  Generated from the live source on 2026-06-06.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Directory Structure](#3-directory-structure)
4. [Quick Start](#4-quick-start)
5. [Environment Variables](#5-environment-variables)
6. [Database Schema](#6-database-schema)
7. [Backend — Application Entry Point](#7-backend--application-entry-point)
8. [Backend — Configuration](#8-backend--configuration)
9. [Backend — Data Models](#9-backend--data-models)
10. [Backend — Routers (API Endpoints)](#10-backend--routers-api-endpoints)
    - [10.1 Indexes Router](#101-indexes-router-v1indexes)
    - [10.2 Catalog Router](#102-catalog-router-v1catalog)
    - [10.3 Content Router](#103-content-router-v1content)
    - [10.4 Extensions Router](#104-extensions-router-v1extensions)
    - [10.5 Search Router](#105-search-router)
11. [Backend — Services](#11-backend--services)
    - [11.1 Database Service](#111-database-service)
    - [11.2 Typesense Client](#112-typesense-client)
    - [11.3 Embedding Service](#113-embedding-service)
    - [11.4 Index Service](#114-index-service)
    - [11.5 REST Connector](#115-rest-connector)
    - [11.6 Sitemap Connector](#116-sitemap-connector)
    - [11.7 Extension Service](#117-extension-service)
    - [11.8 Extension Runner](#118-extension-runner)
12. [Backend — JS Extension Files](#12-backend--js-extension-files)
13. [Frontend — Overview](#13-frontend--overview)
14. [Frontend — TypeScript Library](#14-frontend--typescript-library)
    - [14.1 Types](#141-types-libtypests)
    - [14.2 Stores](#142-stores-libstorets)
    - [14.3 API Client](#143-api-client-libapits)
15. [Frontend — Svelte Components](#15-frontend--svelte-components)
    - [15.1 App.svelte](#151-appsvelte)
    - [15.2 Header.svelte](#152-headersvelte)
    - [15.3 SourcesSidebar.svelte](#153-sourcessidebarsvelte)
    - [15.4 SourceContent.svelte](#154-sourcecontentsvelte)
    - [15.5 CreateWizard.svelte](#155-createwizardsvelte)
    - [15.6 RebuildPanel.svelte](#156-rebuildpanelsvelte)
    - [15.7 IndexDetail.svelte](#157-indexdetailsvelte)
    - [15.8 SearchPanel.svelte](#158-searchpanelsvelte)
    - [15.9 DocumentTable.svelte](#159-documenttablesvelte)
    - [15.10 ApiPanel.svelte](#1510-apipanelsvelte)
    - [15.11 ExtensionsPanel.svelte](#1511-extensionspanelsvelte)
    - [15.12 IndexExtensionsPanel.svelte](#1512-indexextensionspanelsvelte)
    - [15.13 Dashboard.svelte](#1513-dashboardsvelte)
    - [15.14 IndexCard.svelte](#1514-indexcardsvelte)
16. [Data Flows](#16-data-flows)
    - [16.1 Ingest (REST)](#161-ingest-rest)
    - [16.2 Ingest (Sitemap)](#162-ingest-sitemap)
    - [16.3 Rebuild with Extension Pipeline](#163-rebuild-with-extension-pipeline)
    - [16.4 Hybrid Search](#164-hybrid-search)
17. [Extension System](#17-extension-system)
18. [API Reference](#18-api-reference)
19. [Deployment](#19-deployment)
20. [Dependency Reference](#20-dependency-reference)

---

## 1. Project Overview

**Intelligent Search Platform (ISP)** is a self-contained search infrastructure that:

- Indexes content from **REST APIs** and **sitemap-crawled web pages** into [Typesense](https://typesense.org).
- Generates **1024-dimensional embeddings** via [Voyage AI](https://www.voyageai.com) for semantic search.
- Serves **hybrid search** (lexical BM25 + semantic cosine similarity, merged with Reciprocal Rank Fusion).
- Exposes a **management UI** built with Astro + Svelte for adding sources, triggering rebuilds, and browsing indexed content.
- Runs a **JavaScript V8 extension pipeline** (via PyMiniRacer) that transforms raw documents before indexing — enabling field normalisation, URL construction, price extraction, and other enrichment without modifying the ingestion core.
- Powers the **Product Listing Page (PLP)** product search via a dedicated `/v1/catalog` API that dynamically searches all `isp_*` collections (rather than a single shared `catalog` collection), merging results with Reciprocal Rank Fusion and applying editorial score boosts for featured / high-popularity items.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser / PLP Frontend                                         │
│  ┌──────────────┐   ┌──────────────────────────────────────┐   │
│  │  ISP Mgmt UI │   │  PLP (React, tmo-plp)                │   │
│  │  port 3001   │   │  port 3000                           │   │
│  └──────┬───────┘   └──────────────────┬─────────────────-─┘   │
└─────────┼──────────────────────────────┼───────────────────────┘
          │ REST /v1/*                   │ REST /v1/catalog/*
          ▼                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  isp-api  (FastAPI, port 8001)                                  │
│                                                                 │
│  Routers:  indexes | catalog | content | extensions | search    │
│  Services: index_service | typesense_client | embedding_service │
│            rest_connector | sitemap_connector                   │
│            extension_service | extension_runner                 │
└──────────┬──────────────────┬──────────────────┬───────────────┘
           │                  │                  │
           ▼                  ▼                  ▼
   ┌───────────────┐  ┌──────────────┐  ┌──────────────────┐
   │  PostgreSQL   │  │  Typesense   │  │  Redis           │
   │  port 5432    │  │  port 8108   │  │  port 6379       │
   │               │  │              │  │                  │
   │  indexes      │  │  isp_{id}    │  │  Embedding L2    │
   │  ingest_status│  │  (one per    │  │  cache (24h TTL) │
   │  extensions   │  │   source)    │  │                  │
   │  index_ext.   │  │              │  │                  │
   └───────────────┘  └──────────────┘  └──────────────────┘
```

**Key design decisions:**

| Decision | Rationale |
|---|---|
| Typesense as the search engine | Single binary, no JVM, fast BM25 + vector search |
| asyncpg (no ORM) | Minimal overhead; all queries are hand-written SQL |
| PyMiniRacer for JS transforms | V8 isolation without Node.js; extensions are sandboxed ECMAScript |
| Two-tier embedding cache (LRU → Redis) | Avoid Voyage AI round-trips for repeated queries; Redis shared across pods |
| RRF for hybrid ranking | Simple, parameter-free fusion; lexical 0.7 + semantic 0.3 weights |
| `metadata` as JSON string in Typesense | Typesense cannot index nested objects as fields; round-tripped via JSON |
| Connector config persisted before ingest starts | Rebuild panel can pre-populate form even mid-ingest |
| `ON CONFLICT DO NOTHING` on extension seed | Startup seeding never overrides user-configured assignments |

---

## 3. Directory Structure

```
intelligent-search-platform/
│
├── backend/
│   ├── src/isp/
│   │   ├── __init__.py
│   │   ├── config.py              # pydantic-settings Settings class
│   │   ├── main.py                # FastAPI app, lifespan, startup seed
│   │   ├── models.py              # Pydantic request/response models
│   │   ├── routers/
│   │   │   ├── catalog.py         # /v1/catalog — PLP product search
│   │   │   ├── content.py         # /v1/content — generic public content API
│   │   │   ├── extensions.py      # /v1/extensions — JS extension CRUD
│   │   │   ├── indexes.py         # /v1/indexes — source CRUD, ingest, rebuild
│   │   │   └── search.py          # /v1/indexes/{id}/search
│   │   └── services/
│   │       ├── database.py        # asyncpg pool, init_db (schema creation)
│   │       ├── embedding_service.py  # Voyage AI + LRU + Redis embed cache
│   │       ├── extension_runner.py   # PyMiniRacer V8 execution engine
│   │       ├── extension_service.py  # PostgreSQL CRUD for extensions tables
│   │       ├── index_service.py      # PostgreSQL CRUD for indexes table
│   │       ├── rest_connector.py     # REST API fetch + field mapping
│   │       ├── sitemap_connector.py  # Sitemap crawl + HTML extraction
│   │       └── typesense_client.py   # Typesense SDK wrapper
│   ├── extensions/
│   │   └── catalog_mapper.js      # field normalisation transform
│   ├── .env                       # Local dev env vars (not committed)
│   ├── Dockerfile
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── components/            # 14 Svelte UI components (see §15)
│   │   ├── lib/
│   │   │   ├── api.ts             # Typed HTTP client for the backend API
│   │   │   ├── store.ts           # Global Svelte stores
│   │   │   └── types.ts           # TypeScript interfaces mirroring backend models
│   │   └── pages/
│   │       └── index.astro        # Single-page Astro entry point
│   ├── astro.config.mjs
│   ├── package.json
│   ├── tsconfig.json
│   └── Dockerfile
│
├── docs/                          # Additional topic docs
│   ├── 01-application-overview.md
│   ├── 02-typesense-integration.md
│   ├── 03-database.md
│   ├── 04-codebase.md
│   └── 05-integrations.md
│
├── docker-compose.yml             # Full local stack
├── README.md
├── deploy-all.sh                  # Rebuild + restart all containers
├── deploy-api.sh                  # Rebuild + restart backend only
├── deploy-ui.sh                   # Rebuild + restart frontend only
└── deploy-db.sh                   # Start + wait for PostgreSQL
```

---

## 4. Quick Start

**Prerequisites:** Docker, Docker Compose, a [Voyage AI](https://www.voyageai.com) API key.

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd intelligent-search-platform

# 2. Create backend env file
cp backend/.env.example backend/.env
# Edit backend/.env and set VOYAGE_API_KEY=<your-key>

# 3. Start the full stack
docker compose up -d

# 4. Verify services
curl http://localhost:8001/health      # {"ok": true}
open http://localhost:3001             # Management UI
open http://localhost:8001/docs        # Interactive API docs (Swagger)
```

**Service ports:**

| Service | Port | Purpose |
|---|---|---|
| isp-ui | 3001 | Svelte management UI (nginx) |
| isp-api | 8001 | FastAPI backend |
| isp-db | 5432 | PostgreSQL |
| typesense | 8108 | Search engine |
| isp-redis | 6379 | Embedding cache |

---

## 5. Environment Variables

All variables are read by `backend/src/isp/config.py` via pydantic-settings.
They can be set in `backend/.env` or passed as real environment variables.

| Variable | Default | Required | Description |
|---|---|---|---|
| `TYPESENSE_HOST` | `typesense` | No | Typesense hostname |
| `TYPESENSE_PORT` | `8108` | No | Typesense port |
| `TYPESENSE_API_KEY` | `local-dev-key-change-me` | **Yes (prod)** | Typesense API key |
| `DATABASE_URL` | `postgresql://isp:isp-dev-password@isp-db:5432/isp` | No | PostgreSQL DSN |
| `VOYAGE_API_KEY` | `""` | **Yes** | Voyage AI API key for embeddings |
| `REDIS_URL` | `redis://isp-redis:6379/0` | No | Redis DSN for L2 embed cache |
| `EMBED_CACHE_TTL` | `86400` | No | Redis embedding cache TTL in seconds (24h) |
| `EMBED_LRU_SIZE` | `2048` | No | L1 in-process LRU cache slots per pod |
| `EXTENSIONS_DIR` | `/app/extensions` | No | Directory scanned for `*.js` extension files at startup |
| `HOST` | `0.0.0.0` | No | Uvicorn bind host |
| `PORT` | `8001` | No | Uvicorn bind port |

**Frontend build arg:**

| Arg | Default | Description |
|---|---|---|
| `PUBLIC_API_URL` | `http://localhost:8001/v1` | Backend base URL baked in at Astro build time |

---

## 6. Database Schema

PostgreSQL (v16), managed by `init_db()` using `CREATE TABLE IF NOT EXISTS` — no external migration tool.

### `indexes`
Stores all ISP sources (both ISP-managed and external Typesense collections).

| Column | Type | Description |
|---|---|---|
| `id` | VARCHAR(12) PK | Random short ID generated on creation |
| `name` | VARCHAR(80) | Human-readable source name |
| `description` | TEXT | Optional description |
| `created_at` | VARCHAR(30) | ISO 8601 creation timestamp |
| `doc_count` | INTEGER | Cached document count (updated after ingest) |
| `last_ingested` | VARCHAR(30) | ISO 8601 timestamp of last successful ingest |
| `status` | VARCHAR(20) | `ready` \| `ingesting` \| `error` |
| `connectors` | JSONB | Array of connector config objects |
| `collection_name` | VARCHAR(200) | Typesense collection name (`isp_{id}` for managed) |
| `is_external` | BOOLEAN | True for pre-existing Typesense collections |
| `external_collection` | VARCHAR(200) | Typesense collection name for external sources |
| `external_filter` | VARCHAR(500) | Typesense filter string (e.g. `type:=device`) |
| `external_query_by` | VARCHAR(100) | Field(s) to search by in external collection |
| `external_title_field` | VARCHAR(100) | Field used as document title in UI |
| `external_url_field` | TEXT | Field containing the document URL |

### `ingest_status`
One row per index, tracks the current or last background job.

| Column | Type | Description |
|---|---|---|
| `index_id` | VARCHAR(12) PK FK→indexes | References `indexes.id` |
| `state` | VARCHAR(20) | `idle` \| `queued` \| `running` \| `done` \| `error` |
| `total` | INTEGER | Total documents expected |
| `indexed` | INTEGER | Documents indexed so far |
| `error` | TEXT | Error message on failure |

### `deleted_splits`
Tracks soft-deleted external sub-sources so they are not re-discovered.

| Column | Type | Description |
|---|---|---|
| `split_key` | TEXT PK | Unique identifier for the deleted sub-source |

### `extensions`
Global library of reusable JavaScript transform functions.

| Column | Type | Description |
|---|---|---|
| `id` | VARCHAR(12) PK | Random short ID |
| `name` | VARCHAR(120) | Human-readable extension name |
| `description` | TEXT | Optional description |
| `code` | TEXT | Full JavaScript source (must define `function transform(doc)`) |
| `created_at` | VARCHAR(30) | ISO 8601 creation timestamp |
| `updated_at` | VARCHAR(30) | ISO 8601 last-update timestamp |

### `index_extensions`
Many-to-many join: which extensions are assigned to which indexes, in what order.

| Column | Type | Description |
|---|---|---|
| `index_id` | VARCHAR(12) PK FK→indexes | Cascade-deletes when index is deleted |
| `extension_id` | VARCHAR(12) PK FK→extensions | Cascade-deletes when extension is deleted |
| `run_order` | INTEGER | Execution order — lower numbers run first |
| `enabled` | BOOLEAN | If false, extension is skipped without being removed |

---

## 7. Backend — Application Entry Point

**File:** `backend/src/isp/main.py`

The FastAPI app is created once, routers are registered, and a lifespan context manager runs the startup sequence before any requests are served.

### Startup Sequence

```
lifespan() start
    │
    ├── init_db()                    # Create all tables (idempotent)
    ├── _ensure_catalog_collection() # Provision `catalog` Typesense collection
    └── _seed_extensions_from_files()
            │
            ├── Scan EXTENSIONS_DIR for *.js files
            ├── Parse // @name and // @description headers
            ├── upsert_extension_by_name() (ON CONFLICT UPDATE code)
            └── For each external index:
                    assign_extension_if_new() (ON CONFLICT DO NOTHING)
```

### Helper Functions

| Function | Signature | Description |
|---|---|---|
| `_ensure_catalog_collection` | `() → None` | Creates the `catalog` Typesense collection with all required fields if it doesn't exist |
| `_parse_js_header` | `(text: str) → tuple[str, str]` | Extracts `// @name` and `// @description` from the first comment block of a JS file |
| `_seed_extensions_from_files` | `() → None` | Scans `EXTENSIONS_DIR`, upserts each JS file as an extension, auto-assigns to all external indexes |
| `lifespan` | `(app: FastAPI) → AsyncContextManager` | FastAPI lifespan — runs startup above, then yields for the app lifetime |
| `health` | `GET /health → {"ok": True}` | Liveness probe; returns 200 as long as the process is running |

---

## 8. Backend — Configuration

**File:** `backend/src/isp/config.py`

Single `Settings` class using pydantic-settings.  Values are read from environment variables (case-insensitive) and the `.env` file.  Instantiated once at module load as `settings = Settings()`.

```python
from isp.config import settings

settings.typesense_host      # "typesense"
settings.voyage_api_key      # ""  — must be set in .env
settings.embed_lru_size      # 2048
```

---

## 9. Backend — Data Models

**File:** `backend/src/isp/models.py`

Pydantic v2 models used as request bodies and response schemas across routers and services.

| Model | Used as | Key Fields |
|---|---|---|
| `RestConnectorConfig` | Request body / JSONB storage | `url`, `method`, `headers`, `body`, `result_path`, `id_field`, `title_field`, `content_field`, `url_field`, `field_map` |
| `SitemapConnectorConfig` | Request body / JSONB storage | `url`, `max_pages`, `content_selector`, `include_pattern`, `exclude_pattern` |
| `IndexCreate` | POST /indexes body | `name`, `description` |
| `ConnectorAdd` | POST /indexes/{id}/connectors/* | `config: RestConnectorConfig \| SitemapConnectorConfig` |
| `IndexMeta` | API response / service return | All `indexes` table columns |
| `IngestStatus` | API response / service return | `index_id`, `state`, `total`, `indexed`, `error` |
| `SearchRequest` | POST /indexes/{id}/search body | `q`, `mode`, `page`, `per_page` |
| `DocumentItem` | Search hit document | `id`, `title`, `content`, `url`, `source_type`, `indexed_at` + any extra fields |

---

## 10. Backend — Routers (API Endpoints)

### 10.1 Indexes Router (`/v1/indexes`)

**File:** `backend/src/isp/routers/indexes.py`

The primary CRUD and ingest surface.  All background jobs (ingest, rebuild) are launched via FastAPI `BackgroundTasks` so the endpoint returns immediately with `state=queued`.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v1/indexes` | List all indexes ordered by creation date |
| POST | `/v1/indexes` | Create a new ISP-managed index; provisions Typesense collection. Pass `is_external: true` with collection details to register an external source |
| POST | `/v1/indexes/discover` | Scan Typesense for untracked collections, register as external sources |
| GET | `/v1/indexes/{id}` | Fetch full metadata for one index |
| DELETE | `/v1/indexes/{id}` | Delete index + all Typesense documents (catalog write-back gets filter-deleted) |
| GET | `/v1/indexes/{id}/status` | Poll background job state |
| POST | `/v1/indexes/{id}/connectors/rest` | Save REST connector config without starting ingest |
| POST | `/v1/indexes/{id}/connectors/sitemap` | Save sitemap connector config without starting crawl |
| POST | `/v1/indexes/{id}/ingest/rest` | Start REST ingest (append, background) |
| POST | `/v1/indexes/{id}/ingest/sitemap` | Start sitemap crawl (append, background) |
| POST | `/v1/indexes/{id}/rebuild` | Drop all docs and re-fetch from connector (background) |
| GET | `/v1/indexes/{id}/documents` | Paginated document list |
| PATCH | `/v1/indexes/{id}/documents/{doc_id}` | Update `popularity` and/or `is_featured` on a single document without re-embedding |
| POST | `/v1/indexes/{id}/search` | Hybrid/lexical/semantic search |
| GET | `/v1/indexes/{id}/extensions` | List extensions assigned to index |
| POST | `/v1/indexes/{id}/extensions` | Assign extension to index |
| DELETE | `/v1/indexes/{id}/extensions/{ext_id}` | Unassign extension from index |

#### Internal Helpers

| Function | Description |
|---|---|
| `_infer_isp_type(cfg_dict)` | Reads `familyCategory` from the connector body JSON and returns `"accessory"` (if the category contains "ACCESSOR"), `"device"`, or `"item"`.  Used to inject `_isp_type` before extensions run. |
| `_ensure_isp_schema(docs)` | Normalises a list of raw or extension-output dicts to the ISP Typesense schema.  Packs every non-schema field into a `metadata` JSON string; promotes `popularity` and `is_featured` to top-level fields so Typesense can sort/boost on them. |
| `_generic_catalog_mapper(item, idx)` | Packages an extension-normalised flat dict into the Typesense `catalog` schema.  Stores `metadata` as a JSON string.  Returns `None` for items with no displayable name. |
| `_map_external_doc(raw, meta)` | Converts a raw Typesense hit to the ISP `DocumentItem` shape for external collections. |
| `_wrap_external_results(resp, meta)` | Maps every hit in a raw Typesense multi-search response to `DocumentItem`. |
| `_run_ingest(index_id, cfg)` | Background task: fetch → parse → `_ensure_isp_schema` → embed → upsert into ISP-managed collection. |
| `_run_rebuild(index_id, cfg)` | Background task: fetch raw items → inject `_isp_type` (via `_infer_isp_type`) → run extensions → `_ensure_isp_schema` → embed → upsert into ISP collection; also writes back to the external `catalog` collection via `_generic_catalog_mapper`. |
| `DocPatchRequest` | Pydantic model for `PATCH /documents/:id`.  Fields: `popularity: int \| None`, `is_featured: bool \| None`. |

---

### 10.2 Catalog Router (`/v1/catalog`)

**File:** `backend/src/isp/routers/catalog.py`

Powers the PLP product search.  Dynamically discovers all active `isp_*` collections and searches them in a single `multi_search` call, merging results with Reciprocal Rank Fusion.  Editorial boosts (`is_featured`, `popularity`) are applied after RRF so curated items surface above pure relevance.

> **Architecture note:** This router no longer reads from a single hardcoded `catalog` collection.  It calls `_get_isp_collections()` on every request to discover all `isp_*` collections and queries them all in parallel.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v1/intent/tab` | Classify query as `device` or `accessory` — keyword heuristic first, then semantic vote across all ISP collections |
| POST | `/v1/catalog` | Hybrid RRF product search across all `isp_*` collections with `source_type` filter, editorial boosts, and pagination |
| GET | `/v1/catalog/facets` | Aggregate manufacturer, category, and price-range facets from `metadata` across all ISP collections |
| GET | `/v1/suggest` | Typeahead autocomplete — searches `title` and `content` across all ISP collections |

#### Internal Helpers

| Function | Description |
|---|---|
| `_get_isp_collections()` | Returns the names of all Typesense collections whose names start with `isp_`. |
| `_parse_meta(raw_meta)` | Deserialises the `metadata` JSON string field; returns `{}` on failure. |
| `_doc_to_item(doc, score)` | Converts a raw ISP Typesense document to the PLP `CatalogItem` response shape; reads rich product data from the `metadata` blob. |
| `_rrf_search(collections, q, vec, filter_by, page, per_page)` | Issues one lexical + one semantic search per ISP collection via a single `multi_search` call.  Merges all results with RRF (lexical 0.7, semantic 0.3), then applies editorial boosts (`is_featured` +0.5, `popularity` scaled to max +0.5 at 1 M), before slicing the requested page. |

---

### 10.3 Content Router (`/v1/content`)

**File:** `backend/src/isp/routers/content.py`

Generic public API that exposes any ISP-managed index via a URL-safe slug derived from the index name.  Consumers do not need to know internal index IDs.

#### Endpoints

| Method | Path | Example |
|---|---|---|
| GET | `/v1/content` | List all sources with their slugs and endpoint URLs |
| GET | `/v1/content/{slug}` | `/v1/content/phones` — paginated document list |
| GET | `/v1/content/{slug}/search?q=...` | `/v1/content/phones/search?q=galaxy` — search |

#### Internal Helpers

| Function | Description |
|---|---|
| `_slug(name)` | Lowercases and replaces spaces/underscores with hyphens (e.g. `"Smart Watches"` → `"smart-watches"`) |
| `_find_by_slug(slug, indexes)` | Finds the `IndexMeta` whose name slugifies to the given slug; raises 404 if not found |

---

### 10.4 Extensions Router (`/v1/extensions`)

**File:** `backend/src/isp/routers/extensions.py`

CRUD for the JS extension library and per-index assignment management.

#### Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v1/extensions` | List all extensions, newest first |
| POST | `/v1/extensions` | Create a new extension (code must define `function transform(doc)`) |
| GET | `/v1/extensions/{id}` | Get single extension |
| PUT | `/v1/extensions/{id}` | Update name, description, and/or code |
| DELETE | `/v1/extensions/{id}` | Delete extension; cascade-removes all index assignments |

---

### 10.5 Search Router

**File:** `backend/src/isp/routers/search.py`

Search endpoint for both ISP-managed and external indexes.  Routes to either `search_with_vec` (hybrid RRF) or `search_documents` (lexical only) based on `mode`.

| Method | Path | Body |
|---|---|---|
| POST | `/v1/indexes/{id}/search` | `{"q": "...", "mode": "hybrid\|lexical\|semantic", "page": 1, "per_page": 10}` |

---

## 11. Backend — Services

### 11.1 Database Service

**File:** `backend/src/isp/services/database.py`

| Function | Description |
|---|---|
| `get_pool()` | Returns the shared `asyncpg.Pool` (min=2, max=10), creating it on first call |
| `init_db()` | Creates all 5 tables using `CREATE TABLE IF NOT EXISTS` — called once from `lifespan` |

---

### 11.2 Typesense Client

**File:** `backend/src/isp/services/typesense_client.py`

Thin wrapper around the Typesense Python SDK.  All search engine I/O goes through this module.  The client is a lazy singleton.

#### ISP-managed collection schema

Every `isp_{index_id}` collection has these fields:

```
id          (string)   — stable hex id derived from source id
title       (string)
content     (string)
url         (string, optional)
source_type (string, facet)  — "device", "accessory", "rest", "sitemap", …
indexed_at  (int64)
metadata    (string, optional, not indexed)  — JSON blob: price, images, manufacturer, …
popularity  (int32,  optional, sort=true)    — editorial boost score; higher = ranked earlier
is_featured (bool,   optional)               — fixed +0.5 score bonus in RRF merge
vec         (float[], num_dim=1024, optional)
```

`metadata` is stored as a serialised JSON string because Typesense cannot index nested objects.  `popularity` and `is_featured` are top-level fields (not inside `metadata`) so Typesense can sort and the catalog router can apply score boosts without parsing the blob.

#### Key functions

| Function | Description |
|---|---|
| `set_vec_dim(dim)` | Update the vector dimension used when provisioning new ISP collections (called when the operator switches embedding model) |
| `get_vec_dim()` | Return the currently configured vector dimension |
| `get_client()` | Returns the lazy singleton Typesense client |
| `collection_name(index_id)` | Returns `"isp_{index_id}"` |
| `create_collection(index_id)` | Provisions a new ISP collection (idempotent). Includes `metadata`, `popularity` (sort=true), and `is_featured` fields in addition to the core schema |
| `drop_collection(index_id)` | Deletes an ISP-managed collection |
| `upsert_documents(index_id, docs)` | Bulk upsert into ISP collection; returns count of successful upserts |
| `count_documents(index_id)` | Returns document count for ISP collection |
| `search_documents(index_id, q, mode, page, per_page)` | Single-pass BM25 lexical or semantic search |
| `search_with_vec(index_id, q, vec, page, per_page)` | Hybrid multi_search + RRF (lexical 0.7, semantic 0.3, 3× over-fetch before slice) |
| `list_documents(index_id, page, per_page)` | Paginated document listing sorted by `indexed_at` descending |
| `list_all_collections()` | Returns all Typesense collections |
| `list_documents_external(collection, filter_by, query_by, page, per_page)` | Paginated listing from any named collection |
| `search_external(collection, q, filter_by, query_by, vec, page, per_page)` | BM25 or hybrid RRF search on any named collection |
| `update_document_fields(col_name, doc_id, fields)` | Partial update via `action=update` — only the supplied keys are overwritten. Returns `True` on success |
| `retrieve_document(col_name, doc_id)` | Fetch a single document by id from any named collection; returns `None` if not found |
| `delete_from_collection(col_name, filter_by)` | Deletes all documents matching a Typesense filter string; returns count deleted |
| `upsert_to_collection(col_name, docs)` | Bulk upsert into any named collection |
| `count_external(col, filter_by)` | Document count on any collection with optional filter |

**RRF implementation detail:**

```
candidates = per_page * 3   # over-fetch to account for reordering
lexical_score  = 0.7 / (rank + 60)
semantic_score = 0.3 / (rank + 60)
final_score    = lexical_score + semantic_score   # deduplicated by id
```

---

### 11.3 Embedding Service

**File:** `backend/src/isp/services/embedding_service.py`

Generates 1024-dimensional vectors via Voyage AI with a two-tier cache to minimise API calls and latency.

#### Cache architecture

```
embed_query("iphone 15")
    │
    ├─ L1: cachetools.LRUCache (2048 slots, in-process per pod)  ──► hit → return
    │        miss ↓
    ├─ L2: Redis (24h TTL, shared across pods)                   ──► hit → promote to L1, return
    │        miss ↓
    └─ Voyage AI API call ──► store in both L1 and L2, return
```

Vectors are serialised as `struct.pack("f" * len(vec), *vec)` (4 bytes × 1024 dims = 4 KB per vector) for compact Redis storage.

#### Asymmetric embedding

Voyage AI uses different `input_type` values for indexing vs retrieval:

| Function | `input_type` | Use case |
|---|---|---|
| `embed_texts(texts)` | `"document"` | Index-time — called during ingest/rebuild |
| `embed_query(text)` | `"query"` | Query-time — called during search |

#### Key functions

| Function | Description |
|---|---|
| `_get_voyage()` | Lazy Voyage AI async client |
| `_get_redis()` | Redis client or `None` if Redis is unavailable |
| `_cache_key(text, input_type)` | SHA-256 hex of `f"{input_type}:{text}"` |
| `_vec_to_bytes(vec)` | `struct.pack("f" * n, *vec)` — 4 bytes per float |
| `_bytes_to_vec(b)` | `list(struct.unpack("f" * (len(b)//4), b))` |
| `_cache_get(key)` | L1 → L2 lookup; Redis hit promotes to L1 |
| `_cache_set(key, vec)` | Write to L1 LRU and L2 Redis atomically |
| `embed_texts(texts)` | Batch embed documents; returns list of float vectors |
| `embed_query(text)` | Embed single query string; cache-first |

---

### 11.4 Index Service

**File:** `backend/src/isp/services/index_service.py`

PostgreSQL CRUD for the `indexes` and `ingest_status` tables, plus external collection discovery.

| Function | Description |
|---|---|
| `_row_to_meta(row)` | Converts raw `asyncpg.Record` to `IndexMeta` Pydantic model |
| `list_indexes()` | Returns all indexes ordered by `created_at` descending |
| `get_index(id)` | Returns single `IndexMeta` or `None` |
| `create_index(name, description)` | Inserts new row; generates 12-char random ID |
| `delete_index(id)` | Deletes from `indexes` and `ingest_status` |
| `add_connector(id, config)` | Appends connector config dict to `connectors` JSONB array |
| `update_index(meta)` | Writes all mutable `IndexMeta` fields back to PostgreSQL |
| `get_status(id)` | Returns `IngestStatus` for an index |
| `set_status(id, state, total, indexed, error)` | Upserts `ingest_status` row |
| `discover_external_collections()` | Scans all Typesense collections; registers untracked ones as external sources; respects `_KNOWN_SPLITS` for sub-collection partitioning |

**`_KNOWN_SPLITS`** maps Typesense collection names to named partitions.  For example, a single `catalog` collection can be surfaced as separate "Phones", "Accessories", "Smartwatches", and "Tablets" sources in ISP by applying different `external_filter` values.

---

### 11.5 REST Connector

**File:** `backend/src/isp/services/rest_connector.py`

Fetches JSON from a REST API endpoint and normalises the response into ISP documents.

#### Two entry points

| Function | Returns | Used by |
|---|---|---|
| `fetch_raw_items(cfg)` | `list[dict]` — raw API items, no field renaming | Catalog write-back rebuild (`_run_rebuild` in indexes router) |
| `fetch_and_parse(cfg)` | `list[dict]` — ISP-normalised docs with `id/title/content/url` | ISP-managed index ingest (`_run_ingest`) |

#### Auto-detection heuristics

When explicit field mappings are not provided in `RestConnectorConfig`:

1. **Result path** (`_auto_detect_path`) — If the top-level response is a dict, find the first key whose value is a non-empty list. Walks two levels deep.
2. **Field names** (`_auto_detect_fields`) — Inspects a sample document and scores candidate fields:
   - `id_field`: prefers keys named `id`, `sku`, `code`, `key`
   - `title_field`: prefers keys named `title`, `name`, `display`, `label`, `heading`
   - `content_field`: prefers keys named `content`, `body`, `description`, `summary`, `text`
   - `url_field`: prefers keys named `url`, `link`, `href`, `permalink`

#### Internal helpers

| Function | Description |
|---|---|
| `_extract_path(data, path)` | Navigate dot-separated path through nested dicts/lists (e.g. `"results.items"`) |
| `_apply_field_map(item, field_map)` | Rename source keys per `field_map`; empty string target drops the key |
| `_doc_from_item(item, fields, idx)` | Builds one `DocumentItem`-compatible dict; ID is MD5 of raw id for URL-safe stable key; returns `None` if both title and content are empty |
| `_do_request(cfg)` | Executes the configured HTTP request via `httpx.AsyncClient`; returns parsed JSON |

---

### 11.6 Sitemap Connector

**File:** `backend/src/isp/services/sitemap_connector.py`

Crawls a sitemap XML, extracts page URLs, then scrapes each page.

#### Configuration fields (`SitemapConnectorConfig`)

| Field | Description |
|---|---|
| `url` | Sitemap XML URL |
| `max_pages` | Maximum pages to crawl (default 100) |
| `content_selector` | Optional CSS selector to scope content extraction (e.g. `article, .post-content`) |
| `include_pattern` | URL substring filter — only crawl URLs that contain this string |
| `exclude_pattern` | URL substring filter — skip URLs that contain this string |

#### Content extraction strategy

For each page, BeautifulSoup removes `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, and `<aside>` elements, then tries selectors in priority order:

```
1. User-supplied content_selector (if set)
2. <main>
3. [role="main"]
4. <article>
5. .post-content, .entry-content, .article-body, .content
6. <body> (fallback)
```

Content is capped at 4,000 characters.  Page IDs are MD5 hashes of the URL for stable upsert behaviour.  A 0.1 s polite delay is inserted between page fetches.

#### Key functions

| Function | Description |
|---|---|
| `_fetch(url)` | Fetches URL with 10 s timeout; returns text or `None` on error |
| `_parse_sitemap_urls(xml)` | Extracts all `<loc>` URLs from sitemap XML string |
| `_extract_page(url, html, selector)` | Parses HTML into an ISP document dict |
| `crawl(cfg)` | Orchestrates the full crawl; returns list of ISP document dicts |

---

### 11.7 Extension Service

**File:** `backend/src/isp/services/extension_service.py`

PostgreSQL CRUD for the `extensions` and `index_extensions` tables.

| Function | Description |
|---|---|
| `list_extensions()` | Returns all extensions, newest first |
| `get_extension(id)` | Returns single extension or `None` |
| `create_extension(name, description, code)` | Inserts new extension; generates 12-char random ID |
| `update_extension(id, name, description, code)` | Partial update — `None` args leave the field unchanged |
| `upsert_extension_by_name(name, description, code)` | Used by startup seed; creates or updates by name match |
| `delete_extension(id)` | Deletes extension; ON DELETE CASCADE removes all `index_extensions` rows |
| `list_index_extensions(index_id)` | Returns extensions assigned to an index, sorted by `run_order` |
| `assign_extension(index_id, ext_id, run_order, enabled)` | Full upsert — creates or replaces assignment |
| `assign_extension_if_new(index_id, ext_id, run_order)` | Uses `ON CONFLICT DO NOTHING` — startup seeding never overrides user config |
| `unassign_extension(index_id, ext_id)` | Removes a single assignment row |

---

### 11.8 Extension Runner

**File:** `backend/src/isp/services/extension_runner.py`

Executes JavaScript `transform(doc)` functions inside V8 isolates via PyMiniRacer.

#### Execution model

```
apply_extensions(index_id, docs)
    │
    └── asyncio.get_event_loop().run_in_executor(None, _run_sync, ...)
            │  (off the asyncio event loop — V8 is synchronous)
            │
            └── For each enabled extension (sorted by run_order):
                    ├── Load JS code into a fresh MiniRacer context (compiled once per batch)
                    └── For each document:
                            ├── ctx.call("transform", doc)
                            ├── On success → update doc in place
                            └── On error → log and keep original doc (per-extension isolation)
```

**Sandbox:** PyMiniRacer gives extensions access only to ECMAScript built-ins (no `fetch`, no `fs`, no `require`).  Each extension gets its own `MiniRacer` context so a crash in one does not affect others.

**Error handling:**

- A JS runtime error in one extension for one document logs the error and moves on — the document is not dropped.
- A disabled extension (`enabled=False`) is completely skipped.

#### Key functions

| Function | Signature | Description |
|---|---|---|
| `_run_sync` | `(extensions, docs) → list[dict]` | Synchronous V8 execution loop; runs in thread executor |
| `apply_extensions` | `async (index_id, docs) → list[dict]` | Fetches assigned extensions from DB, runs `_run_sync` in executor |

---

## 12. Backend — JS Extension Files

**Directory:** `backend/extensions/`

JS files are scanned at startup, seeded into the `extensions` DB table, and auto-assigned to any external indexes discovered in Typesense.

### File header convention

```javascript
// @name    Extension Name
// @description One-line description visible in the UI
```

### `catalog_mapper.js` — Catalog Mapper

Normalises raw REST API product objects into the ISP catalog schema.  Assign to the Phones, Accessories, Smartwatches, and Tablets indexes.

#### `transform(doc)` — field extraction priority

| Output field | Source (priority order) |
|---|---|
| `price` | `doc.price` → `doc.msrp` → `doc.basePrice` → first SKU price fields → first SKU `frpPrice` |
| `images` | `sku.images` where `imgtype ∈ {FrontImage, LeftImage, ThumbNail}`, first 3 SKUs, deduplicated |
| `category` | `doc.category` → `doc.productCategory` → `doc.familyCategory` → `doc.subcategory` |
| `in_stock` | boolean, status object (`status.indexOf("OUT")`), or string |
| `url` | `base + "/" + urlEncodedFamilyName` — base URL is configured per device/accessory type in the extension |
| `type` | `doc._isp_type` (injected by ingest pipeline before extensions run) |
| `tags` | manufacturer + OS + "phone"/"smartphone" (devices) or manufacturer + category (accessories) |
| `popularity` | `max(0, 1000 - rank * 10)` where rank comes from `doc.deviceRank` |
| `is_featured` | `rank <= 5` |

---

## 13. Frontend — Overview

The frontend is an **Astro** application with **Svelte** components and a **TypeScript** lib layer.

- **Astro** handles the build pipeline and serves as the single page container (`pages/index.astro`).
- **Svelte** components own all UI logic and state.
- **Stores** (`lib/store.ts`) hold global state shared across the component tree.
- **API client** (`lib/api.ts`) is the only file that talks to the backend.

There is no client-side router — navigation is state-driven via the `appState` store.

---

## 14. Frontend — TypeScript Library

### 14.1 Types (`lib/types.ts`)

TypeScript interfaces that mirror the backend Pydantic models.

| Interface | Description |
|---|---|
| `IndexMeta` | One ISP source — `is_external: true` for pre-existing Typesense collections.  Includes `external_collection`, `external_filter`, `external_query_by`, `external_title_field`, `external_url_field` for external sources |
| `ConnectorConfig` | Base connector stored in `connectors` JSONB |
| `RestConnectorConfig` | REST connector fields including optional `result_path`, `field_map`, etc. |
| `SitemapConnectorConfig` | Sitemap crawler fields |
| `IngestStatus` | Background job state: `state` is one of `idle \| queued \| running \| done \| error` |
| `SearchResult` | `{ hits: Array<{document: DocumentItem}>, found: number, page: number }` |
| `DocumentItem` | `{ id, title, content, url, source_type, indexed_at, metadata?: string, [key: string]: unknown }` — `metadata` is a JSON string holding rich product data; extra keys pass through for the UI expand view |
| `ExtensionMeta` | `{ id, name, description, code, created_at, updated_at }` |
| `IndexExtension` | Extends `ExtensionMeta` with `run_order: number` and `enabled: boolean` |
| `AppSettings` | `{ active_model, available_models, dim_changed? }` — returned by `GET /settings` |
| `View` | `'dashboard' \| 'detail' \| 'configure' \| 'extensions'` |
| `AppState` | `{ view: View, selectedIndexId: string \| null, detailTab: string }` |

---

### 14.2 Stores (`lib/store.ts`)

Four Svelte writable stores shared across all components.

| Store | Type | Description |
|---|---|---|
| `appState` | `Writable<AppState>` | Current navigation state — which view is active and which index is selected |
| `indexes` | `Writable<IndexMeta[]>` | Full list of ISP sources; refreshed on mount and after create/delete/discover |
| `loading` | `Writable<boolean>` | True while the initial index list is loading |
| `error` | `Writable<string \| null>` | Last global error string; rendered as a toast in `App.svelte` |

---

### 14.3 API Client (`lib/api.ts`)

All HTTP calls go through the `req<T>()` helper which sets `Content-Type: application/json`, throws `Error("${status} ${body}")` on non-2xx, and returns the parsed JSON.

`BASE` defaults to `http://localhost:8001/v1` in development; set `PUBLIC_API_URL` at Astro build time for other environments.

**Note:** `deleteIndex` and `deleteExtension` use raw `fetch` instead of `req` because `204 No Content` responses have no JSON body.

#### Methods

| Method | HTTP | Path | Description |
|---|---|---|---|
| `listIndexes()` | GET | `/indexes` | Fetch all sources |
| `discoverIndexes()` | POST | `/indexes/discover` | Trigger auto-discovery of external collections |
| `createIndex(name, description, opts?)` | POST | `/indexes` | Create new source.  Pass `opts.is_external: true` with collection/filter/field config for an external source |
| `getIndex(id)` | GET | `/indexes/{id}` | Fetch single source metadata |
| `deleteIndex(id)` | DELETE | `/indexes/{id}` | Delete source + all documents |
| `getStatus(id)` | GET | `/indexes/{id}/status` | Poll background job state |
| `ingestRest(id, cfg)` | POST | `/indexes/{id}/ingest/rest` | Start REST ingest (append) |
| `ingestSitemap(id, cfg)` | POST | `/indexes/{id}/ingest/sitemap` | Start sitemap crawl (append) |
| `listDocuments(id, page, perPage)` | GET | `/indexes/{id}/documents` | Paginated document browse |
| `updateDocument(indexId, docId, popularity, isFeatured)` | PATCH | `/indexes/{id}/documents/{docId}` | Update editorial signals without re-embedding |
| `search(id, q, mode, page, perPage)` | POST | `/indexes/{id}/search` | Search (hybrid \| lexical \| semantic) |
| `rebuild(id, cfg)` | POST | `/indexes/{id}/rebuild` | Full drop-and-re-fetch rebuild |
| `listExtensions()` | GET | `/extensions` | Fetch all extensions |
| `createExtension(name, desc, code)` | POST | `/extensions` | Create new extension |
| `updateExtension(id, fields)` | PUT | `/extensions/{id}` | Partial update |
| `deleteExtension(id)` | DELETE | `/extensions/{id}` | Delete extension |
| `listIndexExtensions(indexId)` | GET | `/indexes/{id}/extensions` | List assigned extensions |
| `assignExtension(indexId, extId, runOrder, enabled)` | POST | `/indexes/{id}/extensions` | Assign / update assignment |
| `unassignExtension(indexId, extId)` | DELETE | `/indexes/{id}/extensions/{extId}` | Remove assignment |
| `getSettings()` | GET | `/settings` | Fetch active embedding model and available models |
| `updateSettings(modelId, opts?)` | PUT | `/settings` | Switch embedding model; returns `dim_changed: true` if dimension changed |

---

## 15. Frontend — Svelte Components

Components communicate upward via Svelte `createEventDispatcher`.  `App.svelte` is the root and translates all events into store mutations or API calls.

### Component tree

```
App.svelte
├── Header.svelte
├── SourcesSidebar.svelte
│   └── (dispatches: select, create, delete, extensions)
├── [main area — conditional on appState.view]
│   ├── view='extensions'   → ExtensionsPanel.svelte
│   ├── view='configure'    → IndexDetail.svelte
│   │                           ├── DocumentTable.svelte
│   │                           ├── SearchPanel.svelte
│   │                           └── ApiPanel.svelte
│   └── view='detail'       → SourceContent.svelte
│                               ├── RebuildPanel.svelte (overlay)
│                               └── IndexExtensionsPanel.svelte (overlay)
├── CreateWizard.svelte (overlay, when showWizard=true)
└── Delete confirmation modal (inline)
```

> **Note:** `Dashboard.svelte` and `IndexCard.svelte` exist for the dashboard grid but `App.svelte` currently routes to `SourceContent` for the `detail` view rather than the dashboard grid.

---

### 15.1 App.svelte

Root layout and routing component.  Owns:
- Global index list (`$indexes` store) — loaded on mount, refreshed after mutations.
- Auto-discovery on mount — calls `/discover`, reloads if new sources found.
- Navigation state via `appState` store.
- Delete confirmation modal.
- Mobile sidebar toggle.
- `showWizard` flag for `CreateWizard` overlay.

**Key functions:**

| Function | Description |
|---|---|
| `loadIndexes()` | Fetches all indexes; auto-selects first if none selected |
| `initLoad()` | Startup: `loadIndexes()` then `discoverIndexes()`; reloads if discovery found new sources |
| `selectSource(id)` | Navigates to detail view; hides sidebar on mobile |
| `configure(id)` | Navigates to configure view |
| `backToContent()` | Returns to detail view from configure |
| `deleteSource(id)` | Opens delete confirmation modal |
| `executeDelete()` | Calls `api.deleteIndex`; deselects if needed; reloads list |
| `onWizardDone(id)` | Closes wizard; selects new source; reloads list |

---

### 15.2 Header.svelte

Top navigation bar.  Logo button resets `appState` to `{ view: 'dashboard', selectedIndexId: null }`.

---

### 15.3 SourcesSidebar.svelte

Left navigation panel listing all indexed sources.  Collapsible "Sources" group with per-item selection and delete.  Footer link navigates to the Extensions page.

**Dispatches:** `select(id)`, `create`, `delete(id)`, `extensions`

**Helper functions:**

| Function | Description |
|---|---|
| `connectorUrl(idx)` | Returns the primary connector URL for display; for external sources returns the Typesense collection name |
| `connectorType(idx)` | Returns the connector type badge string (`rest`, `sitemap`, `typesense`) |
| `shortUrl(url)` | Shortens a URL to `hostname + path[:28]` for compact sidebar display |

---

### 15.4 SourceContent.svelte

Main content area for a selected index.  Shows the source header with action buttons (Extensions, Rebuild, Configure) and a searchable, paginated document list.

Reactive to the `index` prop — reloads the document list whenever the selected index changes via a `$:` reactive statement that compares `index.id` to `_loadedIndexId`.

**Dispatches:** `configure(id)`

**Key functions:**

| Function | Description |
|---|---|
| `loadDocs()` | Fetches a page of documents from the backend |
| `search()` | Runs a search query; clears to doc list when query is empty |
| `clearSearch()` | Resets query and reloads the default document list |
| `excerpt(text, max=180)` | Truncates text with ellipsis |
| `fmtDate(ts)` | Formats Unix timestamp to locale date |
| `connectorUrl(idx)` | Returns first connector URL string |
| `toggleExpand(id)` | Toggles the expanded/collapsed state of a document row |
| `docFields(doc)` | Returns all key-value pairs for the expanded field table |
| `tryParseJson(v)` | Parses a string value as a JSON object; returns `null` on failure |
| `isMetadataKey(key)` | Returns true for the special `metadata` field (rendered as a sub-table) |
| `formatValue(v)` | Stringifies any field value; objects are pretty-printed |
| `formatMetaValue(v)` | Formats metadata sub-field: booleans → Yes/No, arrays → comma list |
| `prev()` / `next()` | Page navigation; re-executes search in search mode |
| `getPopularity(doc)` | Reads `popularity` from the top-level field or falls back to `metadata.popularity` |
| `getIsFeatured(doc)` | Reads `is_featured` from the top-level field or falls back to `metadata.is_featured` |
| `openEdit(doc)` | Opens the edit modal pre-filled with the document's current `popularity` and `is_featured` |
| `closeEdit()` | Dismisses the edit modal without saving |
| `saveEdit()` | Calls `api.updateDocument()` (PATCH); writes top-level fields + metadata blob; closes modal on success |

**Edit modal:** Visible in the expanded document view as a "✎ Edit" button at the bottom of `.doc-fields`.  Opens an overlay with a numeric score input (0–1 000 000) and a toggle switch for `is_featured`.  Changes take effect immediately in Typesense via `action=update` (no re-indexing required).  The `★ featured` badge and `↑{score}` badge are shown in the collapsed document header whenever those values are non-zero/true.

---

### 15.5 CreateWizard.svelte

4-step modal for onboarding a new ISP index.

| Step | What happens |
|---|---|
| 1 — Name | User chooses index type (ISP Managed or External/Catalog), enters name, description, and — for external sources — collection name, filter, query-by, title field, and URL field.  `api.createIndex()` called; `indexId` stored on success |
| 2 — Connector | Form for REST API or Sitemap config; validation only |
| 3 — Field mapping | Builds `RestConnectorConfig` with optional extra field rows; calls `api.ingestRest()` or `api.ingestSitemap()` |
| 4 — Progress | Polls `api.getStatus()` every 1.5 s until `done` or `error` |

**Index type toggle (Step 1):**
- **ISP Managed** — ISP creates and owns a private `isp_{id}` Typesense collection.  All content goes through the ISP pipeline, enabling the security layer and editorial controls.  This is the recommended mode for all new sources.
- **External / Catalog** — ISP writes into a pre-existing shared Typesense collection during rebuild.  Used when a collection already exists and must be shared with other consumers.

**Dispatches:** `done(indexId)`, `cancel`

**Key functions:**

| Function | Description |
|---|---|
| `addMapping()` | Appends blank source→target row to `extraMappings` |
| `removeMapping(i)` | Removes mapping row at index `i` |
| `stepOne()` | Validates name and external fields if applicable; creates index; advances to step 2 |
| `stepTwo()` | Validates connector URL; advances to step 3 |
| `stepThree()` | Builds config; starts ingest; advances to step 4 |
| `pollUntilDone()` | Polls status every 1.5 s; updates `statusText`, `statusIndexed`, `statusTotal` |

---

### 15.6 RebuildPanel.svelte

Full-rebuild dialog.  Allows curl paste to auto-fill all form fields, then triggers a rebuild with live progress tracking.

**Dispatches:** `done`, `close`

**Key functions:**

| Function | Description |
|---|---|
| `getExistingRest()` | Pre-populates form from the first saved REST connector |
| `parseCurl(raw)` | Parses a raw curl string into method, URL, headers, body |
| `onCurlPaste(e)` | Intercepts paste events; if curl command, calls `parseCurl` and suppresses default paste |
| `buildCurl(u, m, h, b)` | Builds a multi-line curl preview from current form values |
| `copyCurl()` | Copies curl preview to clipboard with brief confirmation |
| `runRebuild()` | Validates; calls `api.rebuild()`; starts `poll()` |
| `poll()` | Polls status every 1.2 s; drives progress bar and stage text |
| `reset()` | Resets all progress state for re-submission |

---

### 15.7 IndexDetail.svelte

Tabbed detail view used in the configure flow.

**Tabs:** Overview (last ingest status + connectors list) · Documents · Search · API

**Dispatches:** `back`

**Key function:**

| Function | Description |
|---|---|
| `fmtDate(iso)` | Formats ISO date string to short locale date |

---

### 15.8 SearchPanel.svelte

Standalone search UI inside `IndexDetail`.  Simpler than `SourceContent` — first page only, no pagination.

| Function | Description |
|---|---|
| `search()` | Calls `api.search()` and renders result cards |
| `onKeydown(e)` | Submits on Enter |
| `excerpt(text, max=160)` | Truncates with ellipsis |

---

### 15.9 DocumentTable.svelte

Paginated table of indexed documents used in `IndexDetail "Documents"` tab.  Four columns: Title, Source, URL, Indexed.  Manual refresh button.

| Function | Description |
|---|---|
| `load()` | Fetches current page from `api.listDocuments()` |
| `fmtDate(ts)` | Formats Unix timestamp to locale date |
| `prev()` / `next()` | Page navigation |

---

### 15.10 ApiPanel.svelte

API reference card shown in `IndexDetail "API"` tab.  Generates copy-ready curl examples using `window.location` for the base URL, so examples work correctly in both local and deployed environments.

| Function | Description |
|---|---|
| `copy(text, key)` | Copies text to clipboard; shows brief confirmation badge identified by `key` |

---

### 15.11 ExtensionsPanel.svelte

Global JS extension library management page.  Shown when the user navigates to `view='extensions'` from the sidebar footer.

**Operations:** List extensions · Create new (modal editor) · Edit existing · Delete (with confirmation)

**Key functions:**

| Function | Description |
|---|---|
| `load()` | Fetches all extensions from `api.listExtensions()` |
| `openCreate()` | Opens editor modal with default `transform(doc)` code |
| `openEdit(ext)` | Opens editor pre-filled with extension's current values |
| `save()` | Validates; creates or updates via `api.createExtension()` / `api.updateExtension()` |
| `deleteExt(id)` | `window.confirm` → `api.deleteExtension()` |
| `fmtDate(s)` | Formats ISO date string to short locale date |

---

### 15.12 IndexExtensionsPanel.svelte

Per-index extension assignment overlay.  Opened from the "⚙ Extensions" button in `SourceContent`.

**Operations:** See assigned extensions in run order · Reorder with ↑↓ · Toggle enabled/disabled · Remove from index · Add from library

**Dispatches:** `close`

**Key functions:**

| Function | Description |
|---|---|
| `load()` | Fetches assigned extensions and full library in parallel |
| `add(ext)` | Assigns extension at the end of the current run order |
| `remove(ext)` | Unassigns extension (does not delete from library) |
| `toggle(ext)` | Flips `enabled` without changing `run_order` |
| `moveUp(idx)` | Swaps `run_order` with the preceding item |
| `moveDown(idx)` | Swaps `run_order` with the following item |

---

### 15.13 Dashboard.svelte

Grid overview of all ISP indexes rendered via `IndexCard`.  Shows empty-state CTA when there are no indexes.

**Dispatches:** `open(id)`, `create`, `delete(id)`

---

### 15.14 IndexCard.svelte

Compact card in the Dashboard grid.  Shows connector type badge, name, description, doc count, and creation date.  Clicking the card dispatches `open`; the ✕ button dispatches `delete` with `stopPropagation`.

**Dispatches:** `open(id)`, `delete(id)`

---

## 16. Data Flows

### 16.1 Ingest (REST)

```
POST /v1/indexes/{id}/ingest/rest  {url, method, headers, body, field_map, ...}
    │
    ├── Persist connector config to PostgreSQL (JSONB)
    ├── set_status(state='queued')
    └── BackgroundTasks.add_task(_run_ingest, index_id, cfg)
                │
                ▼ (background)
        rest_connector.fetch_and_parse(cfg)
            │  Auto-detect result_path and field names if not specified
            │  Apply field_map renaming
            └── list[DocumentItem dicts]
                    │
                    embedding_service.embed_texts([doc['content'] for doc in docs])
                    │  Batch embed; two-tier cache
                    └── list[float vector]
                            │
                            Attach vec to each doc
                            typesense_client.upsert_documents(index_id, docs)
                            update_index(doc_count, last_ingested)
                            set_status(state='done', indexed=N)
```

---

### 16.2 Ingest (Sitemap)

```
POST /v1/indexes/{id}/ingest/sitemap  {url, max_pages, content_selector, ...}
    │
    ├── Persist connector config
    ├── set_status(state='queued')
    └── BackgroundTasks.add_task(_run_ingest, index_id, cfg)
                │
                ▼ (background)
        sitemap_connector.crawl(cfg)
            │  Fetch sitemap XML → extract <loc> URLs → apply filters → cap at max_pages
            │  For each URL (0.1s polite delay):
            │      fetch HTML → strip chrome → extract content via CSS selectors → 4000 char cap
            └── list[DocumentItem dicts]
                    │  (same embed + upsert path as REST ingest)
```

---

### 16.3 Rebuild with Extension Pipeline

```
POST /v1/indexes/{id}/rebuild  {url, method, headers, body}
    │
    ├── Persist connector config
    ├── set_status(state='queued')
    └── BackgroundTasks.add_task(_run_rebuild, index_id, cfg)
                │
                ▼ (background)
        rest_connector.fetch_raw_items(cfg)
            └── list[raw API dicts]
                    │
                    _infer_isp_type(cfg) → "device" | "accessory" | "item"
                    Inject _isp_type on every raw item (setdefault)
                    │
                    extension_runner.apply_extensions(index_id, raw_items)
                    │   For each enabled extension (sorted by run_order):
                    │       PyMiniRacer V8: ctx.call("transform", doc)
                    │       Error → keep original, log, continue
                    └── list[normalised dicts with standard keys]
                            │
                    ┌───────┴──────────────────────────────────────────┐
                    │  ISP-managed path (is_external=false)            │
                    │                                                  │
                    │  _ensure_isp_schema(docs)                        │
                    │    • maps title/content/url/source_type          │
                    │    • packs all extra fields into metadata JSON   │
                    │    • promotes popularity + is_featured to        │
                    │      top-level Typesense fields                  │
                    │    • hashes non-string IDs                       │
                    │  → list[ISP schema dicts]                        │
                    │                                                  │
                    │  embedding_service.embed_texts([...])            │
                    │  typesense_client.drop_collection(index_id)      │
                    │  typesense_client.create_collection(index_id)    │
                    │  typesense_client.upsert_documents(index_id)     │
                    └──────────────────────────────────────────────────┘
                            │
                    ┌───────┴──────────────────────────────────────────┐
                    │  Catalog write-back path (is_external=true)      │
                    │                                                  │
                    │  _generic_catalog_mapper(item, idx)              │
                    │    → Typesense catalog schema dict               │
                    │    → metadata JSON string                        │
                    │                                                  │
                    │  embedding_service.embed_texts([display ...])    │
                    │  typesense_client.delete_from_collection(col)    │
                    │  typesense_client.upsert_to_collection(col)      │
                    └──────────────────────────────────────────────────┘
                            │
                    set_status(state='done', indexed=N)
```

---

### 16.4 Hybrid Search

```
POST /v1/indexes/{id}/search  {"q": "iphone 15", "mode": "hybrid"}
    │
    embedding_service.embed_query("iphone 15")
    │   L1 LRU hit → return immediately
    │   L1 miss → L2 Redis hit → promote to L1, return
    │   Both miss → Voyage AI API → cache in L1 + L2, return
    └── [float vector, 1024 dims]
            │
            typesense_client.search_with_vec(index_id, q, vec, page, per_page)
            │
            │   multi_search (two passes in parallel):
            │   ┌─ lexical:  query_by="title,content", num_results = per_page * 3
            │   └─ semantic: vector_query="vec:([...],k=per_page*3)"
            │
            │   RRF merge:
            │   score(doc) = 0.7/(rank_lexical + 60) + 0.3/(rank_semantic + 60)
            │   Sort merged by score desc → slice page
            └── SearchResult {hits, found, page}
```

---

## 17. Extension System

### Overview

Extensions are JavaScript files that define a single `transform(doc)` function.  They run inside PyMiniRacer V8 isolates during `rebuild` jobs.

### Writing an extension

```javascript
// @name    My Transform
// @description One-line description visible in the UI

function transform(doc) {
  // doc contains all fields from the raw API response
  // plus _isp_type ("device" or "accessory") injected by ISP

  // Modify and return the doc
  doc.display = doc.productName || doc.name || '';
  doc.url = 'https://example.com/' + (doc.slug || doc.id);

  return doc;
}
```

**Rules:**
- The file must define `function transform(doc) { ... return doc; }` at the top level.
- The function must return the document object.
- No network, filesystem, or Node.js APIs — only ECMAScript built-ins.
- The `// @name` and `// @description` header comments are required for auto-seeding.

### File-based seeding (startup)

1. On startup, `_seed_extensions_from_files()` scans `EXTENSIONS_DIR` (`/app/extensions` in Docker).
2. Each `*.js` file is upserted by name — code is always updated to the latest committed version.
3. The extension is auto-assigned (`assign_extension_if_new`) to every external Typesense collection.  `ON CONFLICT DO NOTHING` ensures user-configured ordering and enabled state are never overridden.

### Managing extensions in the UI

- **Global library** — `ExtensionsPanel` (sidebar footer → "⚙ Extensions") lets you create, edit, and delete extensions.
- **Per-index assignment** — `IndexExtensionsPanel` (source header → "⚙ Extensions") lets you assign/remove extensions and set their execution order.

### Execution order

Extensions run in `run_order` ascending order.  The output of each extension is passed as input to the next — forming a pipeline.  A disabled extension is skipped without breaking the chain.

---

## 18. API Reference

Full interactive documentation is available at **`http://localhost:8001/docs`** (Swagger UI) when the API is running.

### Summary

| Group | Endpoints |
|---|---|
| Sources | `GET /v1/indexes`, `POST /v1/indexes`, `POST /v1/indexes/discover`, `GET /v1/indexes/{id}`, `DELETE /v1/indexes/{id}` |
| Connectors | `POST /v1/indexes/{id}/connectors/rest`, `POST /v1/indexes/{id}/connectors/sitemap` |
| Ingest | `POST /v1/indexes/{id}/ingest/rest`, `POST /v1/indexes/{id}/ingest/sitemap` |
| Rebuild | `POST /v1/indexes/{id}/rebuild` |
| Status | `GET /v1/indexes/{id}/status` |
| Documents | `GET /v1/indexes/{id}/documents`, `PATCH /v1/indexes/{id}/documents/{doc_id}`, `POST /v1/indexes/{id}/search` |
| Extensions (library) | `GET /v1/extensions`, `POST /v1/extensions`, `GET/PUT/DELETE /v1/extensions/{id}` |
| Extensions (per-index) | `GET/POST /v1/indexes/{id}/extensions`, `DELETE /v1/indexes/{id}/extensions/{ext_id}` |
| Catalog (PLP) | `POST /v1/catalog`, `GET /v1/catalog/facets`, `GET /v1/suggest`, `GET /v1/intent/tab` |
| Content (generic) | `GET /v1/content`, `GET /v1/content/{slug}`, `GET /v1/content/{slug}/search` |
| Settings | `GET /v1/settings`, `PUT /v1/settings` |
| Health | `GET /health` |

### Search request body

```json
{
  "q": "string",
  "mode": "hybrid | lexical | semantic",
  "page": 1,
  "per_page": 10
}
```

### Search response

```json
{
  "hits": [
    {
      "document": {
        "id": "abc123",
        "title": "Page Title",
        "content": "Page content…",
        "url": "https://example.com/page",
        "source_type": "rest | sitemap | external",
        "indexed_at": 1717000000
      }
    }
  ],
  "found": 42,
  "page": 1
}
```

---

## 19. Deployment

### Local development (Docker Compose)

```bash
docker compose up -d           # Start all services
docker compose logs -f isp-api # Tail API logs
docker compose down            # Stop all services
```

### Partial rebuilds (deploy scripts)

```bash
./deploy-api.sh    # docker build + docker compose up -d isp-api
./deploy-ui.sh     # docker build + docker compose up -d isp-ui
./deploy-db.sh     # docker compose up -d isp-db + wait for healthy
./deploy-all.sh    # deploy-db → deploy-api → deploy-ui
```

### Backend Dockerfile summary

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml .
RUN uv pip install --system .
COPY src/ ./src/
COPY extensions/ ./extensions/
CMD ["uvicorn", "isp.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

### Frontend Dockerfile summary

```dockerfile
# Build stage
FROM node:20-alpine AS build
ARG PUBLIC_API_URL=http://localhost:8001/v1
COPY . .
RUN npm ci && npm run build

# Runtime stage
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

### Production checklist

- [ ] Set `TYPESENSE_API_KEY` to a strong random value
- [ ] Set `VOYAGE_API_KEY`
- [ ] Set `DATABASE_URL` to a production PostgreSQL DSN
- [ ] Set `REDIS_URL` if using an external Redis
- [ ] Set `PUBLIC_API_URL` build arg to the public API hostname
- [ ] Configure persistent volumes for PostgreSQL and Typesense data
- [ ] Add a reverse proxy (nginx/Caddy) for TLS termination

---

## 20. Dependency Reference

### Backend (Python ≥ 3.12)

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | ≥0.110 | Web framework |
| `uvicorn[standard]` | ≥0.29 | ASGI server |
| `typesense` | ≥0.21 | Typesense Python SDK |
| `voyageai` | ≥0.3 | Embedding API client |
| `asyncpg` | ≥0.29 | Async PostgreSQL driver |
| `httpx` | ≥0.27 | Async HTTP client (REST connector) |
| `beautifulsoup4` | ≥4.12 | HTML parsing (sitemap connector) |
| `lxml` | ≥5.0 | Fast HTML/XML parser backend for bs4 |
| `pydantic-settings` | ≥2.0 | Environment-based configuration |
| `python-dotenv` | ≥1.0 | `.env` file loading |
| `structlog` | ≥24.0 | Structured logging |
| `redis[hiredis]` | ≥5.0 | L2 embedding cache |
| `cachetools` | ≥5.3 | L1 LRU in-process cache |
| `py-mini-racer` | ≥0.6 | PyMiniRacer V8 JavaScript isolates |

### Frontend (Node 20)

| Package | Version | Purpose |
|---|---|---|
| `astro` | ^4.8 | Build pipeline and page container |
| `@astrojs/svelte` | ^5.4 | Svelte integration for Astro |
| `svelte` | ^4.2 | UI component framework |
| `typescript` | ^5.0 | TypeScript compiler |
