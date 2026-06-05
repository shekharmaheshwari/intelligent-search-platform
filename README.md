# Intelligent Search Platform (ISP)

A self-contained search backend that indexes content from REST APIs and sitemaps into [Typesense](https://typesense.org/), generates 1024-dim vector embeddings via [Voyage AI](https://www.voyageai.com/), and exposes hybrid (lexical + semantic) search. Ships with a Svelte management UI and powers the T-Mobile PLP product search.

---

## Quick Start

```bash
# 1. Copy env template
cp backend/.env.example backend/.env
# Edit VOYAGE_API_KEY in .env

# 2. Start everything (db → api → ui)
./deploy-all.sh
```

| Service | URL |
|---|---|
| ISP Admin UI | http://localhost:3001 |
| ISP API + Swagger | http://localhost:8001/docs |
| T-Mobile PLP | http://localhost:3000 |

---

## Architecture

```
Browser
  └─► isp-ui :3001 (Astro + Svelte)
        └─► isp-api :8001 (FastAPI)
              ├─► isp-db :5432 (PostgreSQL — metadata, connectors, extensions)
              ├─► typesense :8108 (documents + vectors)
              └─► Voyage AI (cloud — 1024-dim embeddings)
```

---

## Core Concepts

| Term | Meaning |
|---|---|
| **Index** | A named data source (e.g. *Phones*, *Blog*). Each index maps to a Typesense collection. |
| **Connector** | Config telling ISP where to fetch data: REST endpoint or sitemap URL. |
| **Ingest** | Background job that fetches, transforms, embeds, and upserts documents. |
| **Extension** | JavaScript `transform(doc)` function run inside a V8 isolate before indexing. Used to normalise API responses, compute URLs, rename fields. |
| **Catalog** | Shared Typesense collection used by the T-Mobile PLP — ISP writes to it via rebuild. |
| **External index** | ISP view over a pre-existing Typesense collection (e.g. `catalog`) that ISP did not create. |

---

## Extension System

Extensions are JavaScript files stored in `backend/extensions/*.js`. On startup ISP seeds them into the database and auto-assigns them to matching external indexes.

```js
// backend/extensions/my_mapper.js
// @name My API Mapper
// @description Normalises MyAPI response to ISP catalog schema

function transform(doc) {
  doc.display  = doc.productName;
  doc.url      = 'https://example.com/product/' + doc.slug;
  doc.price    = parseFloat(doc.msrp || 0);
  return doc;
}
```

Each extension runs inside a PyMiniRacer V8 isolate — no filesystem, no network, pure JS only. Multiple extensions can be chained via `run_order`. The extension output feeds directly into the generic catalog mapper which packages the flat dict into the Typesense schema.

---

## API Reference

Full Swagger UI available at **http://localhost:8001/docs**

Key endpoint groups:

| Prefix | Purpose |
|---|---|
| `GET/POST /v1/indexes` | Manage data sources |
| `POST /v1/indexes/{id}/ingest/rest` | Fetch from REST API |
| `POST /v1/indexes/{id}/rebuild` | Full rebuild with write-back |
| `POST /v1/indexes/{id}/search` | Hybrid search |
| `GET/POST /v1/extensions` | Global extension library |
| `GET/POST /v1/indexes/{id}/extensions` | Per-index assignment |
| `POST /v1/catalog` | PLP product search |
| `GET /v1/catalog/facets` | Filter sidebar facets |
| `GET /v1/suggest` | Typeahead autocomplete |
| `GET /v1/content/{slug}` | Generic content API |

---

## Environment Variables

```env
# backend/.env
TYPESENSE_HOST=typesense
TYPESENSE_PORT=8108
TYPESENSE_API_KEY=local-dev-key-change-me
DATABASE_URL=postgresql://isp:isp-dev-password@isp-db:5432/isp
VOYAGE_API_KEY=<your-voyage-key>
REDIS_URL=redis://isp-redis:6379/0
EXTENSIONS_DIR=/app/extensions
HOST=0.0.0.0
PORT=8001
```

`VOYAGE_API_KEY` is optional. If empty, ISP falls back to lexical-only search with no embeddings.

---

## Deployment Scripts

```bash
./deploy-all.sh      # full stack (db → api → ui)
./deploy-api.sh      # rebuild + restart backend only
./deploy-ui.sh       # rebuild + restart frontend only
./deploy-db.sh       # start + wait for PostgreSQL
```

---

## Docs

- [Application Overview](docs/01-application-overview.md)
- [Typesense Integration](docs/02-typesense-integration.md)
- [Database Schema](docs/03-database.md)
- [Codebase Structure](docs/04-codebase.md)
- [Component Integrations](docs/05-integrations.md)
