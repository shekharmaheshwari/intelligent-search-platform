# Application Overview — Intelligent Search Platform (ISP)

## Purpose

ISP is an internal search platform that lets users connect external data sources (REST APIs, sitemaps, existing Typesense collections) and make them full-text + semantically searchable through a single UI. It also writes back to the shared `catalog` Typesense collection consumed by the T-Mobile PLP.

---

## Technology Stack

| Layer         | Technology                               |
|---------------|------------------------------------------|
| Frontend      | Astro 4 + Svelte 4 + TypeScript          |
| Backend API   | FastAPI (Python 3.12) + uvicorn          |
| Search        | Typesense (vector + full-text)           |
| Embeddings    | Voyage AI (voyage-3, 1024-dim)           |
| Embedding Cache | Redis (L2) + in-memory LRU (L1)        |
| Extension Runtime | PyMiniRacer (V8 JS isolates)         |
| Metadata DB   | PostgreSQL 16                            |
| Container     | Docker Compose                           |

---

## Ports

| Service    | Port | Purpose                        |
|------------|------|--------------------------------|
| isp-ui     | 3001 | Frontend (nginx serving Astro) |
| isp-api    | 8001 | FastAPI backend                |
| isp-db     | 5432 | PostgreSQL metadata store      |
| isp-redis  | 6379 | Embedding cache (Redis)        |
| Typesense  | 8108 | Search engine                  |
| tmo-plp    | 3000 | T-Mobile PLP (external)        |

---

## High-Level Flow Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                          User Browser                            │
│                    http://localhost:3001                         │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTP REST
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                   ISP Frontend (isp-ui)                          │
│              Astro + Svelte SPA — port 3001                      │
│                                                                  │
│  Components:                                                     │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────┐  │
│  │SourcesSidebar│  │SourceContent  │  │ CreateWizard (4-step)│  │
│  │(list sources)│  │(docs + search)│  │ RebuildPanel         │  │
│  └──────────────┘  └───────────────┘  └──────────────────────┘  │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐  │
│  │ ExtensionsPanel      │  │ IndexExtensionsPanel             │  │
│  │ (global library)     │  │ (per-index assignment)           │  │
│  └──────────────────────┘  └──────────────────────────────────┘  │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTP REST /v1/*
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                   ISP Backend (isp-api)                          │
│                   FastAPI — port 8001                            │
│                                                                  │
│  Routers:                                                        │
│  ┌────────────────────┐  ┌──────────────────────────────────┐   │
│  │ /v1/indexes        │  │ /v1/indexes/{id}/search           │   │
│  │  CRUD + ingest     │  │  hybrid / lexical / semantic      │   │
│  └────────────────────┘  └──────────────────────────────────┘   │
│  ┌────────────────────┐  ┌──────────────────────────────────┐   │
│  │ /v1/extensions     │  │ /v1/catalog                       │   │
│  │  JS transform lib  │  │  PLP product search               │   │
│  └────────────────────┘  └──────────────────────────────────┘   │
│                                                                  │
│  Services:                                                       │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────────────────┐  │
│  │index_service│ │embedding_svc │ │ rest_connector /          │  │
│  │ (PostgreSQL)│ │(Voyage + LRU)│ │ sitemap_connector         │  │
│  └─────────────┘ └──────────────┘ └──────────────────────────┘  │
│  ┌─────────────────────────┐  ┌──────────────────────────────┐  │
│  │ extension_service       │  │ extension_runner              │  │
│  │ (DB CRUD for extensions)│  │ (V8 isolates via PyMiniRacer) │  │
│  └─────────────────────────┘  └──────────────────────────────┘  │
└──┬─────────────────┬──────────────────────┬─────────────────────┘
   │                 │                      │
   ▼                 ▼                      ▼
┌────────┐  ┌──────────────────┐  ┌──────────────┐
│ isp-db │  │    Typesense     │  │  Voyage AI   │
│Postgres│  │  port 8108       │  │  API (cloud) │
│ 5432   │  │  (search index   │  │  (embeddings)│
│        │  │  + vectors)      │  │              │
└────────┘  └──────────────────┘  └──────────────┘
```

---

## Core User Flows

### 1. Add a New Source (Create Wizard — 4 Steps)
```
User clicks "+"
  → Step 1: Enter name + description
      → POST /v1/indexes → saved to PostgreSQL
  → Step 2: Enter REST API URL / headers / body
  → Step 3: Configure field mapping (source → target field renames)
      → POST /v1/indexes/{id}/ingest/rest
          → connector config saved to PostgreSQL
          → background task: fetch → extensions → embed → upsert to Typesense
  → Step 4: Progress polling → "View Index"
```

### 2. Rebuild a Source (Extension Pipeline)
```
User clicks "↺ Rebuild"
  → Fields pre-populated from PostgreSQL (connectors column)
  → User edits if needed → "Run Rebuild"
      → POST /v1/indexes/{id}/rebuild
          → background task:
              if external_collection (catalog write-back):
                  fetch raw items from REST API
                  inject _isp_type hint per item (device/accessory)
                  apply JS extensions in run_order (V8 isolates)
                  map to catalog schema via _generic_catalog_mapper
                  embed display+description via Voyage AI
                  upsert to catalog Typesense collection
              else (ISP-managed):
                  fetch → parse
                  apply JS extensions in run_order
                  embed → drop+recreate isp_{id} → upsert
          → connector config + doc_count saved to PostgreSQL
```

### 3. Search
```
User types query → press Enter / click Search
  → POST /v1/indexes/{id}/search
      if has embeddings:
          embed query (served from LRU → Redis cache first)
          multi_search: lexical (0.7 weight) + semantic (0.3 weight)
          RRF merge → ranked results
      else:
          lexical search only
  → Results displayed with expand (+) to see all fields including vec
```

### 4. Delete a Source
```
User clicks ✕ → confirmation modal → "Yes, remove"
  → DELETE /v1/indexes/{id}
      → if external: delete docs from Typesense (filter_by)
                     add to deleted_splits (never re-discover)
      → if ISP-owned: drop isp_{id} Typesense collection
      → DELETE from PostgreSQL indexes + ingest_status tables
      → CASCADE deletes index_extensions assignments
```

### 5. Auto-Discovery + Auto-Assign Extension
```
App loads → POST /v1/indexes/discover
  → list all Typesense collections
  → skip isp_* (ISP-managed)
  → skip deleted_splits entries
  → match against _KNOWN_SPLITS (catalog → Phones, Accessories)
  → create IndexMeta in PostgreSQL for new external collections
  → auto-assign seeded extension (e.g. T-Mobile Catalog Mapper) to each
    new external index using ON CONFLICT DO NOTHING (won't override
    existing user-configured assignments)
```

### 6. Manage Extensions
```
User opens "⚙ Extensions" in sidebar footer
  → ExtensionsPanel (global library)
      → view / create / edit / delete JS transforms
User opens "⚙ Extensions" in SourceContent header
  → IndexExtensionsPanel (per-index overlay)
      → see assigned extensions with run_order
      → ↑↓ reorder, toggle on/off, ✕ remove
      → add extensions from library
```

---

## Docker Network

All services run on the external Docker network `search_tmo-net`, allowing ISP to access Typesense and the T-Mobile services directly by container name.

```
search_tmo-net
├── isp-ui        (3001)
├── isp-api       (8001)
├── isp-db        (5432)
├── isp-redis     (6379)
├── typesense     (8108)
├── tmo-plp       (3000)
└── tmo-app       (8000)
```
