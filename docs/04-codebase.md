# Codebase Structure

## Repository Layout

```
intelligent-search-platform/
├── backend/
│   ├── extensions/                     # JS transform extensions (source of truth)
│   │   ├── catalog_mapper.js           # REST API → catalog schema mapper (run_order 0)
│   │   ├── version_score.js            # version/badge popularity boost (run_order 1)
│   │   └── merchandising_rules.js      # query-aware boost_queries stamping (run_order 2)
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
│           ├── RebuildPanel.svelte     # Rebuild modal: sitemap form OR curl paste + REST form + progress
│           ├── CreateWizard.svelte     # 4-step wizard to add a new source
│           ├── IndexDetail.svelte      # Tabbed detail view: Overview / Connector / Mapping / Fields / Documents / Search / API / Suggestions / Personalisation / Boost Rules
│           ├── FieldsTab.svelte        # Fields tab: table of Typesense fields with Searchable / Facet / Multi-value toggles
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

`_seed_extensions_from_files()` scans `settings.extensions_dir` (default `/app/extensions`) for `*.js` files, upserts each using name as the merge key, and auto-assigns them based on header annotations:

| Header annotation | Effect |
|---|---|
| `// @name …` | Display name (used as the upsert key) |
| `// @description …` | One-line description |
| `// @type transform` | Default. Per-item extension; function is `transform(doc)` |
| `// @type pre_processor` | Full-response extension; function is `pre_process(response)`. Never auto-assigned — must be manually assigned via the Extensions tab |
| `// @assign external` | Auto-assign to catalog write-back indexes only (default for `transform` type) |
| `// @assign all` | Auto-assign to every index |
| `// @assign none` | Skip auto-assignment entirely |
| `// @run_order N` | Execution order within the same stage (lower = first) |

This makes the git repo the single source of truth for extension code — no manual DB management needed.

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
| `IndexMeta` | Full source metadata (id, name, connectors, external_*, doc_count, field_config) |
| `IndexCreate` | Request body for creating an index (name, description) |
| `RestConnectorConfig` | REST connector (url, method, headers, body, field_map, field shortcuts) |
| `SitemapConnectorConfig` | Sitemap connector (url, max_pages, selectors, patterns) |
| `IngestStatus` | Job status (state, total, indexed, error) |
| `SearchRequest` | Search request (q, page, per_page, mode, `filters: dict[str, list[str]]`) |
| `DocumentItem` | Search result shape (id, title, content, url, source_type, indexed_at) |
| `FieldUpdate` | PATCH body for field properties (facet, multi_value, searchable — all optional booleans) |

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
| POST | `/v1/indexes/{id}/connectors/sitemap` | Replace sitemap connector config (no crawl); replaces existing sitemap connector in-place |
| POST | `/v1/indexes/{id}/ingest/rest` | Start REST ingest (background) |
| POST | `/v1/indexes/{id}/ingest/sitemap` | Start sitemap ingest (background) |
| POST | `/v1/indexes/{id}/rebuild` | Rebuild source (background); accepts `RestConnectorConfig` or `SitemapConnectorConfig`; **409** if already running |
| GET | `/v1/indexes/{id}/status` | Poll ingest/rebuild job state |
| GET | `/v1/indexes/{id}/documents` | List documents — includes `vec` (paginated) |
| GET | `/v1/indexes/{id}/documents/public` | List documents — `vec` excluded (external consumers) |
| PATCH | `/v1/indexes/{id}/documents/{doc_id}` | Update editorial fields without re-indexing |
| GET | `/v1/indexes/{id}/fields` | List all Typesense fields merged with stored `field_config` |
| PATCH | `/v1/indexes/{id}/fields/{field_name}` | Toggle facet / multi_value / searchable for a field; persists to `field_config` |

Key internal functions:
- `_resolve_doc_id(raw_id, fallback)` — returns a Typesense-safe document ID. If the extension provides a clean string (alphanumeric, hyphens, underscores) it is used as-is so external analytics systems can reference documents by their native key (e.g. SKU, familyCode). Integer or special-character IDs are hashed to a stable 16-char hex string as a fallback.
- `_generic_catalog_mapper(item, idx)` — passes the full extension output through as-is. No field names hardcoded; all fields land in the catalog collection via the `.*` wildcard. The extension is the single source of truth for field names and values.
- `_ensure_isp_schema(docs)` — normalises docs for ISP-managed collections. Promotes every scalar extra field to top-level via `.*` wildcard. Uses `_resolve_doc_id` for ID resolution.
- `_map_external_doc(raw, meta)` — converts raw Typesense doc to ISP DocumentItem shape for search results.
- `_run_ingest(idx_id, connector_cfg)` — background task: acquire job lock → heartbeat → fetch → extensions → embed → upsert → release lock.
- `_run_rebuild(idx_id, cfg_dict)` — background task: acquire job lock → heartbeat → three branches: catalog write-back (REST), ISP-managed sitemap (crawl via `sitemap_connector`), ISP-managed REST; all branches embed → drop+recreate collection → upsert → re-apply `field_config` → release lock.
- `_list_docs(idx_id, page, per_page, exclude_vec)` — shared helper for `/documents` and `/documents/public`.

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
| POST | `/v1/indexes/{id}/search` | Search index (hybrid/lexical/semantic) — includes `vec` |
| POST | `/v1/indexes/{id}/search/public` | Same search — `vec` excluded (external consumers) |

Both routes share `_run_search(exclude_vec)` internally. Ranking is identical; the vector is used for candidate retrieval inside ISP and stripped before the response is serialised.

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
- `list_extensions()`, `get_extension(id)`, `create_extension(..., ext_type)`, `update_extension(..., ext_type)`, `delete_extension(id)`
- `upsert_extension_by_name(name, desc, code, ext_type)` — create or update matched by name (used by file seed)
- `list_index_extensions(idx_id)` — JOIN query returning all fields including `type`, ordered by `run_order`
- `assign_extension(idx_id, ext_id, run_order, enabled)` — full upsert (updates existing)
- `assign_extension_if_new(idx_id, ext_id, run_order)` — `ON CONFLICT DO NOTHING` (startup seeding)
- `unassign_extension(idx_id, ext_id)`

### `services/extension_runner.py`
Executes JS extensions in V8 isolates via `py-mini-racer`. Two separate execution stages:

**Stage 1 — pre-processor (full response):**
- `_apply_pre_processor_extensions_sync(response, exts)` — filters to `type == 'pre_processor'` records, compiles each, calls `pre_process(response)` in `run_order` sequence; output of one becomes input of next.
- `apply_pre_processor_extensions(response, exts)` — async wrapper.

**Stage 2 — transform (per item):**
- `_run_sync(docs, extension_records)` — filters to `type == 'transform'` records, compiles each into its own `MiniRacer` context (once per batch), applies `transform(doc)` to every document in sequence.
- `apply_extensions(docs, extension_records)` — async wrapper.

**Shared behaviour:**
- If `py-mini-racer` is not installed, both stages are silently skipped with a warning log.
- A compile error skips that extension for the entire batch (logged as ERROR).
- A runtime error on a single item/response falls back to the previous state and logs an ERROR — the rebuild continues.

**Inline pre-processor (connector config):**
- `run_pre_processor(code, response)` — runs a single JS code string (the `pre_processor` field on `RestConnectorConfig`). Runs before file-based pre-processor extensions.

### `services/typesense_client.py`
All Typesense API calls. Key functions:
- `create_collection(id)` / `drop_collection(id)` — ISP-managed collection lifecycle
- `upsert_documents(id, docs)` — ISP-managed collections
- `upsert_to_collection(col, docs)` — any collection (write-back to `catalog`)
- `search_documents(id, q, mode, page, per_page, exclude_vec, query_by)` — lexical search; `query_by` is derived from `field_config.searchable` at request time
- `search_with_vec(id, q, vec, page, per_page, exclude_vec, query_by)` — hybrid RRF (ISP collections); applies `is_featured`, `popularity`, `boost_queries_equal`, `boost_queries_contains` boosts after RRF; `query_by_weights` is computed dynamically (2 for title, 1 for all others)
- `search_external(col, q, filter_by, query_by, vec, page, per_page, exclude_vec)` — hybrid RRF (external collections); same boost logic
- `list_documents(id, page, per_page, exclude_vec)` — list from ISP collection
- `list_documents_external(col, filter_by, query_by, page, per_page, exclude_vec)` — list from external collection
- `get_all_fields(index_id)` — reads live Typesense schema; returns list of `{name, type, facet, multi_value, optional, system}` (system=True for `id`, `vec`, `indexed_at`)
- `update_field_properties(index_id, field_name, *, facet, multi_value, current_type)` — drop + re-add field in Typesense schema without losing document data; changes `facet` flag and converts `string` ↔ `string[]` for multi-value

All read functions accept `exclude_vec=False` (default).  Pass `True` for public-facing calls to strip the `vec` field via Typesense `exclude_fields: "vec"`.

### `services/infra/job_lock.py`
Redis-backed distributed mutex preventing concurrent ingest/rebuild runs across multiple ISP instances:
- `acquire(idx_id)` — `SET NX EX 600`; returns a token string on success or `None` if another instance holds the lock. Degrades gracefully (returns token) when Redis is unreachable.
- `release(idx_id, token)` — Lua compare-and-delete; only the lock holder can release.
- `heartbeat(idx_id, token)` — async coroutine; extends TTL by 10 minutes every 5 minutes until cancelled. Run as an `asyncio.Task` alongside the background job.
- `release_stale_for_instance()` — called at startup; scans `isp:job:*` keys and deletes any whose value starts with this container's hostname, clearing orphaned locks from a prior crash.
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
- Field management: `getFields(indexId)`, `updateField(indexId, fieldName, patch: Partial<FieldConfig>)`

### `lib/types.ts` — TypeScript Interfaces
Mirrors Pydantic models: `IndexMeta`, `RestConnectorConfig`, `IngestStatus`, `DocumentItem`, `ExtensionMeta`, `IndexExtension`, `FieldEntry`, `FieldConfig`.

`FieldEntry` — single field row returned by `GET /fields`:
```typescript
interface FieldConfig  { facet: boolean; multi_value: boolean; searchable: boolean; }
interface FieldEntry extends FieldConfig {
  name: string; type: string; optional: boolean; system: boolean;
}
```

### `components/sources/FieldsTab.svelte` — Fields Tab
Renders a table of all Typesense fields for the selected index with toggle controls.

- On mount: `GET /v1/indexes/{id}/fields` → populates field rows
- Three toggle columns per field: **Searchable**, **Facet**, **Multi-value**
- System fields (`id`, `vec`, `indexed_at`): greyed out at 50% opacity, all toggles disabled
- Core fields (`title`, `content`): always-searchable badge; searchable toggle locked ON
- Multi-value toggle: only shown for `string` and `string[]` type fields
- Optimistic UI: toggle fires immediately, spinner shown while PATCH is in flight, reverts on error
- Per-row error display below each row that fails
- `isBusy(fieldName, prop)` checks `saving["{name}:{prop}"]` — kept as a script-block function (Svelte 4 `{@const}` does not support arrow functions)
- Changes persist across rebuilds via `field_config` in PostgreSQL

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

---

## References

> Citations below are sourced from training knowledge (not live browsing). DOI and arXiv links are provided where known.

1. **Fowler, M.** (2002). *Patterns of Enterprise Application Architecture*. Addison-Wesley. ISBN: 0-321-12742-0 — Defines the Service Layer, Repository, and Data Mapper patterns that structure ISP's `services/` and `routers/` separation; each router delegates all I/O to domain services.

2. **Gamma, E., Helm, R., Johnson, R., & Vlissides, J.** (1994). *Design Patterns: Elements of Reusable Object-Oriented Software*. Addison-Wesley. ISBN: 0-201-63361-2 — "Gang of Four" patterns; ISP uses the Singleton pattern (module-level `_pool`, `_client` globals) and the Strategy pattern (embedding provider selection by model id).

3. **Richardson, C.** (2018). *Microservices Patterns*. Manning Publications. ISBN: 978-1-617-29454-1 — Microservice decomposition principles; ISP is a micro-monolith (all services in one process) but applies the same domain-service / infra-service boundary as microservice patterns recommend.

4. **Wynne, M., & Hellesøy, A.** (2017). *The Cucumber Book: Behaviour-Driven Development for Testers and Developers*. Pragmatic Bookshelf — Background on background tasks and async pipelines; ISP's `BackgroundTasks` ingest model follows the same separation-of-concerns principle.

5. **Merkel, D.** (2014). "Docker: Lightweight Linux Containers for Consistent Development and Deployment." *Linux Journal*, 2014(239). ISSN: 1075-3583 — Docker Compose enables the multi-service dev environment documented in `backend/.env` and `docker-compose.yml`.

6. **Dahl, R., & Schlueter Langford, I.** (2013). *Node.js in Action*. Manning Publications. ISBN: 978-1-617-29036-9 — Background on V8 JavaScript engine; PyMiniRacer embeds V8 to execute JS extensions in sandboxed isolates without Node.js APIs.
