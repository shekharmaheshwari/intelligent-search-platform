# Typesense Integration

## Role in ISP

Typesense is the **search engine and vector store**. It holds all indexed document content and their embeddings. PostgreSQL holds metadata about *how* to fetch data; Typesense holds the *actual data* that gets searched.

---

## Collection Types

### 1. ISP-Managed Collections (`isp_{id}`)
Created by ISP when a user adds a new source via the Create Wizard.

**Schema:**
```json
{
  "name": "isp_abc123def456",
  "fields": [
    { "name": "title",       "type": "string" },
    { "name": "content",     "type": "string" },
    { "name": "url",         "type": "string",  "optional": true },
    { "name": "source_type", "type": "string",  "facet": true },
    { "name": "indexed_at",  "type": "int64" },
    { "name": "vec",         "type": "float[]", "num_dim": 1024, "optional": true }
  ]
}
```

### 2. External Collections (e.g. `catalog`)
Pre-existing Typesense collections created outside of ISP. ISP registers them as external sources and writes back to them during rebuild.

**Catalog schema (created/maintained by ISP on startup):**
```json
{
  "name": "catalog",
  "fields": [
    { "name": "display",     "type": "string" },
    { "name": "type",        "type": "string",   "facet": true },
    { "name": "aliases",     "type": "string[]", "optional": true },
    { "name": "tags",        "type": "string[]", "facet": true, "optional": true },
    { "name": "segment",     "type": "string[]", "facet": true, "optional": true },
    { "name": "popularity",  "type": "int32" },
    { "name": "is_featured", "type": "bool",     "optional": true },
    { "name": "created_at",  "type": "int64",    "optional": true },
    { "name": "updated_at",  "type": "int64",    "optional": true },
    { "name": "vec",         "type": "float[]",  "num_dim": 1024, "optional": true }
  ],
  "default_sorting_field": "popularity"
}
```

Note: `metadata` is stored as a plain `string` field (serialised JSON) because Typesense does not support nested objects as first-class fields. Faceting on manufacturer/category is done client-side by parsing `metadata`.

---

## Search Modes

### Lexical Search
Standard BM25 full-text search. Fast, no embedding needed.
```python
client.collections[name].documents.search({
    "q": q,
    "query_by": "title,content",
    "query_by_weights": "2,1",   # title weighted 2x content
    "num_typos": 2,
    "sort_by": "_text_match:desc",
})
```

### Semantic Search
Vector similarity search using pre-computed embeddings.
```python
client.collections[name].documents.search({
    "q": "*",
    "query_by": "title",
    "vector_query": f"vec:([{vec_str}], k:{per_page})",
})
```

### Hybrid Search (default)
Runs lexical + semantic in parallel via `multi_search`, then merges using **Reciprocal Rank Fusion (RRF)**:

```
score = 0.7 / (60 + lexical_rank + 1)
      + 0.3 / (60 + semantic_rank + 1)
```

Lexical gets 0.7 weight, semantic 0.3 — biased toward exact keyword precision while still surfacing conceptually similar results.

```
┌─────────────────────────────────────┐
│           multi_search              │
│  ┌──────────────┐ ┌──────────────┐  │
│  │ Lexical BM25 │ │ Semantic Vec │  │
│  │  weight: 0.7 │ │  weight: 0.3 │  │
│  └──────┬───────┘ └──────┬───────┘  │
│         └────────┬────────┘         │
│              RRF Merge              │
│           Ranked Results            │
└─────────────────────────────────────┘
```

---

## Write Operations

### Upsert to ISP Collection
```python
client.collections["isp_{id}"].documents.import_(docs, {"action": "upsert"})
```

### Catalog Write-Back via Extension Pipeline
External sources (Phones, Accessories) go through a JS extension pipeline before being written to the shared `catalog` collection. This makes the data available to the T-Mobile PLP.

```
fetch raw items from REST API
      ↓
inject _isp_type hint (device/accessory)
      ↓
apply JS extensions in run_order (V8 isolates)
  — extensions are responsible for ALL field mapping
  — extension computes the final URL
  — extension output: flat dict with standard keys
      ↓
_generic_catalog_mapper: package into Typesense catalog schema
  — sets metadata as JSON string
  — generates stable id via md5(raw_id)[:12]
      ↓
embed display+description via Voyage AI
      ↓
upsert to catalog collection
```

### Delete Filtered Documents
Used when deleting an external source:
```python
client.collections["catalog"].documents.delete({"filter_by": "type:=device"})
```

### Drop Collection
Used when deleting an ISP-managed source:
```python
client.collections["isp_{id}"].delete()
```

---

## Document Listing

All listing calls include `"include_fields": "id, *, vec, metadata"` to explicitly return both the vector embedding and the metadata string (Typesense excludes `float[]` fields by default):

```python
params = {
    "q": "*",
    "query_by": "display",
    "sort_by": "popularity:desc",
    "include_fields": "id, *, vec, metadata",
    "page": page,
    "per_page": per_page,
}
```

---

## External Source Mapping

When listing or searching external (catalog) documents, raw Typesense docs are mapped to the ISP `DocumentItem` shape via `_map_external_doc()`. All extra fields (vec, tags, aliases, metadata, popularity, etc.) are passed through so the UI can display them in the expand view.

URL extraction priority:
1. `metadata.url` (set by the JS extension — this is the canonical source)
2. Top-level `url` or `link` field
3. Empty string if neither is present

```
Raw Typesense doc
  { id, display, type, aliases, tags, metadata(JSON), popularity, vec, ... }
       ↓ _map_external_doc()
ISP DocumentItem
  { id, title, content, url, source_type, indexed_at, + all passthrough fields }
```

---

## Connection Config

```python
typesense.Client({
    "nodes": [{"host": "typesense", "port": 8108, "protocol": "http"}],
    "api_key": "local-dev-key-change-me",
    "connection_timeout_seconds": 10,
})
```

Host is `typesense` (Docker container name on `search_tmo-net`). Configure via `TYPESENSE_HOST`, `TYPESENSE_PORT`, `TYPESENSE_API_KEY` env vars.
