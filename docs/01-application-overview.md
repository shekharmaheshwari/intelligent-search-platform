# Application Overview — Intelligent Search Platform (ISP)

## Purpose

ISP is an internal search platform that lets users connect external data sources (REST APIs, sitemaps, existing Typesense collections) and make them full-text + semantically searchable through a single UI. It also writes back to the shared `catalog` Typesense collection consumed by the PLP (Product Listing Page).

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
| tmo-plp    | 3000 | PLP (external)                 |

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
  → Step 1: Choose index type
      ISP Managed — ISP creates and owns a private isp_{id} collection
                    All content through ISP pipeline (security layer, editorial controls)
      External/Catalog — ISP writes into a pre-existing shared Typesense collection
                    Enter: collection name, filter, query-by field, title field, URL field
      Enter name + description
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
              fetch raw items from REST API
              _infer_isp_type(cfg) → inject _isp_type on every item
              apply JS extensions in run_order (V8 isolates)

              if ISP-managed (is_external=false):
                  _ensure_isp_schema(docs)
                    — map title/content/url/source_type
                    — pack extras into metadata JSON blob
                    — promote popularity + is_featured to top-level
                  embed via Voyage AI
                  drop+recreate isp_{id} collection
                  upsert all docs

              if external (is_external=true, catalog write-back):
                  _generic_catalog_mapper → catalog schema
                  embed display+description via Voyage AI
                  delete old docs from external collection
                  upsert to external Typesense collection

          → connector config + doc_count saved to PostgreSQL
```

### 3. Search (Admin UI — per index)
```
User types query → press Enter / click Search
  → POST /v1/indexes/{id}/search
      if has embeddings:
          embed query (served from LRU → Redis cache first)
          multi_search: lexical (0.7 weight) + semantic (0.3 weight)
          RRF merge → ranked results
      else:
          lexical search only
  → Results displayed with expand (+) to see all fields
  → Each expanded doc shows popularity badge (↑score) and ★ featured badge
  → "✎ Edit" button opens modal to update popularity / is_featured
      → PATCH /v1/indexes/{id}/documents/{doc_id}
          → writes top-level popularity + is_featured + metadata blob
          → no re-embedding required
```

### 3b. PLP Catalog Search
```
PLP → POST /v1/catalog
  → _get_isp_collections() — discover all isp_* collections
  → _rrf_search(collections, q, vec, filter_by="source_type:=device")
      → multi_search: one lex + one sem pass per collection
      → RRF merge across all collections
      → editorial boosts:
            is_featured → +0.5
            popularity  → +min(pop, 1M) / 2M
      → sort by final score desc → slice page
  → results returned as CatalogItem list
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
  → auto-assign seeded extension (e.g. Catalog Mapper) to each
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

All services run on the external Docker network `search_tmo-net`, allowing ISP to access Typesense and external services directly by container name.

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

---

## References

> Citations below are sourced from training knowledge (not live browsing). DOI and arXiv links are provided where known.

1. **Cormack, G. V., Clarke, C. L. A., & Buettcher, S.** (2009). "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods." *Proceedings of SIGIR 2009*, pp. 758–759. DOI: [10.1145/1571941.1572114](https://doi.org/10.1145/1571941.1572114) — Introduces RRF, the rank-merging strategy used in ISP's hybrid search to combine BM25 and semantic ANN results.

2. **Robertson, S., & Zaragoza, H.** (2009). "The Probabilistic Relevance Framework: BM25 and Beyond." *Foundations and Trends in Information Retrieval*, 3(4), 333–389. DOI: [10.1561/1500000019](https://doi.org/10.1561/1500000019) — Full treatment of BM25, the full-text ranking function used by Typesense for the lexical search leg of hybrid mode.

3. **Johnson, J., Douze, M., & Jégou, H.** (2021). "Billion-Scale Similarity Search with GPUs." *IEEE Transactions on Big Data*, 7(3), 535–547. arXiv: [1702.08734](https://arxiv.org/abs/1702.08734) — FAISS: approximate nearest-neighbour (ANN) search at scale; the approach Typesense uses internally for vector similarity retrieval.

4. **Karpukhin, V., Oğuz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., & Yih, W.-T.** (2020). "Dense Passage Retrieval for Open-Domain Question Answering." *Proceedings of EMNLP 2020*. arXiv: [2004.04906](https://arxiv.org/abs/2004.04906) — Dense retrieval with separate query/document encoders; ISP mirrors this with Voyage AI's `input_type="query"` vs `input_type="document"` distinction.

5. **Merkel, D.** (2014). "Docker: Lightweight Linux Containers for Consistent Development and Deployment." *Linux Journal*, 2014(239). ISSN: 1075-3583 — Docker containers enable ISP's reproducible multi-service deployment across development and production environments.

6. **Reimers, N., & Gurevych, I.** (2019). "Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks." *Proceedings of EMNLP 2019*. arXiv: [1908.10084](https://arxiv.org/abs/1908.10084) — Sentence-level embeddings produced by Voyage AI follow this encoder architecture; ISP uses Voyage AI `voyage-3` (1024-dim) as its primary embedding provider.
