# Component Integrations

## How All Pieces Connect

```
┌──────────────────────────────────────────────────────────────────────┐
│                         User Browser                                 │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   isp-ui :3001    │
                    │  (Astro/Svelte)   │
                    └─────────┬─────────┘
                              │ REST /v1/*
                    ┌─────────▼─────────┐
                    │   isp-api :8001   │◄──── .env config
                    │   (FastAPI)       │
                    └──┬──────┬─────┬───┘
                       │      │     │
          ┌────────────▼┐  ┌──▼───┐ └──────────────────┐
          │  isp-db     │  │Voyage│                     │
          │  Postgres   │  │AI API│              ┌──────▼──────┐
          │  :5432      │  │cloud │              │  Typesense  │
          │             │  └──────┘              │  :8108      │
          │ • indexes   │        ┌──────────┐    │             │
          │ • status    │        │isp-redis │    │ • isp_{id}  │
          │ • splits    │        │:6379     │    │   (one per  │
          │ • extensions│        │(emb cache│    │    source)  │
          │ • idx_exts  │        │L2)       │    │ • catalog   │
          └─────────────┘        └──────────┘    │   (external)│
                                                 └─────────────┘
```

---

## Integration 1: Frontend ↔ Backend

**Protocol:** HTTP REST  
**Base URL:** `http://localhost:8001/v1` (set at Astro build time via `PUBLIC_API_URL`)

All API calls are in `frontend/src/lib/api.ts`. The frontend never talks to PostgreSQL, Redis, or Typesense directly — always through the FastAPI backend.

**Key flows:**
```
Frontend                    Backend
   │                           │
   │  GET /v1/indexes          │
   │──────────────────────────►│ → PostgreSQL: SELECT * FROM indexes
   │◄──────────────────────────│
   │  IndexMeta[]              │

   │  POST /v1/indexes/{id}/rebuild  (cfg)
   │──────────────────────────►│ → saves cfg to PostgreSQL immediately
   │◄──────────────────────────│   starts background task
   │  IngestStatus{queued}     │

   │  GET /v1/indexes/{id}/status (polling every 1.2s)
   │──────────────────────────►│ → PostgreSQL: SELECT FROM ingest_status
   │◄──────────────────────────│
   │  IngestStatus{running...} │

   │  GET /v1/indexes/{id}/extensions
   │──────────────────────────►│ → PostgreSQL: JOIN index_extensions + extensions
   │◄──────────────────────────│
   │  [{name, run_order, ...}] │
```

---

## Integration 2: Backend ↔ PostgreSQL

**Library:** `asyncpg` (async, connection pool min=2 max=10)  
**Files:** `services/database.py`, `services/index_service.py`, `services/extension_service.py`

PostgreSQL is used for:
1. **Index metadata** — CRUD on `indexes` table
2. **Connector config persistence** — stored in `connectors` JSONB column
3. **Ingest job tracking** — `ingest_status` table, polled by frontend
4. **Deletion memory** — `deleted_splits` table prevents re-discovery
5. **Extension library** — `extensions` table; seeded from `backend/extensions/*.js`
6. **Per-index assignments** — `index_extensions` join table with `run_order` and `enabled`

**Connection lifecycle:**
```
FastAPI startup (lifespan)
  → asyncpg.create_pool()
  → init_db() — CREATE TABLE IF NOT EXISTS for all 5 tables
  → pool kept alive for app lifetime
  → each request: pool.acquire() → execute → release
```

---

## Integration 3: Backend ↔ Typesense

**Library:** `typesense` Python SDK  
**File:** `services/typesense_client.py`  
**Host:** `typesense:8108` (Docker container name)

Two write paths:

```
ISP-managed source (is_external=false):
  fetch → parse → apply extensions → _ensure_isp_schema
  → embed → upsert to isp_{id} collection
  (promotes popularity + is_featured to top-level fields)

External source write-back (is_external=true, catalog):
  fetch raw → inject _isp_type hint → apply extensions (V8) → generic mapper
  → embed → upsert to catalog collection
```

Two read paths:

```
ISP collection search (admin UI):
  search_with_vec(id, q, vec, ...) → multi_search → RRF merge

Catalog / PLP search:
  _get_isp_collections() → discover all isp_* collections
  multi_search across ALL isp_* → RRF merge
  + editorial boosts (is_featured +0.5, popularity scaled)
  (PLP no longer reads from catalog collection directly)

External collection search (admin UI only):
  search_external(col, q, filter_by, query_by, vec, ...) → same RRF logic
  _wrap_external_results() → maps to ISP DocumentItem shape
```

Partial update (admin UI edit modal):
```
PATCH /v1/indexes/{id}/documents/{doc_id}
  → update_document_fields(col, doc_id, {popularity, is_featured, metadata})
  → action=update (only supplied keys overwritten, vector unchanged)
```

---

## Integration 4: Backend ↔ Voyage AI

**Library:** `voyageai` Python SDK  
**File:** `services/embedding_service.py`  
**API:** Cloud (requires `VOYAGE_API_KEY`)  
**Model:** voyage-3 (1024-dimensional vectors)

Called during ingest/rebuild for document embedding:
```python
texts = [f"{doc['title']} {doc['content']}" for doc in docs]
vectors = await embedding_service.embed_texts(texts)
for doc, vec in zip(docs, vectors):
    if vec:
        doc["vec"] = vec  # added before Typesense upsert
```

Called during search (hybrid/semantic mode):
```python
vec = await embedding_service.embed_query(query)
# vec used in vector_query parameter to Typesense
```

**Two-tier embedding cache** (avoids redundant API calls):
```
Search query arrives
  ↓
L1: in-process LRU cache (2048 slots, configurable)
  hit  → return immediately
  miss ↓
L2: Redis cache (24h TTL, configurable)
  hit  → populate L1, return
  miss ↓
Voyage AI API call
  → populate L1 and L2
```

If `VOYAGE_API_KEY` is empty, embedding returns `[]` and ISP falls back to lexical-only search.

---

## Integration 5: Extension Pipeline

Extensions are JavaScript `transform(doc)` functions that run between data fetch and Typesense upsert.

**Execution model:**
```
V8 isolate per extension (PyMiniRacer)
  — no filesystem, no network, no Node.js APIs
  — same sandboxing approach as Coveo IPE
  — compiled once per extension per ingest batch
  — executed for every document in the batch

Thread executor (asyncio)
  — V8 execution is synchronous/blocking
  — run in loop.run_in_executor(None, ...) to avoid blocking the event loop
```

**Full pipeline (catalog write-back):**
```
1. fetch_raw_items(cfg)          — raw dicts from REST API
2. item.setdefault("_isp_type", type_label)
                                  — inject device/accessory hint for JS
3. apply_extensions(docs, ext_records)
                                  — V8 isolates transform each doc
                                  — output: flat dict with standard keys
                                    (display, type, url, manufacturer, price, ...)
4. _generic_catalog_mapper(item, idx)
                                  — packages flat dict → Typesense catalog schema
                                  — metadata stored as JSON string
5. embed_texts([display + description])
                                  — Voyage AI → vec
6. upsert_to_collection("catalog", docs)
                                  — write to shared Typesense collection
```

**File-based source of truth:**
```
git commit backend/extensions/my_mapper.js
  ↓
deploy-api.sh (docker build + restart)
  ↓
lifespan._seed_extensions_from_files()
  ↓
upsert_extension_by_name() — create or update matched by @name header
  ↓
assign_extension_if_new() for every external index
  (ON CONFLICT DO NOTHING — won't re-enable user-disabled extensions)
```

---

## Integration 6: ISP ↔ T-Mobile PLP

ISP indexes Phones and Accessories into ISP-managed `isp_*` collections.  The T-Mobile PLP reads product data via `/v1/catalog` which dynamically searches all `isp_*` collections.

```
ISP Rebuild (Phones / Accessories — ISP managed)
  → fetch from T-Mobile product API
  → _infer_isp_type() → inject _isp_type = "device" | "accessory"
  → apply T-Mobile Catalog Mapper extension (JS)
      — maps familyName → title
      — builds URL: https://www.t-mobile.com/cell-phone/{urlSlug}
      — extracts price, images, SKUs from nested response
      — sets popularity (rank-based) and is_featured (top-5 rank)
  → _ensure_isp_schema() → ISP collection schema
      — packs images/price/manufacturer into metadata JSON
      — promotes popularity + is_featured to top-level fields
  → embed → drop + recreate isp_{id} → upsert all docs

T-Mobile PLP (localhost:3000)
  → POST /v1/catalog {q:"iphone", type:"device", search_mode:"hybrid"}
  → _get_isp_collections() → discovers isp_Phones + isp_Accessories
  → multi_search: 2 lex + 2 sem passes in one request
  → RRF merge + editorial boosts
  → paginated CatalogItem list (with images, price, manufacturer from metadata)
```

URL ownership: the JS extension computes and returns `url` in its output.  Python never constructs T-Mobile URLs — any URL format change only requires updating the extension JS.

---

## Integration 7: Auto-Discovery Flow

On every app load, ISP calls `POST /v1/indexes/discover`:

```
Typesense: list all collections
      │
      ▼
Skip isp_* collections (ISP-managed, not external)
      │
      ▼
Check deleted_splits in PostgreSQL → skip deleted ones
      │
      ▼
Check _KNOWN_SPLITS mapping:
  "catalog" → [("Phones",      filter="type:=device"),
               ("Accessories", filter="type:=accessory")]
  others    → generic name (col_name.title())
      │
      ▼
For each unseen split:
  count_external() from Typesense
  INSERT into PostgreSQL indexes
  auto-assign seeded extension (ON CONFLICT DO NOTHING)
  return newly created IndexMeta[]
      │
      ▼
Frontend reloads index list if any new ones found
```

---

## Deployment Integration

All services started in order by `deploy-all.sh`:

```
1. deploy-db.sh
   └── docker compose up -d isp-db
   └── wait until pg_isready

2. deploy-api.sh
   └── docker compose build isp-api
       (COPY extensions/ extensions/ is in Dockerfile — JS files land at /app/extensions)
   └── docker compose up -d isp-api
       (depends_on isp-db: condition: service_healthy)
       └── FastAPI starts → init_db() → _ensure_catalog_collection()
                         → _seed_extensions_from_files()

3. deploy-ui.sh
   └── npm run build  (Astro static build with PUBLIC_API_URL)
   └── docker compose build isp-ui
   └── docker compose up -d isp-ui
```

Individual scripts for partial deploys:
- `./deploy-db.sh` — database only (rarely needed)
- `./deploy-api.sh` — after any backend Python or extension JS change
- `./deploy-ui.sh` — after any frontend Svelte/TS change
