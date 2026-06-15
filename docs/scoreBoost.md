# ISP Score Boost Reference

All ranking boosts applied after Reciprocal Rank Fusion (RRF). Each boost is additive on top of the RRF score.

---

## How the base score works (RRF)

Both rankers (`catalog.py → _rrf_search` and `typesense_client.py → search_with_vec`) fetch `per_page × 3` candidates from Typesense via two passes, then merge with RRF:

```
score(doc) = 0.7 / (60 + lexical_rank + 1)
           + 0.3 / (60 + semantic_rank + 1)
```

| Lexical rank | Contribution (weight 0.7) |
|---|---|
| #1 | 0.0114 |
| #5 | 0.0106 |
| #10 | 0.0099 |

Max possible RRF score (rank 1 in both passes): **≈ 0.0164**

---

## Boost signals (applied in order after RRF)

### 1. `is_featured` — +0.5

**What:** Flat bonus for any document with `is_featured = true`.  
**Magnitude:** +0.5 — ~30× a top-ranked RRF hit. Always overrides pure relevance order.  
**Set by:** Edit modal (Featured toggle) → PATCH `/indexes/{id}/documents/{doc_id}`.  
**Stored in:** Top-level Typesense field `is_featured: bool`.  
**Code:**
```python
# catalog.py and typesense_client.py
if doc.get("is_featured"):
    scores[did] += 0.5
```

---

### 2. `popularity` — up to +0.5

**What:** Linear scale based on the document's `popularity` integer score.  
**Magnitude:** `min(popularity, 1_000_000) / 2_000_000` — popularity 1 000 000 → +0.5, popularity 890 → +0.000445.  
**Set by:** Edit modal (Score field) → PATCH `/indexes/{id}/documents/{doc_id}`.  
**Also set by:** `tmobile_catalog.js` extension at index time: `popularity = max(0, 1000 - deviceRank × 10)`.  
**Stored in:** Top-level Typesense field `popularity: int32`.  
**Code:**
```python
pop = doc.get("popularity") or 0
if pop > 0:
    scores[did] += min(int(pop), 1_000_000) / 2_000_000
```

---

### 3. `boost_queries_equal` — +0.5

**What:** Merchandising boost when the user's full search query exactly equals one of the document's registered query terms (case-insensitive).  
**Magnitude:** +0.5 — same as `is_featured`. Effectively pins the document to the top for exact-match queries.  
**Set by (index time):** `merchandising_rules.js` extension — BOOST_EQUAL rule table maps query terms to SKU codes.  
**Set by (editorial):** Edit modal "Boost if query equals" tag input → PATCH `/indexes/{id}/documents/{doc_id}`.  
**Stored in:** Top-level Typesense field `boost_queries_equal: string[]`.  
**Code:**
```python
q_lower = q.lower().strip()
boost_eq = doc.get("boost_queries_equal") or []
if isinstance(boost_eq, list) and q_lower in [t.lower() for t in boost_eq if t]:
    scores[did] += 0.5
```

---

### 4. `boost_queries_contains` — +0.3

**What:** Softer merchandising boost when the user's query contains one of the registered terms as a substring (case-insensitive).  
**Magnitude:** +0.3 — ~18× a top-ranked RRF hit. Strong enough to surface the item near the top but can still be overridden by a more relevant exact-match result.  
**Set by (index time):** `merchandising_rules.js` extension — BOOST_CONTAINS rule table maps query terms to SKU codes.  
**Set by (editorial):** Edit modal "Boost if query contains" tag input → PATCH `/indexes/{id}/documents/{doc_id}`.  
**Stored in:** Top-level Typesense field `boost_queries_contains: string[]`.  
**Code:**
```python
boost_ct = doc.get("boost_queries_contains") or []
if isinstance(boost_ct, list) and any(t.lower() in q_lower for t in boost_ct if t):
    scores[did] += 0.3
```

---

## Combined score formula

```
final_score(doc, query) =
    RRF_score(doc)
  + 0.5  × is_featured
  + min(popularity, 1_000_000) / 2_000_000
  + 0.5  × (query == any boost_queries_equal term)
  + 0.3  × (query contains any boost_queries_contains term)
```

### Example: iPhone 16 boosted for "iphone" query

| Signal | Value | Contribution |
|---|---|---|
| RRF (lexical rank 1) | — | +0.0114 |
| RRF (semantic rank 2) | — | +0.0046 |
| `is_featured` | false | +0.0 |
| `popularity` | 660 | +0.00033 |
| `boost_queries_equal` | ["iphone 16"] | +0.0 (query is "iphone", not exact) |
| `boost_queries_contains` | ["iphone"] | **+0.3** |
| **Total** | | **≈ 0.316** |

---

## Where to configure each signal

| Signal | Index-time (extension) | Editorial (UI / API) |
|---|---|---|
| `is_featured` | — | Edit modal → Featured toggle |
| `popularity` | `tmobile_catalog.js` (deviceRank) | Edit modal → Score field |
| `boost_queries_equal` | `merchandising_rules.js` BOOST_EQUAL | Edit modal → "Boost if query equals" |
| `boost_queries_contains` | `merchandising_rules.js` BOOST_CONTAINS | Edit modal → "Boost if query contains" |

---

## Extension: merchandising_rules.js

File: `backend/extensions/merchandising_rules.js`  
Run order: 2 (after catalog mapper at 0, version_score at 1)

**Rule table format:**
```js
var BOOST_EQUAL = [
  { query: "iphone 16 pro", skus: ["194253462309"] },
];

var BOOST_CONTAINS = [
  { query: "iphone", skus: ["194253462309", "194253462316"] },
  { query: "samsung", skus: ["SM-S931UZKAXAA"] },
];
```

- `query` — matched case-insensitively against the search string at query time
- `skus` — `skuCode` values from the T-Mobile API (`skus[].skuCode` in the raw JSON), stored as `doc.skus` by `tmobile_catalog.js`

**To activate:** assign the extension to the target index in the Extensions panel, populate the rule tables, then rebuild.

---

## Ranker file locations

| File | Function | Path |
|---|---|---|
| Multi-collection catalog search | `_rrf_search` | `backend/src/isp/routers/catalog.py:75` |
| Single-index ISP search | `search_with_vec` | `backend/src/isp/services/infra/typesense_client.py:196` |
| Document PATCH endpoint | `update_document` | `backend/src/isp/routers/indexes.py:807` |
| Edit modal (UI) | `SourceContent.svelte` | `frontend/src/components/sources/SourceContent.svelte:302` |
