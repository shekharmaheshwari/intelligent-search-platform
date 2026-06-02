# Application Overview — Intelligent Search Platform (ISP)

## Purpose

ISP is an internal search platform that lets users connect external data sources (REST APIs, sitemaps, existing Typesense collections) and make them full-text + semantically searchable through a single UI.

---

## Technology Stack

| Layer       | Technology                        |
|-------------|-----------------------------------|
| Frontend    | Astro 4 + Svelte 4 + TypeScript   |
| Backend API | FastAPI (Python 3.12) + uvicorn   |
| Search      | Typesense (vector + full-text)    |
| Embeddings  | Voyage AI (voyage-3, 1024-dim)    |
| Metadata DB | PostgreSQL 16                     |
| Container   | Docker Compose                    |

---

## Ports

| Service    | Port  | Purpose                        |
|------------|-------|--------------------------------|
| isp-ui     | 3001  | Frontend (nginx serving Astro) |
| isp-api    | 8001  | FastAPI backend                |
| isp-db     | 5432  | PostgreSQL metadata store      |
| Typesense  | 8108  | Search engine                  |
| tmo-plp    | 3000  | T-Mobile PLP (external)        |

---

## High-Level Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Browser                             │
│                  http://localhost:3001                          │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP REST
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ISP Frontend (isp-ui)                        │
│              Astro + Svelte SPA — port 3001                     │
│                                                                 │
│  Components:                                                    │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────┐ │
│  │SourcesSidebar│  │SourceContent  │  │ CreateWizard /       │ │
│  │(list sources)│  │(docs + search)│  │ RebuildPanel         │ │
│  └──────────────┘  └───────────────┘  └──────────────────────┘ │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP REST /v1/*
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ISP Backend (isp-api)                        │
│                   FastAPI — port 8001                           │
│                                                                 │
│  Routers:                                                       │
│  ┌──────────────────────┐   ┌──────────────────────────────┐   │
│  │ /v1/indexes          │   │ /v1/indexes/{id}/search      │   │
│  │  CRUD + ingest       │   │  hybrid / lexical / semantic  │   │
│  └──────────────────────┘   └──────────────────────────────┘   │
│                                                                 │
│  Services:                                                      │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────────────────┐  │
│  │index_service│ │embedding_svc │ │ rest_connector /        │  │
│  │ (PostgreSQL)│ │ (Voyage AI)  │ │ sitemap_connector       │  │
│  └─────────────┘ └──────────────┘ └────────────────────────┘  │
└───┬────────────────────┬──────────────────────┬────────────────┘
    │                    │                      │
    ▼                    ▼                      ▼
┌────────┐     ┌──────────────────┐    ┌──────────────┐
│  isp-db│     │    Typesense     │    │  Voyage AI   │
│Postgres│     │  port 8108       │    │  API (cloud) │
│ 5432   │     │  (search index   │    │  (embeddings)│
│        │     │  + vectors)      │    │              │
└────────┘     └──────────────────┘    └──────────────┘
```

---

## Core User Flows

### 1. Add a New Source (Create Wizard)
```
User clicks "+"
  → Step 1: Enter name + description
      → POST /v1/indexes → saved to PostgreSQL
  → Step 2: Enter REST API URL / headers / body
      → POST /v1/indexes/{id}/ingest/rest
          → connector saved to PostgreSQL immediately
          → background task: fetch → embed → upsert to Typesense
  → Step 3: Poll status → show progress → "View Index"
```

### 2. Rebuild a Source
```
User clicks "↺ Rebuild"
  → Fields pre-populated from PostgreSQL (connectors column)
  → User edits if needed → "Run Rebuild"
      → POST /v1/indexes/{id}/rebuild
          → background task:
              if external_collection:
                  fetch raw → map to catalog schema → upsert to catalog collection
              else:
                  fetch → parse → drop+recreate isp_{id} → upsert
          → connector config saved to PostgreSQL
          → doc_count updated in PostgreSQL
```

### 3. Search
```
User types query → press Enter / click Search
  → POST /v1/indexes/{id}/search
      if has embeddings:
          embed query via Voyage AI
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
```

### 5. Auto-Discovery
```
App loads → POST /v1/indexes/discover
  → list all Typesense collections
  → skip isp_* (ISP-managed)
  → skip deleted_splits entries
  → match against _KNOWN_SPLITS (catalog → Phones, Accessories)
  → create IndexMeta in PostgreSQL for new external collections
```

---

## Docker Network

All services run on the external Docker network `search_tmo-net`, allowing ISP to access Typesense and the T-Mobile services directly by container name.

```
search_tmo-net
├── isp-ui        (3001)
├── isp-api       (8001)
├── isp-db        (5432)
├── typesense     (8108)
├── tmo-plp       (3000)
└── tmo-app       (8000)
```
