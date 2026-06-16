# Backend Architecture

**Last Updated**: 2026-06-16

This document defines the structure, layers, and design decisions of the ISP backend: a FastAPI application that indexes content from REST APIs and sitemaps into Typesense, generates vector embeddings, and exposes hybrid search.

---

## Tech Stack

| Concern              | Technology                                    |
| -------------------- | --------------------------------------------- |
| API framework        | FastAPI 0.110 + Uvicorn (Python 3.12)         |
| Relational database  | PostgreSQL 16 via asyncpg (async connection pool) |
| Search engine        | Typesense 0.25 (full-text + vector ANN)       |
| Embeddings           | Voyage AI / OpenAI / RexBERT / custom endpoint |
| Embedding cache      | Redis 7 (L2) + in-process LRU (L1)           |
| JS extension runtime | PyMiniRacer (V8 isolates — no network/FS)    |
| HTTP client          | httpx (REST connector, OpenAI-compat calls)   |
| HTML parsing         | BeautifulSoup4 + lxml (sitemap crawler)       |
| Config               | pydantic-settings (`.env` file + env vars)    |

---

## Data Flow

```
HTTP Request
      │
      ▼
┌─────────────┐
│   Router    │  validates request (Pydantic), calls domain service
│ (routers/)  │
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│  Domain Service │  PostgreSQL CRUD, orchestrates infrastructure services
│(services/domain)│
└──────┬──────────┘
       │
       ├──────────────────────────────────────────┐
       ▼                                          ▼
┌──────────────────┐                   ┌──────────────────────┐
│  typesense_      │                   │  embedding_service   │
│  client.py       │                   │  (Voyage/OpenAI/     │
│  (search, upsert)│                   │   RexBERT/custom)    │
└──────────────────┘                   └────────┬─────────────┘
                                                │
                                       ┌────────┴──────┐
                                       ▼               ▼
                                   Redis (L2)      LRU cache (L1)

       ├──────────────────────────────────────────┐
       ▼                                          ▼
┌──────────────────┐                   ┌──────────────────────┐
│  rest_connector  │                   │  extension_runner    │
│  sitemap_        │                   │  (V8 JS transforms)  │
│  connector       │                   └──────────────────────┘
└──────────────────┘
```

**Rule**: routers never touch PostgreSQL or Typesense directly. All I/O goes through a service module. Infrastructure services (`typesense_client`, `embedding_service`, etc.) are never called from routers — only from domain services or the ingest background task in the router.

---

## Layers

| Layer                    | Responsibility                                                          | Lives in                        |
| ------------------------ | ----------------------------------------------------------------------- | ------------------------------- |
| **Config**               | Load env vars / `.env` into a typed `Settings` object                  | `config.py`                           |
| **Models**               | Pydantic request/response/config shapes shared by routers and services  | `models/` (barrel: `models/__init__.py`) |
| **Router**               | HTTP endpoints, request validation, background task dispatch            | `routers/<domain>.py`                 |
| **Domain Service**       | PostgreSQL CRUD and orchestration logic for one domain                  | `services/domain/<domain>_service.py` |
| **Infrastructure Service** | Thin wrappers around external systems (Typesense, Redis, V8, HTTP)   | `services/infra/<name>.py`            |

---

## Layer Contracts

### Config (`config.py`)

- Single `Settings` class extending `pydantic_settings.BaseSettings`.
- All fields have safe defaults for local Docker development.
- Imported everywhere as the module-level singleton `settings`.
- Never mutated at runtime — runtime-swappable values (embed model, API keys) are stored in `app_settings` (PostgreSQL) and injected into service module-level globals.

```python
# config.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    typesense_host: str = "typesense"
    database_url: str = "postgresql://isp:isp-dev-password@isp-db:5432/isp"
    voyage_api_key: str = ""
    embed_model: str = "voyage-3"
    extensions_dir: str = "/app/extensions"

settings = Settings()
```

### Models (`models/`)

All Pydantic models that cross the router ↔ service boundary are split into domain files under `models/`, with a barrel `__init__.py` that re-exports everything so all callers import from the package root (`from isp.models import IndexMeta`). Three categories:

| Category         | Examples                                                   | Convention            | File               |
| ---------------- | ---------------------------------------------------------- | --------------------- | ------------------ |
| **Request**      | `IndexCreate`, `SearchRequest`, `ConnectorAdd`             | Verb + noun           | `models/index.py`, `models/search.py` |
| **Response**     | `IndexMeta`, `IngestStatus`, `DocumentItem`                | Noun only             | `models/index.py`, `models/search.py` |
| **Config/union** | `RestConnectorConfig`, `SitemapConnectorConfig`, `ConnectorConfig` | Noun + Config | `models/index.py` |

```python
# models/__init__.py — barrel re-export (all existing imports unchanged)
from isp.models.index import (  # noqa: F401
    RestConnectorConfig, SitemapConnectorConfig, ConnectorConfig,
    IndexCreate, ConnectorAdd, IndexMeta, IngestStatus,
)
from isp.models.search import (  # noqa: F401
    SearchRequest, DocumentItem,
)
```

**Rules:**
- Always import from the package root: `from isp.models import IndexMeta` — never from the sub-module directly.
- Add a field here when it appears in an API request or response body.
- Do not define per-router inline models here — only models shared across two or more files.
- Config models (`RestConnectorConfig`, `SitemapConnectorConfig`) are persisted as JSON in the PostgreSQL `connectors` JSONB column; new fields must be optional or have defaults to remain backward-compatible.
- New model files go under `models/<domain>.py`; add re-exports to `models/__init__.py`.

### Router (`routers/`)

- Defines FastAPI endpoints with Pydantic request/response types.
- Validates inputs; raises `HTTPException` for client errors (404, 422, etc.).
- Dispatches long-running work (ingest, rebuild) as `BackgroundTasks` — never `await`s them inline.
- **Never calls `database.py` or `typesense_client.py` directly** — always goes through a domain service.
- Exception: the ingest background task function lives in the router file but delegates all I/O to services.

```python
# routers/indexes.py
@router.post("/{idx_id}/ingest/rest", status_code=202)
async def ingest_rest(idx_id: str, cfg: RestConnectorConfig, bg: BackgroundTasks):
    meta = await index_service.get_index(idx_id)  # ← domain service
    if not meta:
        raise HTTPException(404, "Index not found")
    bg.add_task(_run_ingest, idx_id, cfg)          # ← background task
    return {"status": "queued"}
```

### Domain Service (`services/domain/<domain>_service.py`)

Three domain services own PostgreSQL CRUD for their respective tables:

| Service                 | Tables owned                              | Key responsibilities                                    |
| ----------------------- | ----------------------------------------- | ------------------------------------------------------- |
| `index_service.py`      | `indexes`, `ingest_status`, `deleted_splits` | Index CRUD, status tracking, external collection discovery |
| `extension_service.py`  | `extensions`, `index_extensions`          | Extension library CRUD, per-index assignment           |
| `settings_service.py`   | `app_settings`                            | Embed model catalogue, runtime config persistence      |

**Rules:**
- All PostgreSQL I/O uses `await get_pool()` from `services/infra/database.py` — never creates its own connection.
- Functions are `async def` and return typed values (Pydantic models or plain dicts).
- No FastAPI imports — services are framework-agnostic.
- Row → model conversion is handled by a private `_row_to_meta(row)` helper in the same file.
- Import infra services via `from isp.services.infra import <module>` — never from the flat `isp.services` namespace.

```python
# services/domain/index_service.py
from isp.services.infra.database import get_pool

async def get_index(idx_id: str) -> IndexMeta | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM indexes WHERE id = $1", idx_id)
    return _row_to_meta(dict(row)) if row else None
```

### Infrastructure Service (`services/infra/<name>.py`)

Six infrastructure services each wrap one external system:

| Module                  | Wraps              | Key design note                                                   |
| ----------------------- | ------------------ | ----------------------------------------------------------------- |
| `database.py`           | PostgreSQL (asyncpg) | Lazy singleton pool; `init_db()` called from lifespan            |
| `typesense_client.py`   | Typesense SDK      | Lazy singleton client; all collection/doc operations go here      |
| `embedding_service.py`  | Voyage / OpenAI / RexBERT / custom | Two-tier cache (LRU → Redis); provider selected by model id |
| `extension_runner.py`   | PyMiniRacer (V8)   | One V8 isolate per extension per batch; sync loop in thread pool  |
| `rest_connector.py`     | httpx              | Fetch → extract → field-normalise → `DocumentItem` dicts         |
| `sitemap_connector.py`  | httpx + BeautifulSoup | Fetch sitemap → crawl pages → extract text → `DocumentItem` dicts |
| `job_lock.py`           | Redis (SET NX)     | Distributed mutex preventing concurrent rebuilds across instances |

```python
# routers/indexes.py — correct infra import pattern
from isp.services.domain import index_service, extension_service
from isp.services.infra import embedding_service, typesense_client as ts
from isp.services.infra import rest_connector, sitemap_connector
```

**Rules:**
- Each module exposes only plain functions or async functions — no classes, no FastAPI dependency injection.
- Lazy singletons (`_pool`, `_client`, `_redis_client`) are module-level globals initialised on first use.
- **Never import from `routers/`** — dependency always flows downward.
- `typesense_client` is the only module allowed to call the Typesense SDK.
- `database.py` is the only module allowed to call `asyncpg`.

---

## File Layout

```
backend/
  src/isp/
    __init__.py
    main.py                   ← FastAPI app, lifespan (init_db, seed extensions), router registration
    config.py                 ← pydantic-settings: all env vars with local-dev defaults

    models/                   ← shared Pydantic models split by domain
      __init__.py             ← barrel re-export (from isp.models import X — unchanged for all callers)
      index.py                ← IndexMeta, IndexCreate, IngestStatus, ConnectorConfig, RestConnectorConfig, SitemapConnectorConfig, ConnectorAdd
      search.py               ← SearchRequest, DocumentItem

    routers/
      __init__.py
      indexes.py              ← /v1/indexes — CRUD, ingest/rebuild background tasks, doc listing
      search.py               ← /v1/indexes/{id}/search — hybrid/lexical/semantic search
      content.py              ← /v1/content — public slug-based content API
      catalog.py              ← /v1/catalog — PLP search, facets, typeahead, intent detection
      extensions.py           ← /v1/extensions — global library + per-index assignment
      settings.py             ← /v1/settings — embedding model management

    services/
      __init__.py

      domain/                 ← own PostgreSQL tables; call infra services for external I/O
        __init__.py
        index_service.py      ← indexes + ingest_status + deleted_splits CRUD; auto-discovery
        extension_service.py  ← extensions + index_extensions CRUD; file-based seed helpers
        settings_service.py   ← app_settings CRUD; model catalogue; runtime config sync

      infra/                  ← thin wrappers around external systems; no PostgreSQL ownership
        __init__.py
        database.py           ← asyncpg lazy pool; init_db() schema creation
        typesense_client.py   ← Typesense client; collection provision; search; upsert
        embedding_service.py  ← multi-provider embed (Voyage/OpenAI/RexBERT/custom) + LRU+Redis cache
        extension_runner.py   ← PyMiniRacer V8 sandbox; apply_extensions(docs, extensions)
        rest_connector.py     ← httpx REST fetch; field-map renames; DocumentItem normalisation
        sitemap_connector.py  ← httpx sitemap crawl; BeautifulSoup HTML extraction
        job_lock.py           ← Redis distributed lock for ingest/rebuild (acquire/release/heartbeat)

  extensions/
    tmobile_catalog.js        ← seeded JS extension: T-Mobile API → catalog schema (run_order 0)
    version_score.js          ← seeded JS extension: version/badge popularity boost (run_order 1)
    merchandising_rules.js    ← seeded JS extension: boost_queries_equal/contains stamping (run_order 2)

  Dockerfile                  ← python:3.12-slim; installs via uv; CMD uvicorn isp.main:app
  pyproject.toml              ← dependencies, build config (hatchling)
  .env                        ← local dev secrets (not committed)
```

---

## PostgreSQL Schema

```
indexes
  id              VARCHAR(12) PK
  name            VARCHAR(80)
  description     TEXT
  created_at      VARCHAR(30)
  doc_count       INTEGER
  last_ingested   VARCHAR(30)
  status          VARCHAR(20)          -- ready | running | error
  connectors      JSONB                -- list of RestConnectorConfig / SitemapConnectorConfig
  collection_name VARCHAR(200)         -- 'isp_{id}' for managed; '' for external
  is_external     BOOLEAN
  external_collection  VARCHAR(200)    -- e.g. 'catalog'
  external_filter      VARCHAR(500)    -- e.g. 'type:=device'
  external_query_by    VARCHAR(100)
  external_title_field VARCHAR(100)
  external_url_field   TEXT

ingest_status
  index_id  VARCHAR(12) PK → indexes.id
  state     VARCHAR(20)     -- idle | queued | running | done | error
  total     INTEGER
  indexed   INTEGER
  error     TEXT

deleted_splits
  split_key TEXT PK          -- '{collection}:{filter}' tombstone; prevents re-discovery

extensions
  id          VARCHAR(12) PK
  name        VARCHAR(120)
  description TEXT
  code        TEXT            -- full JS source (must define function transform(doc){…return doc;})
  created_at  VARCHAR(30)
  updated_at  VARCHAR(30)

index_extensions
  index_id     VARCHAR(12) → indexes.id   ON DELETE CASCADE
  extension_id VARCHAR(12) → extensions.id ON DELETE CASCADE
  run_order    INTEGER        -- lower runs first
  enabled      BOOLEAN
  PRIMARY KEY (index_id, extension_id)

app_settings
  key   TEXT PK
  value TEXT
  -- active keys: embed_model, openai_api_key, rexbert_endpoint, rexbert_api_key,
  --              custom_endpoint, custom_api_key, custom_model_name, custom_num_dim
```

---

## Key Design Decisions

### Managed vs External Sources

ISP sources are either **managed** or **external**:

| Property               | Managed (`is_external=False`)           | External (`is_external=True`)           |
| ---------------------- | --------------------------------------- | --------------------------------------- |
| Typesense collection   | `isp_{id}` — created by ISP            | Pre-existing (e.g. `catalog`)           |
| `collection_name`      | `isp_{id}`                             | `""` (empty)                            |
| Ingest writes to       | Own `isp_{id}` collection               | External collection via write-back path |
| Field shape            | `DocumentItem` (title/content/url/…)    | Whatever the external collection has    |
| Auto-discovered        | No                                      | Yes, via `discover_external_collections` |

The **catalog write-back path** for external sources:
```
REST API → raw items → _isp_type injection → JS extensions → embed → upsert to catalog collection
```

### Embedding Cache (Two-Tier)

```
embed_texts(texts) / embed_query(text)
       │
       ├── L1: in-process LRUCache (2048 slots) ← sub-microsecond hit
       │
       └── L2: Redis (24h TTL, binary float packing) ← shared across pods
                     │
                     └── Provider API (Voyage / OpenAI / RexBERT / custom)
```

Cache keys are `sha256("{model}:{input_type}:{text}")` — model name included so cached vectors from one model are never returned after a model switch.

### Runtime-Swappable Embedding Model

The active model is stored in `app_settings` (PostgreSQL) and in module-level globals in `embedding_service`. Changing the model via `PUT /v1/settings` calls `settings_service.set_embed_config()` which:
1. Persists the new config to `app_settings`.
2. Calls `embedding_service.set_current_model()` and related setters immediately.
3. Calls `typesense_client.set_vec_dim()` to update the dimension used for future collection creation.

No restart required. **Switching to a model with a different vector dimension breaks existing collections** — those indexes must be fully rebuilt.

### Hybrid Search (RRF)

```
query text
    │
    ├── embed_query(text) → query vector
    │
    ├── Typesense lexical search  (weight 0.7)
    └── Typesense semantic search (weight 0.3)
            │
            └── Reciprocal Rank Fusion → merged ranked list
```

RRF constant K=60. Both passes fetch `per_page × 3` candidates before merging to avoid page-boundary bias. For **external** collections the same RRF logic applies but scoped to `filter_by` (e.g. `type:=device`).

### JS Extensions (V8 Sandbox)

```
apply_extensions(docs, extensions)   ← called from ingest background task
       │
       └── run_in_executor(thread_pool) ← avoids blocking asyncio event loop
                   │
                   └── for each extension:
                         MiniRacer().eval(code)   ← compile once per batch
                         ctx.call("transform", doc) ← call for each doc
```

Each extension must define `function transform(doc) { ...; return doc; }`. Compile errors skip the extension for the whole batch. Runtime errors on individual documents keep the original document and continue. Extensions can be chained via `run_order` (lower runs first).

### Startup Sequence (Lifespan)

```python
@asynccontextmanager
async def lifespan(app):
    await job_lock.release_stale_for_instance()  # 0. Clear Redis locks left by a prior crash of this container
    await init_db()                              # 1. Create tables (idempotent IF NOT EXISTS)
    await load_embed_model()                     # 2. Restore operator's model choice from app_settings
    _ensure_catalog_collection()                 # 3. Create 'catalog' Typesense collection if absent
    ts.migrate_isp_fields()                      # 4. Add new Typesense fields to existing collections
    await _seed_extensions_from_files()          # 5. Upsert *.js files → extensions table → auto-assign to external indexes
    yield
```

Order matters: `init_db` must complete before `_seed_extensions_from_files` because seeding writes to PostgreSQL tables. `release_stale_for_instance` runs first so no requests are served with orphaned locks still present.

### Distributed Job Lock

Prevents two ISP instances from rebuilding the same index simultaneously. Without this, concurrent rebuilds race to drop and recreate the Typesense collection, producing partial or corrupt data.

```
Instance A receives POST /rebuild for index X
          │
          ▼  (background task starts)
    job_lock.acquire(idx_id)
          │
          ├── TRUE  → start heartbeat task → run rebuild → finally: cancel heartbeat + release lock
          │
          └── FALSE → set ingest_status = error("already running on another instance") → return

Instance B receives POST /rebuild for index X at the same time
          │
          ▼  (background task starts, after A holds the lock)
    job_lock.acquire(idx_id)  ← Redis SET NX returns nil → returns None
          │
          └── FALSE → surface error so UI poller stops waiting
```

**Redis key structure:**

| Field  | Value                        |
| ------ | ---------------------------- |
| Key    | `isp:job:{idx_id}`           |
| Value  | `{hostname}:{uuid4()}`       |
| TTL    | 10 minutes (initial)         |

Hostname is `socket.gethostname()` — stable within one Docker container across process restarts, unique across containers. Embedding the hostname in the value allows startup cleanup to identify stale locks from a prior crash of *this* container without SCAN-ing for PID matches.

**Heartbeat:** a background `asyncio.Task` extends the TTL by 10 minutes every 5 minutes while the job runs. Long rebuilds (large catalogs) are kept alive indefinitely; a crashed instance unblocks in at most 10 minutes without a clean restart.

**Release:** a Lua compare-and-delete script ensures only the lock holder can release it. Prevents instance A from releasing a key that instance B re-acquired after A's TTL expired.

**Degraded mode:** if Redis is unreachable, `acquire` logs a warning and returns a token anyway so the rebuild proceeds — single-instance deployments and Redis outages do not block ingestion.

**Endpoint fast-path:** before enqueuing the background task, the HTTP handler checks `ingest_status.state` and returns **HTTP 409** immediately if a job is already `running` or `queued`. This avoids the overhead of acquiring a Redis lock for the common same-instance duplicate-click case.

---

## API Routes

| Method | Path                                        | Description                                      |
| ------ | ------------------------------------------- | ------------------------------------------------ |
| GET    | `/v1/indexes`                               | List all sources                                 |
| POST   | `/v1/indexes`                               | Create a new managed source                      |
| GET    | `/v1/indexes/{id}`                          | Get source metadata                              |
| DELETE | `/v1/indexes/{id}`                          | Delete source + Typesense collection             |
| POST   | `/v1/indexes/{id}/connectors`               | Add a connector config                           |
| POST   | `/v1/indexes/{id}/ingest/rest`              | Trigger REST ingest (background); 409 if already running |
| POST   | `/v1/indexes/{id}/ingest/sitemap`           | Trigger sitemap crawl (background); 409 if already running |
| POST   | `/v1/indexes/{id}/rebuild`                  | Full rebuild (background); 409 if already running        |
| GET    | `/v1/indexes/{id}/status`                   | Poll ingest/rebuild job state                            |
| GET    | `/v1/indexes/{id}/documents`                | Paginated document listing — includes `vec`              |
| GET    | `/v1/indexes/{id}/documents/public`         | Paginated document listing — `vec` excluded              |
| PATCH  | `/v1/indexes/{id}/documents/{doc_id}`       | Partial document update (popularity, is_featured, boost_queries) |
| POST   | `/v1/indexes/{id}/search`                   | Hybrid/lexical/semantic search — includes `vec`          |
| POST   | `/v1/indexes/{id}/search/public`            | Same search — `vec` excluded (for external consumers)    |
| POST   | `/v1/indexes/discover`                      | Scan Typesense for untracked external collections |
| GET    | `/v1/extensions`                            | List global extension library                    |
| POST   | `/v1/extensions`                            | Create extension                                 |
| PUT    | `/v1/extensions/{id}`                       | Update extension                                 |
| DELETE | `/v1/extensions/{id}`                       | Delete extension                                 |
| GET    | `/v1/indexes/{id}/extensions`               | List extensions assigned to an index             |
| PUT    | `/v1/indexes/{id}/extensions/{ext_id}`      | Assign/update extension on an index              |
| DELETE | `/v1/indexes/{id}/extensions/{ext_id}`      | Unassign extension from an index                 |
| GET    | `/v1/settings`                              | Get current embedding model + available models   |
| PUT    | `/v1/settings`                              | Switch embedding model (applied immediately)     |
| GET    | `/v1/content`                               | List all sources as public slug-based APIs       |
| GET    | `/v1/content/{slug}`                        | List documents for a source by slug              |
| GET    | `/v1/content/{slug}/search`                 | Search a source by slug                          |
| GET    | `/v1/catalog/search`                        | PLP product search across all ISP collections    |
| GET    | `/v1/catalog/facets`                        | Facet counts (type, tags, segment)               |
| GET    | `/v1/catalog/typeahead`                     | Live typeahead suggestions                       |
| GET    | `/health`                                   | Liveness probe                                   |

---

## Anti-Patterns

| Pattern                                                          | Why It Is Wrong                                                                 | Correct Alternative                                              |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Router calls `database.py` directly (`await get_pool()`)        | Router now knows about the persistence layer; bypasses domain service           | Call the domain service function instead                         |
| Router calls `typesense_client` directly                        | Same as above — infrastructure bypassed                                         | Call the domain service; let it call Typesense                  |
| Domain service imports from a router                            | Creates a circular dependency; services must be framework-agnostic              | Move shared logic to a service or to `models/`                  |
| Adding a new Pydantic model inside a router file                | If the model is also used by a service it will cause an import cycle            | Define it in `models/<domain>.py`, re-export from `models/__init__.py` |
| Importing directly from `isp.services.index_service` (flat path) | Flat path no longer exists; module is now under `services/domain/`            | Use `from isp.services.domain import index_service`             |
| Importing directly from `isp.services.database` (flat path)    | Flat path no longer exists; module is now under `services/infra/`              | Use `from isp.services.infra.database import get_pool`          |
| Calling `embedding_service` from a router                       | Embedding is infrastructure; router should not orchestrate multi-service calls  | Move the embed + search orchestration into a domain service      |
| Mutating `settings` (the `config.py` singleton) at runtime     | `Settings` is loaded once; changes are invisible to other parts of the process  | Store runtime values in `app_settings` table; inject into module globals via `settings_service` |
| Starting a background task with `asyncio.create_task`          | Task lifecycle is unmanaged; no status tracking; errors are silently lost        | Use FastAPI `BackgroundTasks`; update `ingest_status` row for progress |
| Acquiring the job lock in the HTTP handler before `bg.add_task` | If the worker dies between acquire and task-start the lock is orphaned with no heartbeat to release it | Acquire the lock as the **first line of the background task** itself |
| Calling `job_lock.release()` without cancelling the heartbeat task first | Heartbeat may re-extend a key after release, causing a new acquire to fail | Always `hb.cancel()` before `job_lock.release()` in the `finally` block |
| Adding fields to `RestConnectorConfig` without defaults         | Breaks existing rows stored as JSON in the `connectors` JSONB column            | Always add new fields as `Optional` or with a default value      |

---

## Adding a New Domain

1. **Models** — create `models/<domain>.py` with Pydantic request/response models. Add re-exports to `models/__init__.py` so callers use `from isp.models import MyModel`.
2. **Domain service** — create `services/domain/<domain>_service.py` with `async def` CRUD functions. Import the pool via `from isp.services.infra.database import get_pool`. Add the table(s) to `init_db()` in `services/infra/database.py`.
3. **Router** — create `routers/<domain>.py`, define endpoints, import the domain service with `from isp.services.domain import <domain>_service`. Register with `app.include_router(<domain>.router, prefix="/v1")` in `main.py`.
4. **Infrastructure** (if needed) — add a new `services/infra/<name>.py` wrapping the external system. Keep it pure-function; no FastAPI imports. Import it in domain services via `from isp.services.infra import <name>`.
