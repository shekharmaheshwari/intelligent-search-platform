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
    external_url_field   TEXT         DEFAULT ''         -- deprecated; URL now owned by extension
);
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
-- List all sources with doc counts
SELECT id, name, doc_count, is_external, external_collection
FROM indexes ORDER BY created_at DESC;

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
