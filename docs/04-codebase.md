# Codebase Structure

## Repository Layout

```
intelligent-search-platform/
├── backend/
│   ├── extensions/                     # JS transform extensions (source of truth)
│   │   └── tmobile_catalog.js          # T-Mobile API → catalog schema mapper
│   ├── src/isp/
│   │   ├── main.py                     # FastAPI app, lifespan, CORS, router registration
│   │   ├── config.py                   # Settings from env vars (.env)
│   │   ├── models.py                   # Pydantic models (request/response shapes)
│   │   ├── routers/
│   │   │   ├── indexes.py              # /v1/indexes/* — CRUD + ingest + rebuild
│   │   │   ├── search.py               # /v1/indexes/{id}/search
│   │   │   ├── catalog.py              # /v1/catalog — PLP product search + facets + suggest
│   │   │   ├── content.py              # /v1/content/{slug} — generic content API
│   │   │   └── extensions.py           # /v1/extensions — JS transform library management
│   │   └── services/
│   │       ├── database.py             # asyncpg pool + init_db() (table creation)
│   │       ├── index_service.py        # PostgreSQL CRUD for indexes/status/splits/discovery
│   │       ├── extension_service.py    # PostgreSQL CRUD for extensions + index assignments
│   │       ├── extension_runner.py     # V8 JS execution via PyMiniRacer
│   │       ├── typesense_client.py     # All Typesense operations
│   │       ├── embedding_service.py    # Voyage AI + two-tier cache (LRU + Redis)
│   │       ├── rest_connector.py       # Fetch + parse REST API data
│   │       └── sitemap_connector.py    # Crawl + parse sitemaps
│   ├── pyproject.toml                  # Dependencies (py-mini-racer, voyageai, asyncpg, ...)
│   ├── Dockerfile
│   └── .env                            # Environment variables
│
├── frontend/
│   └── src/
│       ├── pages/index.astro           # Entry point, mounts App component
│       ├── lib/
│       │   ├── api.ts                  # All fetch calls to backend API
│       │   ├── store.ts                # Svelte stores (appState, indexes, loading, error)
│       │   └── types.ts                # TypeScript interfaces
│       └── components/
│           ├── App.svelte              # Root: layout, routing, delete modal
│           ├── Header.svelte           # Top nav bar
│           ├── SourcesSidebar.svelte   # Left sidebar: source list + "⚙ Extensions" footer
│           ├── SourceContent.svelte    # Main panel: docs list + search + "⚙ Extensions" button
│           ├── RebuildPanel.svelte     # Rebuild modal: curl paste + form + progress
│           ├── CreateWizard.svelte     # 4-step wizard to add a new source
│           ├── IndexDetail.svelte      # Configure tab (connector settings)
│           ├── ExtensionsPanel.svelte  # Global extension library (list/create/edit/delete)
│           └── IndexExtensionsPanel.svelte  # Per-index extension assignment overlay
│
├── docs/                               # Design documents (this folder)
├── docker-compose.yml
├── deploy-all.sh                       # Start everything in order
├── deploy-api.sh                       # Rebuild + restart backend
├── deploy-ui.sh                        # Build + restart frontend
└── deploy-db.sh                        # Start + wait for PostgreSQL
```

---

## Backend: Key Files

### `main.py`
FastAPI application entry point.

```python
@asynccontextmanager
async def lifespan(app):
    await init_db()                    # create tables on startup
    _ensure_catalog_collection()       # create/verify catalog Typesense collection
    await _seed_extensions_from_files()# load *.js → DB → auto-assign to external indexes
    yield

app = FastAPI(lifespan=lifespan)
app.include_router(indexes.router,    prefix="/v1")
app.include_router(search.router,     prefix="/v1")
app.include_router(content.router,    prefix="/v1")
app.include_router(catalog.router,    prefix="/v1")
app.include_router(extensions.router, prefix="/v1")
```

`_seed_extensions_from_files()` scans `settings.extensions_dir` (default `/app/extensions`) for `*.js` files, upserts each using name as the merge key, and assigns them to all existing external indexes. This makes the git repo the single source of truth for extension code — no manual DB management needed.

### `config.py` — Settings
All configuration is read from environment variables or `.env`:

| Setting | Default | Purpose |
|---|---|---|
| `typesense_host` | `typesense` | Typesense container hostname |
| `typesense_port` | `8108` | Typesense port |
| `typesense_api_key` | `local-dev-key-change-me` | Typesense auth key |
| `database_url` | `postgresql://...@isp-db:5432/isp` | asyncpg DSN |
| `voyage_api_key` | `""` | Voyage AI key (empty = lexical only) |
| `redis_url` | `redis://isp-redis:6379/0` | Embedding cache L2 |
| `embed_cache_ttl` | `86400` | Redis TTL in seconds (24h) |
| `embed_lru_size` | `2048` | In-memory LRU slots per pod |
| `extensions_dir` | `/app/extensions` | Directory scanned for *.js files |

### `models.py` — Core Pydantic Models

| Model | Purpose |
|---|---|
| `IndexMeta` | Full source metadata (id, name, connectors, external_*, doc_count) |
| `IndexCreate` | Request body for creating an index (name, description) |
| `RestConnectorConfig` | REST connector (url, method, headers, body, field_map, field shortcuts) |
| `SitemapConnectorConfig` | Sitemap connector (url, max_pages, selectors, patterns) |
| `IngestStatus` | Job status (state, total, indexed, error) |
| `SearchRequest` | Search request (q, page, per_page, mode) |
| `DocumentItem` | Search result shape (id, title, content, url, source_type, indexed_at) |

`RestConnectorConfig.field_map` — optional source→target field renames applied before indexing. Setting a target to `""` drops the field. Special target names `title`, `content`, `url`, `id` override the corresponding core field shortcuts.

---

## Backend: Routers

### `routers/indexes.py` — Endpoints

| Method | Path | Action |
|---|---|---|
| GET | `/v1/indexes` | List all sources |
| POST | `/v1/indexes` | Create source + provision Typesense collection |
| GET | `/v1/indexes/{id}` | Get source metadata |
| DELETE | `/v1/indexes/{id}` | Delete source + Typesense data |
| POST | `/v1/indexes/discover` | Auto-import Typesense collections + auto-assign extensions |
| GET | `/v1/indexes/{id}/status` | Poll ingest job status |
| POST | `/v1/indexes/{id}/connectors/rest` | Save REST connector config (no ingest) |
| POST | `/v1/indexes/{id}/connectors/sitemap` | Save sitemap connector config (no crawl) |
| POST | `/v1/indexes/{id}/ingest/rest` | Start REST ingest (background) |
| POST | `/v1/indexes/{id}/ingest/sitemap` | Start sitemap ingest (background) |
| POST | `/v1/indexes/{id}/rebuild` | Rebuild source (background) |
| GET | `/v1/indexes/{id}/documents` | List documents (paginated) |

Key internal functions:
- `_generic_catalog_mapper(item, idx)` — packages extension output (flat dict) into Typesense catalog schema. Makes no assumptions about API field names; that is the extension's job.
- `_map_external_doc(raw, meta)` — converts raw Typesense doc to ISP DocumentItem shape for search results.
- `_run_ingest(idx_id, connector_cfg)` — background task: fetch → extensions → embed → upsert.
- `_run_rebuild(idx_id, cfg, type_label)` — background task for rebuilds; handles both catalog write-back and ISP-managed paths.

### `routers/catalog.py` — PLP Product Search

| Method | Path | Action |
|---|---|---|
| GET | `/v1/intent/tab` | Detect whether query is device or accessory |
| POST | `/v1/catalog` | Hybrid product search (PLP) |
| GET | `/v1/catalog/facets` | Manufacturer / category / price facets |
| GET | `/v1/suggest` | Typeahead autocomplete |

URL in search results is read directly from `metadata.url` — set by the JS extension during rebuild. No URL base constants exist in Python.

### `routers/extensions.py` — Extension Library

| Method | Path | Action |
|---|---|---|
| GET | `/v1/extensions` | List all extensions |
| POST | `/v1/extensions` | Create a new extension |
| GET | `/v1/extensions/{id}` | Get one extension |
| PUT | `/v1/extensions/{id}` | Update name/description/code |
| DELETE | `/v1/extensions/{id}` | Delete (cascades index assignments) |
| GET | `/v1/indexes/{id}/extensions` | List extensions assigned to an index |
| POST | `/v1/indexes/{id}/extensions` | Assign extension to index |
| DELETE | `/v1/indexes/{id}/extensions/{ext_id}` | Unassign extension |

### `routers/search.py` — Index Search

| Method | Path | Action |
|---|---|---|
| POST | `/v1/indexes/{id}/search` | Search index (hybrid/lexical/semantic) |

### `routers/content.py` — Generic Content API

| Method | Path | Action |
|---|---|---|
| GET | `/v1/content` | List all sources with their slugs |
| GET | `/v1/content/{slug}` | List documents for a source |
| GET | `/v1/content/{slug}/search` | Search within a source |

Slugs are auto-derived from index names: `"Smart Watches"` → `"smart-watches"`.

---

## Backend: Services

### `services/database.py`
Manages the asyncpg connection pool and `init_db()` which creates all five tables (`indexes`, `ingest_status`, `deleted_splits`, `extensions`, `index_extensions`) idempotently on startup.

### `services/index_service.py`
PostgreSQL CRUD for the primary metadata tables:
- `list_indexes()`, `get_index(id)`, `create_index(name, desc)`, `update_index(meta)`, `delete_index(id)`
- `get_status(id)`, `set_status(status)` — ingest_status table
- `discover_external_collections()` — scan Typesense, match against `_KNOWN_SPLITS`, create ISP entries

`_KNOWN_SPLITS` maps Typesense collection names to named splits with display names, descriptions, and filter expressions. Add new catalog splits here when new external collections are deployed.

### `services/extension_service.py`
PostgreSQL CRUD for the extension library and index assignments:
- `list_extensions()`, `get_extension(id)`, `create_extension(...)`, `update_extension(...)`, `delete_extension(id)`
- `upsert_extension_by_name(name, desc, code)` — create or update matched by name (used by file seed)
- `list_index_extensions(idx_id)` — JOIN query, ordered by `run_order`
- `assign_extension(idx_id, ext_id, run_order, enabled)` — full upsert (updates existing)
- `assign_extension_if_new(idx_id, ext_id, run_order)` — `ON CONFLICT DO NOTHING` (startup seeding)
- `unassign_extension(idx_id, ext_id)`

### `services/extension_runner.py`
Executes JS extensions in V8 isolates via `py-mini-racer`:
- One `MiniRacer` context per extension, compiled once and reused across all documents in a batch.
- `_run_sync(docs, extension_records)` — synchronous; compiles extensions, applies each `transform(doc)` call to every document in sequence.
- `apply_extensions(docs, extension_records)` — async wrapper that runs `_run_sync` in a thread executor to avoid blocking the asyncio event loop.
- If `py-mini-racer` is not installed, extensions are silently skipped with a warning log.
- If a single extension raises a runtime error for a document, that document is kept in its pre-extension state and the error is logged.

### `services/typesense_client.py`
All Typesense API calls. Key functions:
- `create_collection(id)` / `drop_collection(id)` — ISP-managed collection lifecycle
- `upsert_documents(id, docs)` — ISP-managed collections
- `upsert_to_collection(col, docs)` — any collection (write-back to `catalog`)
- `search_documents(id, q, mode, page, per_page)` — lexical search
- `search_with_vec(id, q, vec, page, per_page)` — hybrid RRF (ISP collections)
- `search_external(col, q, filter_by, query_by, vec, page, per_page)` — hybrid RRF (external collections)
- `list_documents(id, page, per_page)` — list from ISP collection
- `list_documents_external(col, filter_by, query_by, page, per_page)` — list from external collection
- `delete_from_collection(col, filter_by)` — delete by filter
- `list_all_collections()` — used by auto-discovery
- `count_external(col, filter_by)` — count for doc_count display

### `services/embedding_service.py`
Voyage AI `voyage-3` embedding with a two-tier cache:
- **L1**: In-process `cachetools.LRUCache` (2048 slots by default, configurable)
- **L2**: Redis with 24h TTL (optional; silently skipped if Redis is unavailable)
- Vectors serialised to bytes with `struct.pack` for compact Redis storage
- `embed_texts(texts)` — batch embed for ingest (128 texts/request max)
- `embed_query(text)` — single embed for search queries (uses `input_type="query"` for asymmetric retrieval)
- Cache key: `emb:{sha256(input_type:text)}`

### `services/rest_connector.py`
REST API data fetching:
- `fetch_raw_items(cfg)` — raw items with no field-name mapping (used for catalog write-back path)
- `fetch_and_parse(cfg)` — fetch + apply `field_map` renames + auto-detect ISP doc fields → `DocumentItem` shape
- `_auto_detect_path(data)` — finds first non-empty array in a dict response
- `_auto_detect_fields(item)` — heuristic field detection using name patterns
- `_apply_field_map(item, field_map)` — rename/drop fields per connector config

---

## Frontend: Key Files

### `lib/store.ts` — Global State
```typescript
appState: { selectedIndexId, view, detailTab }
indexes: IndexMeta[]
loading: boolean
error: string | null
```

`view` values: `'indexes'` | `'extensions'`

### `lib/api.ts` — Backend Client
All calls go to `PUBLIC_API_URL` (set at Astro build time). Key methods:
- Source management: `listIndexes`, `createIndex`, `deleteIndex`, `getStatus`, `discoverIndexes`
- Ingest: `ingestRest`, `rebuild`
- Search: `search`, `listDocuments`
- Extensions: `listExtensions`, `createExtension`, `updateExtension`, `deleteExtension`
- Index assignments: `listIndexExtensions`, `assignExtension`, `unassignExtension`
- Catalog: `searchCatalog`, `getCatalogFacets`, `getSuggestions`, `getIntentTab`

### `lib/types.ts` — TypeScript Interfaces
Mirrors Pydantic models: `IndexMeta`, `RestConnectorConfig`, `IngestStatus`, `DocumentItem`, `ExtensionMeta`, `IndexExtension`.

### `App.svelte` — Root Component
- Owns the layout (Header + Sidebar + Main)
- Handles source selection, delete confirmation modal
- Routes between `view='indexes'` (normal) and `view='extensions'` (global extensions panel)
- Calls `loadIndexes()` + `discoverIndexes()` on mount

### `SourceContent.svelte` — Main Panel
- Loads documents on source switch (triggers by ID change, not object reference)
- Search fires only on Enter or Search button click
- Document expand (+/-) shows all raw fields including vec
- "⚙ Extensions" button opens `IndexExtensionsPanel` overlay

### `RebuildPanel.svelte` — Rebuild UI
- Curl paste parser auto-fills all form fields
- Pre-populates from saved `type:rest` connector in PostgreSQL
- Shows live progress (queued → fetching → embedding → indexing → done)

### `CreateWizard.svelte` — 4-Step Wizard
- Step 1: Name + description → creates index in PostgreSQL
- Step 2: REST (URL, method, headers, body) or Sitemap connector config
- Step 3: Field mapping — source→target renames with legend explaining source (purple) and target (green) badges
- Step 4: Progress polling → "View Index"

### `ExtensionsPanel.svelte` — Global Extension Library
- List all extensions (name, description, created/updated timestamps)
- Create new extension with code editor textarea and `DEFAULT_CODE` template
- Edit existing extension inline
- Delete extension (with cascade warning)
- Accessible from sidebar footer "⚙ Extensions"

### `IndexExtensionsPanel.svelte` — Per-Index Assignment Overlay
- Shows extensions currently assigned to the selected index with `run_order`
- ↑↓ reorder (adjusts `run_order`), On/Off toggle (updates `enabled`), ✕ remove
- Shows library extensions not yet assigned with "+ Add" button
- Opens from "⚙ Extensions" button in SourceContent header

---

## Environment Variables (backend/.env)

```env
TYPESENSE_HOST=typesense
TYPESENSE_PORT=8108
TYPESENSE_API_KEY=local-dev-key-change-me
DATABASE_URL=postgresql://isp:isp-dev-password@isp-db:5432/isp
VOYAGE_API_KEY=<your-voyage-key>
REDIS_URL=redis://isp-redis:6379/0
EMBED_CACHE_TTL=86400
EMBED_LRU_SIZE=2048
EXTENSIONS_DIR=/app/extensions
HOST=0.0.0.0
PORT=8001
```
