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
    { "name": "url",         "type": "string",   "optional": true },
    { "name": "source_type", "type": "string",   "facet": true },
    { "name": "indexed_at",  "type": "int64" },
    { "name": "metadata",    "type": "string",   "optional": true, "index": false },
    { "name": "popularity",             "type": "int32",    "optional": true, "sort": true },
    { "name": "is_featured",            "type": "bool",     "optional": true },
    { "name": "boost_queries_equal",    "type": "string[]", "optional": true },
    { "name": "boost_queries_contains", "type": "string[]", "optional": true },
    { "name": "vec",                    "type": "float[]",  "num_dim": 1024, "optional": true }
  ]
}
```

`metadata` is a serialised JSON string carrying rich product fields (images, price, manufacturer, etc.) that don't need to be indexed/searched directly.  `popularity`, `is_featured`, `boost_queries_equal`, and `boost_queries_contains` are promoted to **top-level fields** so the ranker can apply score boosts without parsing the metadata blob.

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
    { "name": "popularity",             "type": "int32" },
    { "name": "is_featured",            "type": "bool",     "optional": true },
    { "name": "boost_queries_equal",    "type": "string[]", "optional": true },
    { "name": "boost_queries_contains", "type": "string[]", "optional": true },
    { "name": "created_at",             "type": "int64",    "optional": true },
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
rrf_score = 0.7 / (60 + lexical_rank + 1)
          + 0.3 / (60 + semantic_rank + 1)
```

Lexical gets 0.7 weight, semantic 0.3 — biased toward exact keyword precision while still surfacing conceptually similar results.

### Editorial Boosts (applied after RRF)
After the RRF merge, the ranker applies score bonuses before the final sort:

```
if doc.is_featured:
    score += 0.5                                   # always surfaces above pure relevance

if doc.popularity > 0:
    score += min(popularity, 1_000_000) / 2_000_000
    # popularity=100 000 → +0.05
    # popularity=1 000 000 → +0.50  (max)

if query (exact) in doc.boost_queries_equal:
    score += 0.5                                   # merchandising pin for exact-match queries

if any term in doc.boost_queries_contains is substring of query:
    score += 0.3                                   # softer merchandising lift for contains-match
```

All four signals read from top-level Typesense fields (never the `metadata` blob), so no JSON parsing is required at query time. See `docs/scoreBoost.md` for full magnitude tables and configuration guidance.

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

### ISP-Managed Upsert via `_ensure_isp_schema`
All content indexed into ISP-owned `isp_*` collections is normalised first:

```
fetch raw items (REST or after extension pipeline)
      ↓
_ensure_isp_schema(docs)
  — maps title / content / url / source_type
  — packs every non-schema field into metadata JSON
  — promotes popularity + is_featured to top-level fields
  — hashes non-string IDs to stable hex
      ↓
embed via Voyage AI
      ↓
upsert to isp_{id} collection
```

### Catalog Write-Back via Extension Pipeline
External sources can also write back to a shared `catalog` collection for legacy consumers.

```
fetch raw items from REST API
      ↓
inject _isp_type hint (device/accessory)
      ↓
apply JS extensions in run_order (V8 isolates)
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

Internal listing calls include `"include_fields": "id, *, vec, metadata"` to explicitly return both the vector embedding and the metadata string (Typesense excludes `float[]` fields by default):

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

**Public listing** (`/documents/public`) passes `exclude_fields: "vec"` instead, stripping the 1 024-float array (~4 KB per document) before the response leaves ISP:

```python
params["exclude_fields"] = "vec"   # omit include_fields override; use this instead
```

Ranking is identical — the vector is used for candidate retrieval inside ISP and discarded before serialisation.

---

## Partial Document Update

Used by the admin UI "Edit" modal to update editorial fields without re-indexing:

```python
client.collections[col].documents.import_(
    [{
        "id": doc_id,
        "popularity": 100000,
        "is_featured": True,
        "boost_queries_equal":    ["iphone 16 pro"],
        "boost_queries_contains": ["iphone", "apple"],
    }],
    {"action": "update"},
)
```

Only the supplied keys are overwritten.  The vector and all other content are untouched. `boost_queries_equal` and `boost_queries_contains` are stored as top-level `string[]` fields (not inside `metadata`) so the ranker can read them without JSON parsing.

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

---

## References

> Citations below are sourced from training knowledge (not live browsing). DOI and arXiv links are provided where known.

1. **Robertson, S., & Zaragoza, H.** (2009). "The Probabilistic Relevance Framework: BM25 and Beyond." *Foundations and Trends in Information Retrieval*, 3(4), 333–389. DOI: [10.1561/1500000019](https://doi.org/10.1561/1500000019) — Full derivation of BM25 including the IDF weighting and term-frequency saturation used by Typesense's lexical search; `query_by_weights: "2,1"` reflects BM25's field-weighting capability.

2. **Cormack, G. V., Clarke, C. L. A., & Buettcher, S.** (2009). "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods." *Proceedings of SIGIR 2009*, pp. 758–759. DOI: [10.1145/1571941.1572114](https://doi.org/10.1145/1571941.1572114) — Introduces RRF with K=60; ISP implements `0.7/(60+lex_rank+1) + 0.3/(60+sem_rank+1)` directly from this paper.

3. **Karpukhin, V., Oğuz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., & Yih, W.-T.** (2020). "Dense Passage Retrieval for Open-Domain Question Answering." *Proceedings of EMNLP 2020*, pp. 6769–6781. arXiv: [2004.04906](https://arxiv.org/abs/2004.04906) — Establishes dense vector retrieval as complementary to sparse BM25; motivates the hybrid 0.7/0.3 weighting in favour of lexical precision while retaining semantic coverage.

4. **Johnson, J., Douze, M., & Jégou, H.** (2021). "Billion-Scale Similarity Search with GPUs." *IEEE Transactions on Big Data*, 7(3), 535–547. arXiv: [1702.08734](https://arxiv.org/abs/1702.08734) — FAISS: the ANN library underlying Typesense's `vector_query` capability; `k` in `vec:([…], k:N)` controls the number of ANN candidates returned.

5. **Liu, T.-Y.** (2009). "Learning to Rank for Information Retrieval." *Foundations and Trends in Information Retrieval*, 3(3), 225–331. DOI: [10.1561/1500000016](https://doi.org/10.1561/1500000016) — Surveys rank fusion and feature-based ranking; the editorial boosts (`is_featured`, `popularity`, `boost_queries_*`) are a hand-crafted instance of feature-based ranking applied post-RRF.

6. **Salton, G., & McGill, M. J.** (1983). *Introduction to Modern Information Retrieval*. McGraw-Hill. ISBN: 0-07-054484-0 — Foundational text defining the vector space model; `exclude_fields: "vec"` on public endpoints reflects the distinction between internal ANN-retrieval use and external response payloads.
