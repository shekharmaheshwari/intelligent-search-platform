# Codebase Structure

## Repository Layout

```
intelligent-search-platform/
├── backend/                        # FastAPI Python backend
│   ├── src/isp/
│   │   ├── main.py                 # FastAPI app, lifespan, CORS, router registration
│   │   ├── config.py               # Settings from env vars (.env)
│   │   ├── models.py               # Pydantic models (request/response shapes)
│   │   ├── routers/
│   │   │   ├── indexes.py          # All /v1/indexes/* endpoints
│   │   │   └── search.py           # /v1/indexes/{id}/search endpoint
│   │   └── services/
│   │       ├── database.py         # asyncpg pool + init_db() (table creation)
│   │       ├── index_service.py    # PostgreSQL CRUD for indexes/status/splits
│   │       ├── typesense_client.py # All Typesense operations
│   │       ├── embedding_service.py# Voyage AI embedding calls
│   │       ├── rest_connector.py   # Fetch + parse REST API data
│   │       └── sitemap_connector.py# Crawl + parse sitemaps
│   ├── pyproject.toml              # Dependencies
│   ├── Dockerfile
│   └── .env                        # Environment variables
│
├── frontend/                       # Astro + Svelte frontend
│   └── src/
│       ├── pages/index.astro       # Entry point, mounts App component
│       ├── lib/
│       │   ├── api.ts              # All fetch calls to backend API
│       │   ├── store.ts            # Svelte stores (appState, indexes, loading, error)
│       │   └── types.ts            # TypeScript interfaces
│       └── components/
│           ├── App.svelte          # Root: layout, routing, delete modal
│           ├── Header.svelte       # Top nav bar
│           ├── SourcesSidebar.svelte # Left sidebar: source list + add/delete
│           ├── SourceContent.svelte  # Main panel: docs list + search
│           ├── RebuildPanel.svelte   # Rebuild modal: curl paste + form + progress
│           ├── CreateWizard.svelte   # 3-step wizard to add a new source
│           └── IndexDetail.svelte    # Configure tab (connector settings)
│
├── docs/                           # Design documents (this folder)
├── docker-compose.yml
├── deploy-all.sh                   # Start everything in order
├── deploy-api.sh                   # Rebuild + restart backend
├── deploy-ui.sh                    # Build + restart frontend
└── deploy-db.sh                    # Start + wait for PostgreSQL
```

---

## Backend: Key Files

### `main.py`
```python
@asynccontextmanager
async def lifespan(app):
    await init_db()   # creates tables on startup
    yield

app = FastAPI(lifespan=lifespan)
app.include_router(indexes.router, prefix="/v1")
app.include_router(search.router, prefix="/v1")
```

### `models.py` — Core Pydantic Models
| Model | Purpose |
|---|---|
| `IndexMeta` | Full source metadata (id, name, connectors, external_*, doc_count) |
| `IndexCreate` | Request body for creating an index (name, description) |
| `RestConnectorConfig` | REST connector (url, method, headers, body, field mapping) |
| `SitemapConnectorConfig` | Sitemap connector (url, max_pages, selectors, patterns) |
| `IngestStatus` | Job status (state, total, indexed, error) |
| `DocumentItem` | Search result shape (id, title, content, url, source_type, indexed_at) |

### `routers/indexes.py` — Endpoints
| Method | Path | Action |
|---|---|---|
| GET | `/v1/indexes` | List all sources |
| POST | `/v1/indexes` | Create source |
| GET | `/v1/indexes/{id}` | Get source |
| DELETE | `/v1/indexes/{id}` | Delete source + Typesense data |
| POST | `/v1/indexes/discover` | Auto-import Typesense collections |
| GET | `/v1/indexes/{id}/status` | Poll ingest job status |
| POST | `/v1/indexes/{id}/ingest/rest` | Start REST ingest (background) |
| POST | `/v1/indexes/{id}/ingest/sitemap` | Start sitemap ingest (background) |
| POST | `/v1/indexes/{id}/rebuild` | Rebuild source (background) |
| GET | `/v1/indexes/{id}/documents` | List documents (paginated) |

### `services/index_service.py` — PostgreSQL CRUD
All database operations. Key functions:
- `list_indexes()` — SELECT all from indexes
- `get_index(id)` — SELECT by id
- `create_index(name, desc)` — INSERT new index
- `update_index(meta)` — UPDATE all fields
- `delete_index(id)` — DELETE + add to deleted_splits
- `get_status(id)` / `set_status(status)` — ingest_status table
- `discover_external_collections()` — scan Typesense, create ISP entries

### `services/typesense_client.py` — Typesense Operations
All Typesense API calls. Key functions:
- `create_collection(id)` / `drop_collection(id)`
- `upsert_documents(id, docs)` — ISP-managed collections
- `upsert_to_collection(col, docs)` — any collection (write-back)
- `search_documents(id, q, mode, page, per_page)`
- `search_with_vec(id, q, vec, page, per_page)` — hybrid RRF
- `list_documents(id, page, per_page)`
- `list_documents_external(col, filter, query_by, page, per_page)`
- `delete_from_collection(col, filter_by)`

### `services/rest_connector.py` — REST Data Fetching
- `fetch_raw_items(cfg)` — fetch without field mapping
- `fetch_and_parse(cfg)` — fetch + auto-detect fields → ISP doc shape
- `_auto_detect_path(data)` — finds first array in API response
- `_auto_detect_fields(item)` — heuristic field name detection

---

## Frontend: Key Files

### `lib/store.ts` — Global State
```typescript
appState: { selectedIndexId, view, detailTab }
indexes: IndexMeta[]
loading: boolean
error: string | null
```

### `lib/api.ts` — Backend Client
All calls go to `PUBLIC_API_URL` (set at build time via Astro env).
Key methods: `listIndexes`, `createIndex`, `deleteIndex`, `ingestRest`, `rebuild`, `search`, `listDocuments`, `getStatus`, `discoverIndexes`.

### `lib/types.ts` — TypeScript Interfaces
Mirrors the Pydantic models: `IndexMeta`, `RestConnectorConfig`, `IngestStatus`, `DocumentItem`.

### `App.svelte` — Root Component
- Owns the layout (Header + Sidebar + Main)
- Handles source selection, delete confirmation modal
- Calls `loadIndexes()` + `discoverIndexes()` on mount

### `SourceContent.svelte` — Main Panel
- Loads documents on source switch (by ID, not object reference)
- Search only fires on Enter or Search button click
- Document expand (+/-) shows all raw fields including vec textarea
- Rebuild panel opens as overlay modal

### `RebuildPanel.svelte` — Rebuild UI
- Curl paste parser auto-fills all fields
- Pre-populates from saved `type:rest` connector in PostgreSQL
- Shows live progress (queued → fetching → embedding → indexing → done)

### `CreateWizard.svelte` — 3-Step Wizard
- Step 1: Name + description → creates index in PostgreSQL
- Step 2: REST (URL, method, headers, body) or Sitemap → saves connector + starts ingest
- Step 3: Progress polling → "View Index"

---

## Environment Variables (backend/.env)

```env
TYPESENSE_HOST=typesense
TYPESENSE_PORT=8108
TYPESENSE_API_KEY=local-dev-key-change-me
DATABASE_URL=postgresql://isp:isp-dev-password@isp-db:5432/isp
VOYAGE_API_KEY=<your-voyage-key>
HOST=0.0.0.0
PORT=8001
```
