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
                    └──┬─────┬──────┬───┘
                       │     │      │
          ┌────────────▼┐  ┌─▼────┐ └──────────────┐
          │  isp-db     │  │Voyage│                 │
          │  Postgres   │  │AI API│          ┌──────▼──────┐
          │  :5432      │  │cloud │          │  Typesense  │
          │             │  └──────┘          │  :8108      │
          │ • indexes   │                    │             │
          │ • status    │                    │ • isp_{id}  │
          │ • splits    │                    │ • catalog   │
          └─────────────┘                    └─────────────┘
```

---

## Integration 1: Frontend ↔ Backend

**Protocol:** HTTP REST  
**Base URL:** `http://localhost:8001/v1` (set at Astro build time via `PUBLIC_API_URL`)

All API calls are in `frontend/src/lib/api.ts`. The frontend never talks to PostgreSQL or Typesense directly — always through the FastAPI backend.

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
```

---

## Integration 2: Backend ↔ PostgreSQL

**Library:** `asyncpg` (async, connection pool min=2 max=10)  
**File:** `services/database.py` + `services/index_service.py`

PostgreSQL is used for:
1. **Index metadata** — CRUD on `indexes` table
2. **Connector config persistence** — stored in `connectors` JSONB column
3. **Ingest job tracking** — `ingest_status` table, polled by frontend
4. **Deletion memory** — `deleted_splits` table prevents re-discovery

**Connection lifecycle:**
```
FastAPI startup (lifespan)
  → asyncpg.create_pool()
  → init_db() — CREATE TABLE IF NOT EXISTS for all 3 tables
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
ISP-managed source (REST/Sitemap):
  fetch → parse → embed → upsert to isp_{id} collection

External source write-back (catalog):
  fetch raw → map to catalog schema → embed → upsert to catalog collection
  (so tmo-plp at localhost:3000 sees updated data)
```

Two read paths:

```
ISP collection search:
  search_with_vec(id, q, vec, ...) → multi_search → RRF merge

External collection search:
  search_external(col, q, filter_by, query_by, vec, ...) → same RRF logic
  _wrap_external_results() → maps to ISP DocumentItem shape (passes all fields through)
```

---

## Integration 4: Backend ↔ Voyage AI

**Library:** `voyageai` Python SDK  
**File:** `services/embedding_service.py`  
**API:** Cloud (requires `VOYAGE_API_KEY`)  
**Model:** voyage-3 (1024-dimensional vectors)

Called during ingest and rebuild:
```python
texts = [f"{doc['title']} {doc['content']}" for doc in docs]
vectors = await embedding_service.embed_texts(texts)
for doc, vec in zip(docs, vectors):
    if vec:
        doc["vec"] = vec  # added before Typesense upsert
```

For search (hybrid mode):
```python
vec = await embedding_service.embed_texts([query])
# vec used in vector_query parameter to Typesense
```

If `VOYAGE_API_KEY` is empty, embedding returns `[]` and ISP falls back to lexical-only search.

---

## Integration 5: ISP ↔ T-Mobile PLP (localhost:3000)

ISP can write-back to the shared `catalog` Typesense collection used by the T-Mobile PLP (`tmo-plp`).

```
ISP Rebuild (Phones/Accessories)
  → fetch from T-Mobile product API
  → _map_api_item_to_catalog()  ← maps to catalog schema
  → embed display + description
  → upsert_to_collection("catalog", docs)
         ↓
  T-Mobile PLP (localhost:3000)
  → reads from catalog collection
  → shows updated phones/accessories
```

The catalog schema document shape ISP writes:
```python
{
    "id": f"dev_{md5(familyCode)[:12]}",
    "display": familyName,
    "type": "device" | "accessory",
    "aliases": ["samsung galaxy s25"],
    "tags": ["samsung", "android", "smartphone"],
    "segment": ["all"],
    "popularity": 1000 - deviceRank * 10,
    "is_featured": rank <= 5,
    "created_at": now,
    "updated_at": now,
    "metadata": json.dumps({
        "manufacturer": "Samsung",
        "url_name": "samsung-galaxy-s25",
        "images": ["https://cdn.tmobile.com/..."],
        "color_options": ["Titanium Black"],
        "skus": ["SKU123"],
        "description": "Galaxy S25 by Samsung...",
        "operating_system": "Android",
    }),
    "vec": [0.023, -0.145, ...]   # 1024-dim embedding
}
```

---

## Integration 6: Auto-Discovery Flow

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
  "catalog" → [("Phones", filter="type:=device"),
               ("Accessories", filter="type:=accessory")]
  others    → generic name (col_name.title())
      │
      ▼
For each unseen split:
  count_external() from Typesense
  INSERT into PostgreSQL indexes
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
   └── docker compose up -d isp-api
       (isp-api depends_on isp-db: condition: service_healthy)
       └── FastAPI starts → init_db() → tables created

3. deploy-ui.sh
   └── npm run build  (Astro static build)
   └── docker compose build isp-ui
   └── docker compose up -d isp-ui
```

Individual scripts for partial deploys:
- `./deploy-db.sh` — database only (rarely needed)
- `./deploy-api.sh` — after any backend Python change
- `./deploy-ui.sh` — after any frontend Svelte/TS change
