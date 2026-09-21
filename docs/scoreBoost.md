# ISP Score Boost Reference

All ranking boosts are applied after Reciprocal Rank Fusion (RRF) by a single
shared function — `typesense_client.apply_editorial_boosts(scores, all_docs, q, *, is_browse)`
— used by every search path (lexical, hybrid `search_with_vec`, and
cross-catalog `catalog._rrf_search`) so ranking behaviour is identical everywhere.

**`is_featured` and the `boost_queries_*` signals are additive** — deliberate
merchandising levers that may outrank pure relevance.

**`popularity` is query-aware** (changed 2026-09 — see
`docs/trainedRescorerDesign.md`):

| Query intent | How popularity is applied | Rationale |
|---|---|---|
| **Empty-query browse** (`q` blank or `*`) | Additive, `popularity / 2_000_000` → up to **+0.5**. The dominant ordering signal. | No query relevance to respect — popularity *is* the ranking. |
| **Non-empty query** | Bounded multiplicative: `relevance *= (1 + 0.25 · pop_norm)` where `pop_norm = popularity / max_popularity_in_result_set`. Max lift **×1.25**. | A learned CTR signal must not overturn a clear text match. An additive term on the RRF scale (max ≈ 0.016) can't guarantee that; a capped multiplier can. |

Pinned hits are handled separately by the caller and always win.

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

The pure-lexical path (`search_documents`, used for `mode=lexical` and for
hybrid/semantic browse when no query vector is available) has no RRF merge; it
retrieves by `sort_by=_text_match:desc` and `apply_editorial_boosts` re-ranks
in Python over rank-based pseudo-scores `1 / (60 + rank + 1)`. On **browse** it
instead asks Typesense for `sort_by=is_featured:desc,popularity:desc,_text_match:desc`
so pagination stays correct across the whole collection, and skips the Python
popularity pass (already baked into the sort).

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

### 2. `popularity` — query-aware (browse: up to +0.5 · query: ≤ ×1.25)

**What:** The document's `popularity` integer — either hand-edited or written by
the trained browse re-scorer (`popularity_trainer.py`, Case A) / the formula
`analytics_service.aggregate_popularity`.
**Magnitude:**

- **Browse** (`is_browse`): `min(popularity, 1_000_000) / 2_000_000` added — popularity 1 000 000 → +0.5.
- **Query**: `relevance *= 1 + 0.25 · pop_norm`, where `pop_norm = popularity / max_popularity_in_result_set` — the most-popular candidate gets ×1.25, everything else proportionally less. Never additive, so it can reorder near-ties but not overturn a clear relevance win.
- **Exact-match protection (query only):** if the doc's normalized title *or* any alias **equals the query**, `pop_norm` is forced to `1.0` regardless of the doc's actual popularity. So a sibling variant's popularity can't out-boost it — `q="iphone 17"` keeps "iPhone 17" above "iPhone 17 Pro Max" even when the Pro Max is more popular. The exact match still only gets the same capped ×1.25, so base relevance decides between an exact and a partial match (a low-ranked exact match won't leapfrog a much-more-relevant partial one). This is the "exact match" slice of Tier 1a in `docs/queryAwareGroupPopularityPlan.md`, shipped early. Normalization is lower-case + whitespace-collapse only — `"Galaxy S25+"` ≠ `"Galaxy S25"`.

**Tuning:** the `0.25` coefficient is `_POP_QUERY_COEFF` in `typesense_client.py`.
`0.10` is the conservative floor (barely reorders vague queries); `0.20–0.30`
gives popular items real pull on broad queries while still protecting specific
ones. Above ~1.0 the relevance guarantee breaks. (Before exact-match protection,
`0.25` let "iphone 17" surface "iPhone 17 Pro Max" first — the protection is
what makes `0.25` safe for specific-model queries.)
**Set by:** Edit modal (Score field) → PATCH `/indexes/{id}/documents/{doc_id}`; `POST /v1/training/popularity`; `POST /v1/analytics/aggregate`; `catalog_mapper.js` at index time.
**Stored in:** Top-level Typesense field `popularity: int32`.
**Code:**
```python
# apply_editorial_boosts(), typesense_client.py
pop = min(int(doc.get("popularity") or 0), 1_000_000)
if is_browse:
    if pop > 0:
        base += pop / 2_000_000
elif max_pop > 0:
    pop_norm = 1.0 if _is_exact_match(doc) else (pop / max_pop)
    if pop_norm > 0:
        base *= 1.0 + _POP_QUERY_COEFF * pop_norm          # _POP_QUERY_COEFF = 0.25
```

**Why it changed:** with the additive `+pop/2_000_000` rule a max-popularity
document received +0.5 on *every* query — ~30× the entire RRF score range — so a
popular-but-irrelevant item (e.g. a Galaxy fold with a trained popularity of
1 000 000) ranked #1 for the query "iphone 17". See
`docs/trainedRescorerDesign.md` and the regression tests in
`backend/tests/services/infra/test_editorial_boosts.py`.

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

**Non-empty query:**

```
base = RRF_score(doc)                      # or 1/(60+rank+1) on the pure-lexical path
if popularity > 0:
    base *= 1 + 0.25 × (popularity / max_popularity_in_result_set)
final = base
      + 0.5 × is_featured
      + 0.5 × (query == any boost_queries_equal term)
      + 0.3 × (query contains any boost_queries_contains term)
```

**Empty-query browse:**

```
final = base
      + min(popularity, 1_000_000) / 2_000_000     # up to +0.5, the dominant term
      + 0.5 × is_featured
# boost_queries_* never fire on an empty query
```

### Example: iPhone 16 for the query "iphone" (max popularity in result set = 1 000 000)

| Signal | Value | Effect |
|---|---|---|
| RRF (lexical rank 1) | — | base = 0.0114 |
| RRF (semantic rank 2) | — | base += 0.0046 → 0.0160 |
| `popularity` | 660 000 | base ×= 1 + 0.25·(660 000 / 1 000 000) = ×1.165 → 0.0186 |
| `is_featured` | false | +0.0 |
| `boost_queries_equal` | ["iphone 16"] | +0.0 (query is "iphone", not exact) |
| `boost_queries_contains` | ["iphone"] | **+0.3** |
| **Total** | | **≈ 0.319** |

### Example: same iPhone 16 on empty-query browse

| Signal | Value | Effect |
|---|---|---|
| base (browse pseudo-score) | — | ~0.016 |
| `popularity` | 660 000 | + 660 000 / 2 000 000 = **+0.33** |
| **Total** | | **≈ 0.346** — ordered essentially by popularity |

---

## Where to configure each signal

| Signal | Index-time (extension) | Editorial (UI / API) |
|---|---|---|
| `is_featured` | — | Edit modal → Featured toggle |
| `popularity` | `catalog_mapper.js` (deviceRank) | Edit modal → Score field |
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
- `skus` — `skuCode` values from the product API (`skus[].skuCode` in the raw JSON), stored as `doc.skus` by `catalog_mapper.js`

**To activate:** assign the extension to the target index in the Extensions panel, populate the rule tables, then rebuild.

---

## Ranker file locations

| File | Function | Path |
|---|---|---|
| **Shared editorial boost logic** | `apply_editorial_boosts` | `backend/src/isp/services/infra/typesense_client.py` |
| Multi-collection catalog search | `_rrf_search` | `backend/src/isp/routers/catalog.py` |
| Single-index hybrid search | `search_with_vec` | `backend/src/isp/services/infra/typesense_client.py` |
| Single-index lexical / browse search | `search_documents` | `backend/src/isp/services/infra/typesense_client.py` |
| Regression tests | — | `backend/tests/services/infra/test_editorial_boosts.py` |
| Document PATCH endpoint | `update_document` | `backend/src/isp/routers/indexes.py` |
| Edit modal (UI) | `SourceContent.svelte` | `frontend/src/components/sources/SourceContent.svelte` |

## Known follow-ups

- `boost_rules` (per-index rule table, e.g. "+0.04 for all Apple") is still
  **additive on the RRF scale** — the same scale mismatch `popularity` had. A
  small rule boost can pin a whole brand to the top of browse above a
  1 000 000-popularity item. Not yet migrated to the query-aware model.
- `catalog._rrf_search` browse only sees `per_page × 3` candidates per
  collection, so cross-catalog global browse ordering by popularity is
  approximate. Single-index browse (`search_documents`) is exact — it sorts in
  Typesense.

---

## References

> Citations below are sourced from training knowledge (not live browsing). DOI and arXiv links are provided where known.

1. **Cormack, G. V., Clarke, C. L. A., & Buettcher, S.** (2009). "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods." *Proceedings of the 32nd Annual ACM SIGIR Conference*, pp. 758–759. DOI: [10.1145/1571941.1572114](https://doi.org/10.1145/1571941.1572114) — Introduces Reciprocal Rank Fusion (RRF) with the constant K=60 used in this system's `0.7/(60+rank+1) + 0.3/(60+rank+1)` scoring formula.

2. **Robertson, S., & Zaragoza, H.** (2009). "The Probabilistic Relevance Framework: BM25 and Beyond." *Foundations and Trends in Information Retrieval*, 3(4), 333–389. DOI: [10.1561/1500000019](https://doi.org/10.1561/1500000019) — Comprehensive treatment of BM25 (Best Match 25), the term-frequency/IDF scoring function used for the lexical (0.7 weight) component of hybrid search.

3. **Burges, C., Shaked, T., Renshaw, E., Lazier, A., Deeds, M., Hamilton, N., & Svore, K.** (2005). "Learning to Rank Using Gradient Descent." *Proceedings of the 22nd International Conference on Machine Learning (ICML 2005)*, pp. 89–96. DOI: [10.1145/1102351.1102363](https://doi.org/10.1145/1102351.1102363) — RankNet: gradient-descent approach to learning ranking functions, forerunner of LambdaMART; provides theoretical grounding for the editorial boost signals as learnable ranking features.

4. **Robertson, S. E., & Sparck Jones, K.** (1976). "Relevance Weighting of Search Terms." *Journal of the American Society for Information Science*, 27(3), 129–146. DOI: [10.1002/asi.4630270302](https://doi.org/10.1002/asi.4630270302) — Original probabilistic relevance model underlying BM25; establishes inverse document frequency (IDF) and term frequency weighting principles.

5. **Liu, T.-Y.** (2009). "Learning to Rank for Information Retrieval." *Foundations and Trends in Information Retrieval*, 3(3), 225–331. DOI: [10.1561/1500000016](https://doi.org/10.1561/1500000016) — Surveys pointwise, pairwise, and listwise ranking; provides the theoretical framework for additive score functions such as the editorial boosts implemented here.

6. **Agrawal, R., & Srikant, R.** (1994). "Fast Algorithms for Mining Association Rules." *Proceedings of the 20th VLDB Conference*, pp. 487–499. — Association rule mining forms the theoretical basis for query-to-SKU merchandising rules (`boost_queries_equal` / `boost_queries_contains`), which are a manually curated special case.

7. **Voorhees, E. M.** (1999). "The TREC-8 Question Answering Track Report." *Proceedings of TREC 1999*. — TREC evaluation benchmarks established the role of exact-match query boosts; informs the `boost_queries_equal` (+0.5) and contains-match (+0.3) magnitude design.
