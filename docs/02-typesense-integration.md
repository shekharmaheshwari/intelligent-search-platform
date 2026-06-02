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
Pre-existing Typesense collections created outside of ISP (e.g. by T-Mobile tmo-app).

**Catalog schema (T-Mobile):**
```json
{
  "name": "catalog",
  "fields": [
    { "name": "id",          "type": "string" },
    { "name": "display",     "type": "string" },
    { "name": "type",        "type": "string", "facet": true },
    { "name": "aliases",     "type": "string[]" },
    { "name": "tags",        "type": "string[]" },
    { "name": "segment",     "type": "string[]" },
    { "name": "popularity",  "type": "int32" },
    { "name": "is_featured", "type": "bool" },
    { "name": "created_at",  "type": "int64" },
    { "name": "updated_at",  "type": "int64" },
    { "name": "metadata",    "type": "string" },
    { "name": "vec",         "type": "float[]", "num_dim": 1024, "optional": true }
  ]
}
```

---

## Search Modes

### Lexical Search
Standard BM25 full-text search. Fast, no embedding needed.
```python
client.collections[name].documents.search({
    "q": q,
    "query_by": "title,content",
    "query_by_weights": "2,1",   # title weighted 2x
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

### Upsert to External Collection (write-back)
Used during Rebuild of external sources (Phones, Accessories):
```python
client.collections["catalog"].documents.import_(docs, {"action": "upsert"})
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

All listing calls include `"include_fields": "*, vec"` to explicitly return the vector embedding (Typesense excludes float[] fields by default):

```python
params = {
    "q": "*",
    "query_by": "display",
    "sort_by": "popularity:desc",
    "include_fields": "*, vec",   # required to get vec field
    "page": page,
    "per_page": per_page,
}
```

---

## External Source Mapping

When listing or searching external (catalog) documents, raw Typesense docs are mapped to the ISP `DocumentItem` shape via `_map_external_doc()`. All extra fields (vec, tags, metadata, popularity, etc.) are passed through so the UI can display them in the expand view.

```
Raw Typesense doc
  { id, display, type, aliases, tags, metadata, popularity, vec, ... }
       ↓ _map_external_doc()
ISP DocumentItem
  { id, title, content, url, source_type, indexed_at, + all raw fields }
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

Host is `typesense` (Docker container name on `search_tmo-net`).
