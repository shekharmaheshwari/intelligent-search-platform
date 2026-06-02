# Database — PostgreSQL

## Overview

PostgreSQL is the **control plane** of ISP. It stores all metadata about sources, connector configurations, ingest status, and deletion history. It does NOT store document content (that lives in Typesense).

**Previously:** Redis was used as a key-value store for the same data.  
**Migrated to PostgreSQL** for persistence, SQL querying, disk-based storage (no RAM cost), and unified service management.

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
    connectors           JSONB        DEFAULT '[]',      -- REST/sitemap config
    collection_name      VARCHAR(200) DEFAULT '',        -- isp_{id} or ''
    is_external          BOOLEAN      DEFAULT FALSE,     -- pre-existing TS collection
    external_collection  VARCHAR(200) DEFAULT '',        -- e.g. "catalog"
    external_filter      VARCHAR(500) DEFAULT '',        -- e.g. "type:=device"
    external_query_by    VARCHAR(100) DEFAULT 'display',
    external_title_field VARCHAR(100) DEFAULT 'display',
    external_url_field   TEXT         DEFAULT ''         -- product URL prefix
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
  "url_field": "url"
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
Tracks the current state of an ingest or rebuild job. One row per index.

```sql
CREATE TABLE ingest_status (
    index_id VARCHAR(12) PRIMARY KEY,
    state    VARCHAR(20) DEFAULT 'idle',   -- idle|queued|running|done|error
    total    INTEGER     DEFAULT 0,        -- total docs fetched
    indexed  INTEGER     DEFAULT 0,        -- docs successfully indexed
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
  ─────────────►  │ DELETE FROM ingest_status        │
                  │ INSERT INTO deleted_splits       │
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

Tables are created on startup via `init_db()` called from FastAPI's lifespan handler — no separate migration tool needed for this schema.

---

## Useful Queries

```sql
-- List all sources with doc counts
SELECT id, name, doc_count, is_external, external_collection
FROM indexes ORDER BY created_at DESC;

-- Check ingest status for all sources
SELECT i.name, s.state, s.indexed, s.total, s.error
FROM ingest_status s JOIN indexes i ON i.id = s.index_id;

-- See what's been deleted
SELECT * FROM deleted_splits;

-- View connector config for a source
SELECT name, connectors FROM indexes WHERE name = 'Phones';
```
