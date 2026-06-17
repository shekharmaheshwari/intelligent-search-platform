# Database — PostgreSQL

## Overview

PostgreSQL is the **control plane** of ISP. It stores all metadata about sources, connector configurations, extension code, ingest status, and deletion history. It does NOT store document content (that lives in Typesense).

---

## Connection

```
Host:     localhost (external) / isp-db (Docker internal)
Port:     5432
Database: isp
User:     isp
Password: isp-dev-password
DSN:      postgresql://isp:isp-dev-password@isp-db:5432/isp
```

Access via DBeaver: connect to `localhost:5432` with above credentials.

---

## Schema

### Table: `indexes`
Stores one row per ISP source. This is the primary metadata table.

```sql
CREATE TABLE indexes (
    id                   VARCHAR(12)  PRIMARY KEY,      -- uuid4.hex[:12]
    name                 VARCHAR(80)  NOT NULL,          -- display name
    description          TEXT         DEFAULT '',
    created_at           VARCHAR(30)  NOT NULL,          -- ISO8601 UTC
    doc_count            INTEGER      DEFAULT 0,         -- synced from Typesense
    last_ingested        VARCHAR(30),                    -- ISO8601 UTC
    status               VARCHAR(20)  DEFAULT 'ready',
    connectors           JSONB        DEFAULT '[]',      -- REST/sitemap config array
    collection_name      VARCHAR(200) DEFAULT '',        -- isp_{id} for managed, '' for external
    is_external          BOOLEAN      DEFAULT FALSE,     -- pre-existing Typesense collection
    external_collection  VARCHAR(200) DEFAULT '',        -- e.g. "catalog"
    external_filter      VARCHAR(500) DEFAULT '',        -- e.g. "type:=device"
    external_query_by    VARCHAR(100) DEFAULT 'display',
    external_title_field VARCHAR(100) DEFAULT 'display',
    external_url_field   TEXT         DEFAULT '',        -- deprecated; URL now owned by extension

    -- Added via ALTER TABLE on existing deployments:
    embed_model          VARCHAR(50)  DEFAULT '',        -- per-index embedding model override ('' = inherit global)
    version_boost        BOOLEAN      DEFAULT FALSE,     -- recency score bump for new-badge items
    intent_label         TEXT         DEFAULT '',        -- PLP tab this index maps to (e.g. 'device', 'tablet')
    slug                 VARCHAR(120) DEFAULT ''         -- human-readable URL alias (e.g. 'tablets')
);

-- Partial unique index: only non-empty slugs are constrained, allowing '' during the
-- init_db → seed_slugs startup window before back-fill completes.
CREATE UNIQUE INDEX indexes_slug_unique ON indexes (slug) WHERE slug <> '';
```

**`connectors` JSONB examples:**

REST connector (saved after first ingest/rebuild):
```json
[{
  "type": "rest",
  "url": "https://api.example.com/products",
  "method": "POST",
  "headers": {"Authorization": "Bearer TOKEN"},
  "body": "{\"category\": \"phones\"}",
  "result_path": "",
  "id_field": "id",
  "title_field": "title",
  "content_field": "description",
  "url_field": "url",
  "field_map": {"productName": "title", "msrp": "price"}
}]
```

External connector (auto-created by discover):
```json
[{
  "type": "external",
  "url": "https://www.t-mobile.com/cell-phone/",
  "collection": "catalog"
}]
```

---

### Table: `ingest_status`
Tracks the current state of an ingest or rebuild job. One row per index, upserted in place.

```sql
CREATE TABLE ingest_status (
    index_id VARCHAR(12) PRIMARY KEY,
    state    VARCHAR(20) DEFAULT 'idle',   -- idle|queued|running|done|error
    total    INTEGER     DEFAULT 0,        -- total docs fetched
    indexed  INTEGER     DEFAULT 0,        -- docs successfully upserted
    error    TEXT        DEFAULT ''        -- error message if state=error
);
```

**State transitions:**
```
idle → queued → running → done
                        → error
```

---

### Table: `deleted_splits`
Tracks external sources the user explicitly deleted. Prevents `discover` from re-adding them on page load.

```sql
CREATE TABLE deleted_splits (
    split_key TEXT PRIMARY KEY   -- format: "{collection}:{filter}"
);
```

**Examples:**
```
catalog:type:=device       ← Phones deleted
catalog:type:=accessory    ← Accessories deleted
catalog:                   ← Entire catalog deleted (no filter)
```

---

### Table: `extensions`
Stores the global JS extension library. Each row is a reusable `transform(doc)` function.

```sql
CREATE TABLE extensions (
    id          VARCHAR(12)  PRIMARY KEY,   -- uuid4.hex[:12]
    name        VARCHAR(120) NOT NULL,       -- human-readable, used to match file-based seeds
    description TEXT         DEFAULT '',
    code        TEXT         NOT NULL DEFAULT '',  -- full JS source
    created_at  VARCHAR(30)  NOT NULL,
    updated_at  VARCHAR(30)  NOT NULL
);
```

Extensions are seeded from `backend/extensions/*.js` on every startup. The `// @name` and `// @description` comment headers are parsed to populate `name` and `description`. If an extension with the same name already exists it is updated (code sync), so the file is always the source of truth.

---

### Table: `index_extensions`
Many-to-many join between indexes and extensions. Controls which transforms run on which source and in what order.

```sql
CREATE TABLE index_extensions (
    index_id     VARCHAR(12) NOT NULL REFERENCES indexes(id) ON DELETE CASCADE,
    extension_id VARCHAR(12) NOT NULL REFERENCES extensions(id) ON DELETE CASCADE,
    run_order    INTEGER     NOT NULL DEFAULT 0,   -- lower = runs first
    enabled      BOOLEAN     NOT NULL DEFAULT TRUE,
    PRIMARY KEY (index_id, extension_id)
);
```

- Deleting an index or extension cascades to remove the assignment.
- `assign_extension_if_new` uses `ON CONFLICT DO NOTHING` so startup seeding never overrides user-configured `run_order` or `enabled` state.
- `assign_extension` (full upsert) is used only when the user explicitly re-assigns via the UI.

---

## Slug and Intent Label

### Slug

`slug` is a URL-safe alias for an index. It is generated from the display `name` at creation time using these rules:

1. Lowercase the name
2. Spaces and underscores → hyphens
3. Strip characters outside `[a-z0-9-]`
4. Collapse runs of multiple hyphens to one
5. Strip leading/trailing hyphens

Examples: `"TABLETS"` → `"tablets"`, `"My Products v2"` → `"my-products-v2"`.

If the generated slug is already taken by another index, the first 4 hex chars of the index ID are appended as a disambiguator (e.g. `tablets-9ed5`).

**Startup back-fill:** on every startup `seed_slugs()` runs after `init_db()` and assigns slugs to any indexes that have an empty slug. This means existing deployments receive slugs automatically on the first restart after upgrading.

**API resolution:** all routes that accept `{id}` in the path call `_resolve_index(id_or_slug)`, which tries slug lookup first and falls back to hex ID. Both URL forms always work:

```
/v1/indexes/tablets/search/public    ← slug form
/v1/indexes/9ed56100f220/search/public  ← hex ID form (still valid)
```

You can pass `slug` when creating an index via `POST /v1/indexes`. If omitted, it is auto-generated.

### Intent Label

`intent_label` maps an index to a PLP tab identifier (e.g. `device`, `accessory`, `smartwatch`, `tablet`). The `/v1/intent/tab` endpoint reads this column at request time — there are no hardcoded collection IDs or keywords in the code. Adding a new tab is as simple as setting `intent_label` on an index and the intent detection self-configures. See [Tab Intent Detection](tabIntent.md) for the scoring algorithm.

---

## Data Flow

```
                  PostgreSQL
                  ┌─────────────────────────────────┐
  Create Source   │ INSERT INTO indexes              │
  ─────────────►  │ (name, description, created_at)  │
                  └─────────────────────────────────┘

                  ┌─────────────────────────────────┐
  Start Ingest    │ UPDATE indexes                  │
  ─────────────►  │ SET connectors = [cfg]          │
                  │ INSERT INTO ingest_status        │
                  │ SET state = 'queued'             │
                  └─────────────────────────────────┘

                  ┌─────────────────────────────────┐
  Ingest Done     │ UPDATE indexes                  │
  ─────────────►  │ SET doc_count = N               │
                  │ UPDATE ingest_status             │
                  │ SET state = 'done', indexed = N  │
                  └─────────────────────────────────┘

                  ┌─────────────────────────────────┐
  Delete Source   │ DELETE FROM indexes              │
  ─────────────►  │ CASCADE: index_extensions        │
                  │ DELETE FROM ingest_status        │
                  │ INSERT INTO deleted_splits       │
                  └─────────────────────────────────┘

                  ┌─────────────────────────────────┐
  Startup Seed    │ UPSERT INTO extensions          │
  ─────────────►  │ (from backend/extensions/*.js)  │
                  │ INSERT INTO index_extensions     │
                  │ ON CONFLICT DO NOTHING           │
                  └─────────────────────────────────┘
```

---

## asyncpg Connection Pool

The backend uses `asyncpg` with a connection pool (min 2, max 10 connections):

```python
_pool = await asyncpg.create_pool(
    settings.database_url,
    min_size=2,
    max_size=10
)
```

Tables are created on startup via `init_db()` called from FastAPI's lifespan handler — no separate migration tool is needed for this schema. All `CREATE TABLE` statements use `IF NOT EXISTS`.

---

## Useful Queries

```sql
-- List all sources with slugs, intent labels, and doc counts
SELECT id, name, slug, intent_label, doc_count, is_external
FROM indexes ORDER BY created_at DESC;

-- Look up an index by slug (same as API resolution)
SELECT * FROM indexes WHERE slug = 'tablets';

-- Find indexes that still have no slug (should be empty after first startup)
SELECT id, name FROM indexes WHERE slug = '' OR slug IS NULL;

-- Check ingest status for all sources
SELECT i.name, s.state, s.indexed, s.total, s.error
FROM ingest_status s JOIN indexes i ON i.id = s.index_id;

-- See what has been deleted
SELECT * FROM deleted_splits;

-- View connector config for a source
SELECT name, connectors FROM indexes WHERE name = 'Phones';

-- List all extensions
SELECT id, name, updated_at FROM extensions ORDER BY created_at DESC;

-- See which extensions are assigned to which indexes
SELECT i.name AS index_name, e.name AS extension_name, ie.run_order, ie.enabled
FROM index_extensions ie
JOIN indexes i ON i.id = ie.index_id
JOIN extensions e ON e.id = ie.extension_id
ORDER BY i.name, ie.run_order;
```
