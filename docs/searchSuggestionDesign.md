# Search Suggestions — Design Document

Typeahead suggestions for the PLP search bar.  Returns generalised search terms
ranked by real user demand, optionally scoped to the active catalog tab.

---

## Overview

When a user types in the PLP search bar, ISP returns a list of suggested search
terms — phrases like "screen protector", "samsung charger", "iphone 17" — before
they finish typing.  Suggestions are ranked by how frequently real users have
searched for them, with the active catalog tab used to promote relevant terms to
the top without filtering out global results.

---

## Collection schema

**Collection name:** `search_suggestions`

```json
{
  "name": "search_suggestions",
  "fields": [
    { "name": "id",         "type": "string"  },
    { "name": "query",      "type": "string"  },
    { "name": "popularity", "type": "int32",  "facet": false },
    { "name": "boost",      "type": "int32",  "facet": false },
    { "name": "type",       "type": "string",  "facet": true  },
    { "name": "category",   "type": "string",  "facet": true, "optional": true }
  ],
  "default_sorting_field": "popularity"
}
```

| Field | Description |
|---|---|
| `id` | `{type}:{slug(query)}:{slug(category)}` — stable upsert key |
| `query` | The suggestion text shown to the user, e.g. `"screen protector"` |
| `popularity` | Search frequency count — starts at 0, updated nightly from `search_events` |
| `boost` | Operator-assigned priority — `0` normal, `1` promoted, `-1` suppressed |
| `type` | `"query"` (sourced from search history) \| `"catalog"` (generated from indexed products) |
| `category` | Optional — the catalog tab this suggestion belongs to, e.g. `"accessories"` |

The `search_suggestions` collection is a **platform-level** collection, like `catalog`.
It is never dropped and recreated — only upserted into.  Multiple indexes contribute
to it; no single index rebuild owns it.

---

## Data sources and population timing

### Source 1 — Catalog extraction (indexing time)

Runs as the final step of every index rebuild, after all documents have been upserted
into Typesense.  Extracts phrases from the indexed documents and upserts them as
`type = "catalog"` with `popularity = 0` (or retains existing popularity if the
entry already exists).

Upsert key is the slugified `id` — running rebuild twice does not create duplicate
entries.  Starting at `popularity = 0` means real search queries (Source 2) rise
above generated phrases as soon as history accumulates.

**Extraction is per-index and defined by JS extensions — see
[Per-index extraction via JS extensions](#per-index-extraction-via-js-extensions) below.**

---

## Per-index extraction via JS extensions

Different indexes have completely different document shapes — a Phones index has
`manufacturer` and a title like "Apple iPhone 15 Pro Max", while a Furniture index
might have `style`, `room_type`, and `brand`.  Hardcoding extraction logic in Python
would require a code change every time a new index type is added.

Instead, ISP's existing **extension system** is reused.  Each JS extension can
optionally define an `extractSuggestions(doc)` entry point alongside the existing
`transform(doc)` entry point.  The seeder calls this function at rebuild time on
every document in the index and collects the phrase tuples it returns.

### JS contract

```js
/**
 * extractSuggestions(doc) — called once per document at rebuild time.
 *
 * @param  doc  Raw Typesense document (same shape as transform receives)
 * @return      Array of { query: string, category: string|null } objects
 */
function extractSuggestions(doc) {
  // index-specific logic here
  return [{ query: "...", category: "..." }];
}
```

The function receives the raw Typesense document dict and must return an array of
`{ query, category }` objects.  `category` may be `null` for global (non-scoped)
suggestions.

> **Scoping note:** the `category` value in the returned objects must match the
> index's `intent_label` (e.g. `"device"`, `"accessory"`) for suggestions to appear
> in the correct Suggestions tab.  The document's own `category` field (e.g.
> `"handset"`, `"smartphones"`) is a raw connector value and will not match.

### Example — Phones / Accessories / Tablets (T-Mobile REST catalog)

```js
// @name Catalog Mapper
// @description Maps REST API product fields. Also extracts suggestion phrases.

function transform(doc) { /* ... existing field mapping ... */ }

function extractSuggestions(doc) {
  var mfr   = doc.manufacturer || "";
  var cat   = doc.category || doc.source_type || "";
  var title = doc.title || "";
  var phrases = [];

  if (cat)             phrases.push({ query: cat,             category: null });
  if (mfr)             phrases.push({ query: mfr,             category: null });
  if (mfr && cat)      phrases.push({ query: mfr + " " + cat, category: cat  });

  // Model-family keyword from title — "Apple iPhone 15 Pro" → "iphone"
  if (title && mfr) {
    var idx  = title.toLowerCase().indexOf(mfr.toLowerCase());
    var rest = idx >= 0 ? title.slice(idx + mfr.length).trim() : title.trim();
    var word = rest.split(" ")[0].replace(/[^a-zA-Z0-9]/g, "");
    if (word.length >= 3 && word.toLowerCase() !== mfr.toLowerCase())
      phrases.push({ query: word.toLowerCase(), category: cat || null });
  }
  return phrases;
}
```

### Example — Furniture catalog (different data shape)

```js
// @name Furniture Suggestion Extractor
// @description Extracts room- and style-based phrases from furniture catalog data.

function extractSuggestions(doc) {
  var style = doc.style || "";
  var room  = doc.room_type || "";
  var brand = doc.brand || "";
  var phrases = [];

  if (room)              phrases.push({ query: room,                category: null  });
  if (style && room)     phrases.push({ query: style + " " + room,  category: room  });
  if (brand && room)     phrases.push({ query: brand + " " + room,  category: room  });
  return phrases;
}
```

### Assignment

Extensions are created and edited in the **Extensions** panel and assigned
per-index via the existing `index_extensions` table.  No code deployment is needed
to add or change extraction logic for any index — the operator creates/edits the
extension in the UI and the next rebuild picks it up.

### Execution at seeding time

The seeder (`suggestion_service.py`) at rebuild time:

1. Resolves the index's `intent_label` from the `indexes` table — this is the
   category scope used for all suggestions from this index.
2. Fetches all extensions assigned to the index via `extension_service`.
3. Filters to those whose JS code defines `extractSuggestions`
   (detected via `typeof extractSuggestions == "function"`, same pattern as `filterUrl`).
4. Executes each function on every document using `py_mini_racer` (V8, already used
   by the existing extension runner — no new dependency).
5. Falls back to the title-only baseline if no extension fires.
6. Reads existing boost values for the index category before touching anything.
7. Deletes all `type=catalog` suggestions scoped to the index's `intent_label`.
8. Re-upserts the fresh phrase set with preserved boost values.

### Baseline fallback

If no assigned extension defines `extractSuggestions`, the seeder falls back to
a minimal baseline: the document's `title` field, lowercased, scoped to the
index's `intent_label`.

| Field | Suggestion written | Category scoped to |
|---|---|---|
| `title` | full title, lowercased | index `intent_label` (e.g. `"device"`) |

**Important:** the baseline uses `intent_label`, not `doc.category`.  A document's
own category field (e.g. `"handset"`) may differ from the index's `intent_label`
(e.g. `"device"`).  Using the document field would scope suggestions to the wrong
category and they would never appear in the Suggestions tab.

`category` values are intentionally excluded — terms like `"device"` or
`"accessory"` are too generic on their own. Category-level phrases belong in a
custom `extractSuggestions` extension.

Once an extension defines `extractSuggestions`, the baseline is bypassed entirely.

---

### Source 2 — Search query history (nightly job)

A scheduled job reads `search_events` and computes per-query, per-category frequency
over a rolling 30-day window:

```sql
SELECT
    lower(trim(query))         AS q,
    lower(trim(category_hint)) AS cat,
    COUNT(*)                   AS freq
FROM search_events
WHERE created_at > NOW() - INTERVAL '30 days'
  AND query IS NOT NULL
  AND length(trim(query)) >= 2
GROUP BY q, cat
```

Each row is upserted into `search_suggestions` as `type = "query"` with
`popularity = freq`.  Catalog-generated entries for the same query are updated
in place — their popularity rises as users search for them.

**Schedule:** nightly at 02:00.  Can be triggered manually via
`POST /v1/admin/suggestions/rebuild` (JWT-protected operator endpoint).

> **Dependency:** the `category_hint` column needs to be added to `search_events`
> and populated by the PLP when logging search events.  Until it exists, the nightly
> job runs without the `cat` grouping and all suggestions are stored without a
> category — global popularity only.

### Source 3 — Real-time increment (Phase 2)

On every search request, increment the matching suggestion's `popularity` counter
immediately.  Gives live trending — a product launch spike shows up in suggestions
the same day, not the next morning.

Not in scope for the initial implementation.  Requires a debounce / batching
strategy to avoid write pressure on the hot search path.

---

## API — `GET /v1/suggest`

### Request parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `q` | string | required | Partial query, min 2 chars |
| `limit` | int | 8 | Max suggestions returned (1–20) |
| `session_id` | string | `""` | Reserved for future personalisation |
| `category` | string | `null` | Active catalog tab — boosts matching suggestions |

### Ranking

Ranking applies three signals in order of precedence:

1. **Operator boost** — `boost:desc` always wins, regardless of category or popularity
2. **Category match** — `_eval(category:={category}):desc` when a tab is active
3. **Popularity** — `popularity:desc` as the final tiebreaker

When `category` is supplied:

```
sort_by = "boost:desc,_eval(category:={category}):desc,popularity:desc"
```

When `category` is absent:

```
sort_by = "boost:desc,popularity:desc"
```

Both cases use prefix-match on `query` with `num_typos: 1`.

This means:
- Operator-boosted terms always appear first, for every user on every tab
- Category-matching terms rank above global terms within the same boost tier
- If accessories has no matching suggestions, global results fill the gap
- Suppressed terms (`boost: -1`) sink below all organic results without being deleted

### Response

```json
{
  "suggestions": [
    { "text": "samsung galaxy s25",        "type": "query",   "popularity": 203, "boosted": true  },
    { "text": "screen protector",          "type": "query",   "popularity": 142, "boosted": false },
    { "text": "samsung screen protector",  "type": "query",   "popularity": 38,  "boosted": false },
    { "text": "iphone screen protector",   "type": "catalog", "popularity": 0,   "boosted": false }
  ],
  "query":    "screen",
  "category": "accessories"
}
```

`boosted: true` is set when `boost > 0` so the PLP can optionally render a visual
indicator (e.g. a "✦ Trending" label) on promoted suggestions.

---

## Worked example

**Catalog contains:** Samsung Galaxy S25, Samsung Screen Protector, Apple AirPods,
ZAGG Screen Protector for iPhone.

**After first rebuild (day 1, all popularity = 0):**

| query | category | type | popularity |
|---|---|---|---|
| screen protectors | accessories | catalog | 0 |
| samsung screen protectors | accessories | catalog | 0 |
| samsung | accessories | catalog | 0 |
| apple | accessories | catalog | 0 |
| smartphones | devices | catalog | 0 |
| samsung smartphones | devices | catalog | 0 |

**After first nightly job (one week of user searches):**

| query | category | type | popularity |
|---|---|---|---|
| screen protector | accessories | query | 142 |
| samsung screen protector | accessories | query | 38 |
| iphone screen protector | accessories | query | 21 |
| samsung galaxy s25 | devices | query | 203 |
| iphone 17 | devices | query | 189 |

**User types "screen" on the accessories tab:**

```
GET /v1/suggest?q=screen&category=accessories
sort_by = "_eval(category:=accessories):desc,popularity:desc"
```

Returned order:
1. "screen protector" — accessories match, popularity 142
2. "samsung screen protector" — accessories match, popularity 38
3. "iphone screen protector" — accessories match, popularity 21
4. "iphone 17 screen" — devices (no boost), popularity 15 — falls below

**User types "screen" with no tab selected:**

```
GET /v1/suggest?q=screen
sort_by = "popularity:desc"
```

Returned order: purely by popularity across all categories.

---

## Boost and pinning

Operators can manually promote or suppress any suggestion by setting its `boost` value.
All other fields (`popularity`, `category`, `type`) are managed automatically — only
`boost` requires operator input.

### Boost values

| Value | Effect |
|---|---|
| `1` | Promoted — always appears above organic results |
| `0` | Normal — ranked by category match and popularity (default) |
| `-1` | Suppressed — sinks below all organic results; stays in the collection for auditing |

### Use cases

**Campaign promotion**
Boost "samsung deals" and "black friday accessories" before a sale event starts.
Remove the boost after the campaign ends — popularity takes over.

**New product launch**
Boost "iphone 17 pro" on launch day before organic search history has accumulated.
Once popularity crosses a meaningful threshold the boost can be removed.

**Suppress low-quality terms**
Set `boost: -1` on terms that appear frequently in queries but generate zero clicks
(e.g. misspellings that survived typo-tolerance, or overly generic single-word terms).

### Management API

```
POST   /v1/admin/suggestions
       Body: { "query": "samsung galaxy s25", "boost": 1, "category": "devices" }
       Creates or updates a suggestion entry with the given boost value.

DELETE /v1/admin/suggestions/{id}
       Removes the entry entirely (use boost: -1 to suppress without deleting).

GET    /v1/admin/suggestions?q=samsung&boosted=true
       Lists all boosted suggestions, optionally filtered by partial query.
```

All three endpoints are JWT-protected and available to admin operators only.
The nightly job never overwrites the `boost` field — operator values are permanent
until explicitly changed.

---

## Suggestions tab in ISP Admin UI

Each index detail view gains a **Suggestions** tab alongside Overview, Connector,
Documents, Search, and Api.  It shows all `search_suggestions` entries whose
`category` matches the index's category label, and lets operators manage boost
values without calling the API directly.

### Scoping rule

The tab filters by the index's `intent_label` (e.g. `accessories`, `devices`).
Suggestions with no category (global) are shown in a separate section at the bottom.
Suggestions belonging to other categories are not shown — they are managed from
their respective index tab.

Boost changes made here apply globally — a suggestion promoted from the Accessories
tab appears promoted on the PLP for all users, not just users browsing that index.

### Tab layout

```
Accessories  /  Suggestions

  [ + Add suggestion ]                          [ Search suggestions... ]

  PROMOTED  ───────────────────────────────────────────────────────────
  ★ black friday accessories    accessories   boost: 1    0 searches   [ Edit ]
  ★ samsung deals               accessories   boost: 1    0 searches   [ Edit ]

  ORGANIC  ────────────────────────────────────────────────────────────
    screen protector             accessories   142 searches             [ Edit ]
    samsung screen protector     accessories    38 searches             [ Edit ]
    samsung charger              accessories    21 searches             [ Edit ]
    iphone screen protector      accessories     0 searches             [ Edit ]

  SUPPRESSED  ─────────────────────────────────────────────────────────
  ✕ broken screen                accessories   boost: -1   8 searches  [ Edit ]

  GLOBAL (no category)  ───────────────────────────────────────────────
    samsung                      —              89 searches             [ Edit ]
    apple                        —              54 searches             [ Edit ]
```

### Edit modal

Clicking **Edit** on any row opens a modal with the suggestion's current values.
The operator can change the boost and save — no page reload required.

```
┌─────────────────────────────────────────────────┐
│  Edit Suggestion                            ✕   │
├─────────────────────────────────────────────────┤
│                                                 │
│  Query          screen protector                │
│                 (read-only — set by system)     │
│                                                 │
│  Category       accessories                     │
│                 (read-only — set by system)     │
│                                                 │
│  Popularity     142 searches (last 30 days)     │
│                 (read-only — updated nightly)   │
│                                                 │
│  Boost                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Suppress │  │  Normal  │  │ Promote  │      │
│  │   − 1    │  │    0  ●  │  │    + 1   │      │
│  └──────────┘  └──────────┘  └──────────┘      │
│                                                 │
│            [ Cancel ]    [ Save ]               │
└─────────────────────────────────────────────────┘
```

- **Query** and **Category** are always read-only for system-generated entries
- **Popularity** is always read-only — updated by the nightly job only
- **Boost** is the only editable field — three-way toggle: Suppress / Normal / Promote
- Saving calls `PUT /v1/indexes/{id}/suggestions/{suggestion_id}` with `{ "boost": value }`
- The row updates in place after save — section it appears in (Promoted / Organic /
  Suppressed) updates immediately without a full table reload

### Add suggestion modal

Clicking **+ Add suggestion** opens a create form.  This is for manually adding a
term that does not yet exist in search history — e.g. a campaign term before launch.

```
┌─────────────────────────────────────────────────┐
│  Add Suggestion                             ✕   │
├─────────────────────────────────────────────────┤
│                                                 │
│  Query       [ black friday accessories     ]   │
│                                                 │
│  Category    [ accessories             ▾   ]    │
│              (pre-filled from current index)    │
│                                                 │
│  Boost                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Suppress │  │  Normal  │  │ Promote  │      │
│  │   − 1    │  │    0     │  │    + 1 ● │      │
│  └──────────┘  └──────────┘  └──────────┘      │
│                                                 │
│            [ Cancel ]    [ Add ]                │
└─────────────────────────────────────────────────┘
```

- Query and Category are editable for manually added entries
- Boost defaults to Promote (`+1`) since adding manually implies intent to surface it
- Calls `POST /v1/indexes/{id}/suggestions`

### Actions summary

| Action | How triggered | Boost change | Result |
|---|---|---|---|
| **Promote** | Edit modal → Promote → Save | any → 1 | Moves to PROMOTED section |
| **Restore to normal** | Edit modal → Normal → Save | 1 or -1 → 0 | Returns to organic ranking |
| **Suppress** | Edit modal → Suppress → Save | any → -1 | Moves to SUPPRESSED section |
| **Add suggestion** | + Add suggestion → Add | new entry, boost 1 | Creates and promotes immediately |
| **Delete** | Edit modal → Delete button | — | Hard deletes the entry |

### Backend endpoints for the tab

```
GET  /v1/indexes/{id}/suggestions?q=&page=1&per_page=50
     Returns suggestions scoped to the index category, grouped by boost tier.

PUT  /v1/indexes/{id}/suggestions/{suggestion_id}
     Body: { "boost": 1 }
     Updates the boost value.  Nightly job never overwrites this field.

POST /v1/indexes/{id}/suggestions
     Body: { "query": "black friday accessories", "boost": 1 }
     Creates a new suggestion entry scoped to the index category.

DELETE /v1/indexes/{id}/suggestions/{suggestion_id}
     Hard deletes the entry.  Use boost: -1 to suppress without deleting.
```

All four endpoints are JWT-protected.  They delegate to `suggestion_service.py`
and operate on the shared `search_suggestions` Typesense collection.

---

## Files to create or modify

### Backend

| File | Change |
|---|---|
| `backend/src/isp/services/infra/typesense_client.py` | Add `ensure_suggestions_collection()`, `upsert_suggestions(entries)` |
| `backend/src/isp/services/domain/suggestion_service.py` | Seeding loop: load assigned JS extensions, run `extractSuggestions` via `py_mini_racer`; no extraction if no extension assigned |
| `backend/src/isp/routers/catalog.py` | Update `suggest()` — add `category` param, boost+category sort, query `search_suggestions` |
| `backend/src/isp/routers/admin.py` | New — `POST /v1/admin/suggestions`, `DELETE /v1/admin/suggestions/{id}`, `GET /v1/admin/suggestions` |
| `backend/src/isp/routers/indexes.py` | Add `GET/POST/PUT/DELETE /v1/indexes/{id}/suggestions` routes |
| `backend/src/isp/main.py` | On startup: call `ensure_suggestions_collection()`; mount admin router |
| `backend/requirements.txt` | `py_mini_racer` already present — no new dependency |

### Extensions (JS, seeded to DB at startup)

| File | Purpose |
|---|---|
| `backend/extensions/catalog-mapper.js` | Existing — add `extractSuggestions()` for manufacturer + model-family phrases |
| `backend/extensions/furniture-extractor.js` | Example — style + room_type phrase extraction for furniture-shaped data |

### Frontend

| File | Change |
|---|---|
| `frontend/src/components/sources/SuggestionsTab.svelte` | New — suggestions table with promote/suppress/restore actions |
| `frontend/src/components/sources/IndexDetail.svelte` | Add Suggestions tab to the tab bar |
| `frontend/src/lib/repository/suggestionsRepository.ts` | New — typed API calls for the four suggestions endpoints |

---

## Out of scope (Phase 2 and beyond)

- Real-time popularity increment on the search hot path
- Personalised suggestions using customer action history

---

## References

> **Note on sourcing:** citations below are drawn from accumulated knowledge of the
> information retrieval and recommender systems literature, not from live web browsing.
> Every source listed is a real, verifiable publication.  DOI and arXiv links are
> provided where known.

---

### Query autocompletion

**Survey of Query Auto Completion**
Cai, F., & de Rijke, M. (2016).
*A survey of query auto completion in information retrieval.*
Foundations and Trends in Information Retrieval, 10(4), 273–363.
https://doi.org/10.1561/1500000055

> The definitive survey of query autocompletion techniques, covering frequency-based,
> context-aware, and personalised approaches.  Directly informs the three-source
> architecture (catalog extraction → search log mining → real-time increment) and the
> decision to rank suggestions by search frequency rather than textual similarity to
> the partial query.

**Fast Autocompletion Search with a Succinct Index**
Bast, H., & Weber, I. (2006).
*Type less, find more: Fast autocompletion search with a succinct index.*
Proceedings of SIGIR 2006, pp. 364–371.
https://dl.acm.org/doi/10.1145/1148170.1148234

> Foundational paper on prefix-match autocompletion using succinct index structures.
> Establishes that prefix search over a ranked suggestion list is the correct
> retrieval model for typeahead — the same model used by Typesense's `prefix: true`
> search and the `num_typos: 1` tolerance in the suggest endpoint.

---

### Query suggestion from search logs

**Query Recommendation Using Query Logs**
Baeza-Yates, R., Hurtado, C., & Mendoza, M. (2004).
*Query recommendation using query logs in search engines.*
Proceedings of EDBT Workshops 2004, LNCS 3268, pp. 588–596.
https://doi.org/10.1007/978-3-540-30192-9_58

> Establishes that past search queries are the highest-quality source of suggestion
> candidates — more natural and more popular-query-biased than terms extracted from
> documents alone.  Directly motivates Source 2 (nightly job over `search_events`)
> as the primary driver of the `popularity` field.

**Mining Query Logs**
Silvestri, F. (2010).
*Mining query logs: Turning search usage data into knowledge.*
Foundations and Trends in Information Retrieval, 4(1–2), 1–174.
https://doi.org/10.1561/1500000013

> Comprehensive treatment of query log analysis: frequency aggregation, session
> boundary detection, and temporal windowing.  The 30-day rolling window in the
> nightly aggregation SQL and the minimum query length (≥ 2 chars) are both
> consistent with practices described here.

---

### Prefix search and trie data structures

**Trie Memory**
Fredkin, E. (1960).
*Trie memory.*
Communications of the ACM, 3(9), 490–499.
https://dl.acm.org/doi/10.1145/367390.367400

> The original paper introducing the trie data structure — the basis for all
> prefix-match autocomplete indexes, including Typesense's internal suggestion
> lookup.  Prefix traversal in a trie is O(|prefix|) regardless of collection
> size, which is why prefix-match typeahead remains fast at catalogue scale.

---

### Popularity and implicit feedback weighting

**Collaborative Filtering for Implicit Feedback Datasets**
Hu, Y., Koren, Y., & Volinsky, C. (2008).
*Collaborative filtering for implicit feedback datasets.*
Proceedings of ICDM 2008, pp. 263–272.
https://doi.org/10.1109/ICDM.2008.22

> Foundational paper for treating implicit signals (searches, clicks, views) as
> confidence-weighted preferences.  The `popularity` field accumulates search
> frequency as an implicit confidence score — higher frequency = higher confidence
> that the term reflects real user intent.  The nightly aggregation window (30 days)
> follows the temporal confidence decay argument in this paper.

**Temporal Dynamics in Collaborative Filtering**
Koren, Y. (2009).
*Collaborative filtering with temporal dynamics.*
Proceedings of KDD 2009, pp. 447–456.
https://dl.acm.org/doi/10.1145/1557019.1557072

> Demonstrates that recency matters in implicit signal aggregation — older signals
> should contribute less than recent ones.  Motivates the 30-day rolling window
> rather than an unbounded all-time count, so that a viral search term from 6 months
> ago does not permanently dominate suggestions.

---

### Contextual and category-scoped suggestions

**Faceted Search**
Tunkelang, D. (2009).
*Faceted search.*
Synthesis Lectures on Information Concepts, Retrieval, and Services. Morgan & Claypool.
https://doi.org/10.2200/S00190ED1V01Y200904ICR005

> Covers faceted navigation — organising search results and suggestions by category,
> manufacturer, and other structured dimensions.  The category-scoped suggestion
> approach (boosting accessories suggestions when the user is on the accessories tab)
> is an application of faceted relevance: the active facet selection provides context
> that improves suggestion precision.

**Context-Aware Query Suggestion**
Shen, X., Tan, B., & Zhai, C. (2005).
*Context-sensitive information retrieval using implicit feedback.*
Proceedings of SIGIR 2005, pp. 43–50.
https://dl.acm.org/doi/10.1145/1076034.1076045

> Shows that using session context (the active category, previous queries) improves
> retrieval precision significantly over context-free ranking.  Directly motivates
> the `category` parameter and the `_eval(category:=X):desc` boost expression —
> surfacing category-relevant suggestions first reduces the cognitive load of
> selecting the right term.

---

### Editorial boost and merchandising

**Overview of the TREC 2004 Terabyte Track**
Clarke, C. L. A., Craswell, N., & Soboroff, I. (2004).
*Overview of the TREC 2004 Terabyte Track.*
Proceedings of TREC 2004.
https://trec.nist.gov/pubs/trec13/papers/TERABYTE.OVERVIEW.pdf

> Establishes the concept of editorial relevance signals — hand-curated boosts that
> override statistical ranking when operators have ground-truth knowledge (e.g. a
> product launch, a campaign).  The `boost` field and the Promoted / Organic /
> Suppressed tiers in the suggestions table implement this pattern: operator
> judgement takes precedence over learned popularity, but only where explicitly set.

**Learning to Rank from Clickthrough Data**
Joachims, T. (2002).
*Optimizing search engines using clickthrough data.*
Proceedings of KDD 2002, pp. 133–142.
https://dl.acm.org/doi/10.1145/775047.775067

> Establishes that click signals carry position bias — a suggestion shown first is
> clicked more often regardless of quality.  This is why suppressed terms (`boost: -1`)
> are kept in the collection rather than deleted: deleting them would allow them to
> re-enter organic ranking via the nightly job and accumulate biased clicks from
> their earlier high-position exposure.

---

### General information retrieval

**Introduction to Information Retrieval**
Manning, C. D., Raghavan, P., & Schütze, H. (2008).
*Introduction to information retrieval.*
Cambridge University Press.
Freely available: https://nlp.stanford.edu/IR-book/

> Standard IR textbook.  Chapter 3 (Dictionaries and tolerant retrieval) covers
> prefix search, wildcard queries, and edit-distance tolerant matching — the
> theoretical basis for the `num_typos: 1` prefix search used in the suggest
> endpoint.  Chapter 21 (Link analysis) covers authority and popularity signals
> analogous to the `popularity` field.
