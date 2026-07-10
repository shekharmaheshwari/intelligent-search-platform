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
    { "name": "id",               "type": "string"   },
    { "name": "query",            "type": "string"   },
    { "name": "popularity",       "type": "int32",   "facet": false },
    { "name": "boost",            "type": "int32",   "facet": false },
    { "name": "type",             "type": "string",  "facet": true  },
    { "name": "category",         "type": "string",  "facet": true,  "optional": true },
    { "name": "mode",             "type": "string",  "optional": true },
    { "name": "url",              "type": "string",  "optional": true },
    { "name": "featured_doc_ids", "type": "string[]","optional": true }
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
| `mode` | Optional — `"link"` (navigate to URL) \| `"search"` (run search). Defaults to `"search"` |
| `url` | Optional — destination URL when `mode = "link"` |
| `featured_doc_ids` | Optional — ordered list of Typesense doc IDs to pin at the top of search results when `mode = "search"` |

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
 * @param  doc     ISP catalog document already mapped to the Typesense schema.
 *                 Not the raw API payload — fields like title, manufacturer,
 *                 operating_system, price, and metadata (JSON string) are available.
 * @return         Array of { query, category?, url?, doc_id? } objects.
 *                 - category: optional — when omitted the seeder uses the index's
 *                   intent_label automatically.
 *                 - url: optional — when present, seeder sets mode = "link" on the
 *                   suggestion so the PLP navigates directly rather than searching.
 *                 - doc_id: optional — when present, seeder stores it in
 *                   featured_doc_ids so that doc is pinned at position #1 for
 *                   search-mode suggestions.
 */
function extractSuggestions(doc) {
  // index-specific logic here
  return [{ query: "..." }];
}
```

The function receives the **already-mapped Typesense document** (not the raw API
payload) and returns an array of `{ query, category?, url?, doc_id? }` objects.

**`category` is optional.** When omitted or `null`, the seeder automatically scopes
the suggestion to the index's `intent_label` (read from the `indexes` table).  This
means a single extension can be assigned to any device-type index (Phones, Tablets,
Smartwatches) without knowing or hardcoding the index's category.

> **If you do set `category` explicitly:** it must match the index's `intent_label`
> (e.g. `"device"`, `"accessory"`), not the document's own `category` field
> (e.g. `"handset"`, `"smartphones"`), which is a raw connector value and will not
> match the Suggestions tab filter.

### Example — Device catalog (`backend/extensions/device_suggestions.js`)

Assigned to Phones, Tablets, and Smartwatches.  Extracts only the product title
and any aliases stored in metadata — simple, reliable, no hardcoded field logic.
Real user searches (Source 2) fill in richer phrases over time as organic traffic
accumulates.

```js
// @name Device Suggestions
// @description Generates typeahead search suggestions from ISP catalog device documents.
// Assign to Phones, Tablets, or Smartwatches indexes.

function extractSuggestions(doc) {
  var results = [];
  var seen = {};

  function add(query) {
    var q = String(query || '').trim().toLowerCase();
    if (q && !seen[q]) { seen[q] = true; results.push({ query: q }); }
  }

  add(doc.title);

  var meta = {};
  try { meta = JSON.parse(doc.metadata || '{}'); } catch (e) {}
  (meta.aliases || []).forEach(function(a) { add(a); });

  return results;
}
```

Sample output for "iPhone 17 Pro Max" (alias: "apple iphone 17 pro max"):

| query | scoped to |
|---|---|
| `iphone 17 pro max` | `intent_label` |
| `apple iphone 17 pro max` | `intent_label` |

### Example — Furniture catalog (different data shape)

```js
// @name Furniture Suggestion Extractor
// @description Extracts room- and style-based phrases from furniture catalog data.

function extractSuggestions(doc) {
  var style = doc.style || "";
  var room  = doc.room_type || "";
  var brand = doc.brand || "";
  var phrases = [];

  if (room)          phrases.push({ query: room });
  if (style && room) phrases.push({ query: style + " " + room });
  if (brand && room) phrases.push({ query: brand + " " + room });
  return phrases;
}
```

### Assignment

Extensions are created and edited in the **Extensions** panel and assigned
per-index via the existing `index_extensions` table.  No code deployment is needed
to add or change extraction logic for any index — the operator creates/edits the
extension in the UI and the next rebuild picks it up.

### Execution at seeding time

Python (`suggestion_service.py`) is pure orchestration — no phrase logic lives here:

1. Fetches all documents from the index's Typesense collection (paginated, 250/page).
2. Resolves the index's `intent_label` from the `indexes` table.
3. Loads assigned extensions via `extension_service` and filters to those that define
   `extractSuggestions` (detected via `typeof extractSuggestions == "function"`).
4. Executes each function on every document using `py_mini_racer` (V8).
   **If no extension defines `extractSuggestions`, seeding stops here — nothing is upserted.**
5. For each returned phrase: if `category` is `null` or omitted, substitutes
   `intent_label` so the suggestion is correctly scoped for the refresh cycle.
6. Reads existing boost values for the index category before touching anything.
7. Deletes stale `type=catalog` entries scoped to both `intent_label` and any raw
   document category slugs (catches legacy entries from older extractors).
8. Re-upserts the fresh phrase set with preserved boost values.

> **No Python fallback.** If an index has no extension with `extractSuggestions`,
> that index produces no catalog suggestions.  All phrase decisions live in JS.

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

To re-seed catalog suggestions for a single index without a full data rebuild
(e.g. after editing an `extractSuggestions` extension), use:

```
POST /v1/admin/suggestions/seed/{index_id}
```

This runs the same 8-step refresh as the post-rebuild hook — reads existing
Typesense documents, runs JS extractors, preserves boost, deletes stale entries,
re-upserts — but without touching connector config or re-fetching from the source API.

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
    {
      "text": "samsung galaxy s25",
      "type": "query",
      "popularity": 203,
      "boosted": true,
      "mode": "search"
    },
    {
      "text": "transfer my number",
      "type": "catalog",
      "popularity": 0,
      "boosted": false,
      "mode": "link",
      "url": "/my-account/port-in-dashboard"
    },
    {
      "text": "iphone 17 pro max",
      "type": "query",
      "popularity": 189,
      "boosted": false,
      "mode": "search",
      "featured_doc_ids": ["f1c835831b6aeb3e"]
    }
  ],
  "query":    "iphone",
  "category": "devices"
}
```

`boosted: true` is set when `boost > 0` so the PLP can optionally render a visual
indicator (e.g. a "✦ Trending" label) on promoted suggestions.

`mode`, `url`, and `featured_doc_ids` are only present when set — absent means
`"search"` mode with no pinned docs.

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
The operator can change boost, mode, URL, and pinned docs — no page reload required.

```
┌─────────────────────────────────────────────────┐
│  Edit Suggestion                            ✕   │
├─────────────────────────────────────────────────┤
│                                                 │
│  Query          iphone 17 pro max               │
│                 (read-only — set by system)     │
│                                                 │
│  Category       devices                         │
│                 (read-only — set by system)     │
│                                                 │
│  Popularity     189 searches (last 30 days)     │
│                 (read-only — updated nightly)   │
│                                                 │
│  Mode                                           │
│  ● Search — run a search for this query         │
│  ○ Link   — navigate directly to URL            │
│                                                 │
│  Pin to top  (shown when mode = Search)         │
│  ┌─────────────────────────────────────────┐   │
│  │  f1c835831b6aeb3e  ✕                    │   │
│  └─────────────────────────────────────────┘   │
│  [ enter doc id...  ]  [ Add ]  [ Browse ]      │
│  (ordered list — first id appears at position 1)│
│                                                 │
│  URL  (shown when mode = Link)                  │
│  [ /my-account/port-in-dashboard          ]     │
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
- **Mode** radio toggles between `search` (default) and `link`
- **Pin to top** is a multi-chip input (search mode only) — each chip is a Typesense
  doc ID; chips are ordered and the first ID is pinned at position #1 in search results.
  **Browse** opens `DocPickerModal` — a search dialog to find and select docs by title
  instead of typing IDs manually.
- **URL** field is shown only when mode is `link`
- **Boost** three-way toggle: Suppress / Normal / Promote
- Saving calls `PUT /v1/indexes/{id}/suggestions/{suggestion_id}` with the changed fields
- The row updates in place after save

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
     Each entry includes mode, url, and featured_doc_ids where set.

PUT  /v1/indexes/{id}/suggestions/{suggestion_id}
     Body: {
       "boost": 1,
       "mode": "link",
       "url": "/my-account/port-in-dashboard",
       "featured_doc_ids": ["f1c835831b6aeb3e", "a2b3c4d5e6f70001"]
     }
     All fields are optional — only provided keys are updated.
     Nightly job never overwrites boost, mode, url, or featured_doc_ids.

POST /v1/indexes/{id}/suggestions
     Body: { "query": "black friday accessories", "boost": 1 }
     Creates a new suggestion entry scoped to the index category.

DELETE /v1/indexes/{id}/suggestions/{suggestion_id}
     Hard deletes the entry.  Use boost: -1 to suppress without deleting.
```

All four endpoints are JWT-protected.  They delegate to `suggestion_service.py`
and operate on the shared `search_suggestions` Typesense collection.

---

## Files modified

### Backend

| File | Change |
|---|---|
| `backend/src/isp/services/infra/typesense_client.py` | `ensure_suggestions_collection()`, `upsert_suggestions()`; `get_suggestions()`, `update_suggestion()`; `get_featured_doc_ids_for_query()`; `pinned_hits` param on `search_documents` and `search_with_vec` |
| `backend/src/isp/services/domain/suggestion_service.py` | Seeding loop via `py_mini_racer`; reads `url` and `doc_id` from `extractSuggestions` output; sets `mode` and `featured_doc_ids` |
| `backend/src/isp/routers/catalog.py` | `suggest()` with `category` param, boost+category sort, `mode`/`url`/`featured_doc_ids` in response |
| `backend/src/isp/routers/indexes.py` | `GET/POST/PUT/DELETE /v1/indexes/{id}/suggestions`; PUT accepts `mode`, `url`, `featured_doc_ids` |
| `backend/src/isp/routers/search.py` | `_run_search()` auto-applies `get_featured_doc_ids_for_query`; passes `pinned_hits` to all search calls |
| `backend/src/isp/models/search.py` | Added `pinned_hits: list[str]` to `SearchRequest` |
| `backend/src/isp/main.py` | On startup: calls `ensure_suggestions_collection()` |

### Extensions (JS)

| File | Purpose |
|---|---|
| `backend/extensions/account_suggestions.js` | Returns `{ query, url, doc_id }` per trigger phrase — seeds link mode + auto-pin |
| `backend/extensions/device_suggestions.js` | Returns `{ query }` per doc — title + metadata aliases |

### Frontend (ISP admin UI)

| File | Change |
|---|---|
| `frontend/src/components/sources/SuggestionsTab.svelte` | Suggestions table; edit modal with Mode radio, URL field, multi-chip Pin to top, Browse button |
| `frontend/src/components/sources/DocPickerModal.svelte` | New — document search-and-select dialog with body portal for correct z-index stacking |
| `frontend/src/components/sources/IndexDetail.svelte` | Suggestions tab added to tab bar |
| `frontend/src/lib/types/suggestions.types.ts` | `Suggestion` type with `mode`, `url`, `featured_doc_ids` |
| `frontend/src/lib/api.ts` | `updateSuggestion(indexId, suggestionId, fields)` |
| `frontend/src/lib/repository/SuggestionsRepository.ts` | Typed calls for all four suggestions endpoints |
| `frontend/src/lib/services/suggestions/SuggestionsService.ts` | Service layer for suggestions operations |

### PLP

| File | Change |
|---|---|
| `plp/src/lib/types.ts` | `Suggestion` with `mode`, `url`, `featured_doc_ids`; `IndexSearchParams` with `pinned_hits` |
| `plp/src/components/SearchBar.svelte` | `selectSuggestion` handles link mode; `_matchSuggestion` for Enter-key path; `triggerSearch(pinnedHits?)` |
| `plp/src/lib/controller/catalogController.ts` | `loadProducts` options: `pinnedHits?`; passed to `searchIndex` as `pinned_hits` |

---

## Suggestion Link Mode

### Motivation

For support and account indexes (e.g. the Account GSheet index), each trigger phrase maps to exactly one page.  "Transfer my number" has one right answer — the Port-In Dashboard.  Sending the customer directly to that page is faster and clearer than showing a results list.

For catalog indexes (Phones, Accessories), suggestions trigger a search — "samsung galaxy s25" benefits from a results page.  Some queries benefit further from having a specific product pinned to the top of results: "iphone 17 pro max" can pin the exact model to position #1 so the customer sees it immediately.

---

### Concept

Each suggestion has a **mode** field:

| Mode | PLP behavior | Use case |
|---|---|---|
| `search` | Fill search bar and run search (default) | Catalog queries, broad terms |
| `link` | Navigate directly to the attached URL | Support pages, account pages, known single-answer intents |

Additionally, `search`-mode suggestions can carry a `featured_doc_ids` list — Typesense document IDs that are forced to the top positions of the search results, in order.

The operator configures mode per suggestion in the edit modal.  For account indexes, suggestions auto-populate as `link` because `extractSuggestions` returns a `url`.

---

### Pin to top — featured_doc_ids

`featured_doc_ids` is an ordered list of Typesense document IDs applied to `search`-mode suggestions.  The first ID lands at position #1, the second at #2, and so on.

**How pinning works in the backend (`search.py` + `typesense_client.py`):**

1. When the client calls `/v1/indexes/{id}/search/public`, `SearchRequest.pinned_hits` carries any client-supplied IDs.
2. Regardless of what the client sends, the backend also calls `get_featured_doc_ids_for_query(q)` — an exact-match lookup in `search_suggestions` for the search query.
3. If a matching suggestion is found with `featured_doc_ids`, those IDs are used (client-supplied IDs take precedence if both are provided).
4. For **lexical search** (`search_documents`), the IDs are passed as Typesense's native `pinned_hits` parameter in `"id1:1,id2:2"` format.
5. For **hybrid/semantic search** (`search_with_vec`), Typesense's native pinned_hits is not available (Python-side RRF is used).  Instead, any pinned docs missing from the candidate set are fetched individually by ID and injected with an artificially high RRF score to force them to the top.

**Effect:** pinning works from any search client on any port — no PLP code change is required.

---

### SearchRequest.pinned_hits

`POST /v1/indexes/{id}/search/public` (and `/search`) accept an optional `pinned_hits` field:

```json
{
  "q": "iphone 17 pro max",
  "mode": "hybrid",
  "page": 1,
  "per_page": 24,
  "pinned_hits": ["f1c835831b6aeb3e"]
}
```

When absent or empty, the backend auto-applies pinned IDs from the matching suggestion in `search_suggestions` (if any).

---

### extractSuggestions contract

The JS contract supports optional `url` and `doc_id` fields alongside `query`:

```js
/**
 * @returns {Array<{ query: string, category?: string, url?: string, doc_id?: string }>}
 *
 * url:    present → seeder sets mode = "link" on the suggestion
 * doc_id: present → seeder stores [doc_id] in featured_doc_ids for auto-pin
 */
function extractSuggestions(doc) {
  return [
    { query: "transfer my number", url: doc.url },            // link mode
    { query: "iphone 17 pro max",  doc_id: doc.id }           // search mode + pin
  ];
}
```

Backwards-compatible — existing extensions returning `{ query }` only are unaffected.

---

### account_suggestions.js

```js
function extractSuggestions(doc) {
  var results = [];
  var seen = {};
  var meta = {};
  try { meta = JSON.parse(doc.metadata || '{}'); } catch (e) {}

  var triggers = String(meta.Triggers || meta.triggers || '').trim();
  triggers.split(',').forEach(function(phrase) {
    var q = phrase.trim().toLowerCase();
    if (q && q.length >= 3 && !seen[q]) {
      seen[q] = true;
      results.push({ query: q, url: doc.url, doc_id: doc.id });
    }
  });
  return results;
}
```

Every trigger phrase carries both the page URL (sets `mode = "link"`) and the source doc ID (stored in `featured_doc_ids` for search fallback).

---

### PLP typeahead behavior

```typescript
function selectSuggestion(s: Suggestion) {
  if (s.mode === 'link' && s.url) {
    window.location.href = s.url;           // direct navigation, no search
    return;
  }
  appStateStore.update(st => ({ ...st, query: s.text, page: 1 }));
  triggerSearch(s.featured_doc_ids?.length ? s.featured_doc_ids : undefined);
}
```

When the user presses **Enter** without selecting from the dropdown, the PLP does an exact-match lookup against the loaded suggestions (`_matchSuggestion`) to find and apply `featured_doc_ids` for the typed query.

---

### Files modified

| File | Change |
|---|---|
| `backend/src/isp/services/infra/typesense_client.py` | Added `mode`, `url`, `featured_doc_ids` to schema; `get_suggestions()` returns them; `get_featured_doc_ids_for_query(q)` for auto-pin lookup; `search_documents` and `search_with_vec` accept `pinned_hits`; `update_suggestion()` general partial-update |
| `backend/src/isp/services/domain/suggestion_service.py` | `_run_extraction_sync` returns `(query, category, url, doc_id)`; `seed_suggestions_from_index` sets `mode="link"` when url present, `featured_doc_ids=[doc_id]` when doc_id present |
| `backend/src/isp/routers/indexes.py` | `list_index_suggestions` returns `featured_doc_ids`; `update_index_suggestion` accepts `featured_doc_ids` array |
| `backend/src/isp/routers/search.py` | `_run_search()` auto-applies `get_featured_doc_ids_for_query`; passes `pinned_hits` to all search calls |
| `backend/src/isp/models/search.py` | Added `pinned_hits: list[str]` to `SearchRequest` |
| `backend/extensions/account_suggestions.js` | Returns `{ query, url: doc.url, doc_id: doc.id }` for each trigger phrase |
| `frontend/src/lib/types/suggestions.types.ts` | Added `mode`, `url`, `featured_doc_ids` to `Suggestion` type |
| `frontend/src/lib/api.ts` | Added `updateSuggestion(indexId, suggestionId, fields)` |
| `frontend/src/lib/repository/SuggestionsRepository.ts` | Added `updateSuggestion` delegating to `api.updateSuggestion` |
| `frontend/src/lib/services/suggestions/SuggestionsService.ts` | Added `updateSuggestion` delegating to repository |
| `frontend/src/components/sources/SuggestionsTab.svelte` | Edit modal: Mode radio, URL field, multi-chip Pin to top input, Browse button; `saveEdit()` sends `featured_doc_ids` and `mode` |
| `frontend/src/components/sources/DocPickerModal.svelte` | New — search-and-select dialog for choosing pinned doc IDs; uses body portal (`onMount` + `onDestroy`) so it renders above the edit modal's stacking context; `z-index: 500` |
| `plp/src/lib/types.ts` | Added `mode`, `url`, `featured_doc_ids` to `Suggestion`; added `pinned_hits` to `IndexSearchParams` |
| `plp/src/components/SearchBar.svelte` | `selectSuggestion` handles `link` mode and passes `featured_doc_ids`; `_matchSuggestion` for Enter-key path; `triggerSearch` accepts `pinnedHits` |
| `plp/src/lib/controller/catalogController.ts` | `loadProducts` accepts `pinnedHits` option; passes to `searchIndex` as `pinned_hits` |

---

## Out of scope (future)

- Real-time popularity increment on the search hot path
- Personalised suggestions using customer action history
- Multi-client pin conflict resolution (last-write-wins today)

---

## Future: LLM-assisted suggestion enrichment (Phase 3)

### Motivation

The JS extension system extracts phrases that are explicitly present in document
fields — manufacturer, OS, category slug, "for X" device targets.  It cannot
infer terms that are semantically implied but not literally present:

| Document title | What JS extracts | What an LLM would add |
|---|---|---|
| `"Powerstation Mini 5K w/ Stand"` | `"charger"`, `"mophie charger"` | `"power bank"`, `"portable charger"`, `"travel charger"` |
| `"Qi2 Mini Wireless Charging Car Vent Mount"` | `"wireless charger"`, `"car mount"` | `"phone holder for car"`, `"qi2 charger"`, `"magnetic car mount"` |
| `"ZAGG XTR5 Screen Protector"` | `"screen protector"`, `"zagg screen protector"` | `"tempered glass"`, `"premium screen protector"`, `"shatterproof glass"` |

An LLM understands product taxonomy and real user vocabulary in a way that
rule-based extraction cannot match.

### Proposed design

Add an optional `_run_llm_enrichment(docs, intent_label)` step in
`suggestion_service.py` that runs **after** JS extraction, before deduplication.
It is skipped entirely when no LLM API key is configured — so it is opt-in and
never breaks environments without external API access.

```
JS extension
    ↓ phrase_pairs
LLM enrichment  ← opt-in, runs only if ANTHROPIC_API_KEY or OPENAI_API_KEY set
    ↓ additional phrase_pairs
Deduplicate → delete stale → upsert
```

### Batching strategy

Calling the LLM once per document would be slow and expensive.  Instead, batch
10–20 documents per call:

- 542 accessories ÷ 15 per batch = ~37 API calls per rebuild
- ~200 tokens per batch in + ~150 tokens out ≈ ~13 K tokens total
- Cost at Claude Haiku pricing: **~$0.02 per full accessories rebuild**
- Latency: ~30–60 seconds total (parallelisable with `asyncio.gather`)

### Prompt contract

```
You are a search suggestion generator for a telecom accessory catalog.
Given the following product documents, return a JSON array of search phrases
a customer might type to find these products. Phrases should be 2–5 words,
lowercase, generic enough to match multiple products.

Documents:
[{ "title": "...", "category": "...", "manufacturer": "..." }, ...]

Return only: ["phrase 1", "phrase 2", ...]
No explanation.
```

### Implementation notes

- Use the API key already stored in ISP settings (`ANTHROPIC_API_KEY` or
  `OPENAI_API_KEY`) — no new credential management needed.
- Recommended model: **Claude Haiku** or **GPT-4o-mini** — fast and cheap,
  sufficient for structured phrase generation.
- LLM-generated phrases get `type="catalog"` like JS-extracted ones — they
  participate in the same boost and refresh cycle.
- The LLM step is additive: it supplements JS extraction, never replaces it.
  If the LLM call fails, the seeder continues with JS-only results.
- Cap LLM suggestions per document at 5–8 to avoid flooding the collection
  with low-quality phrases.

### Files to add or modify

| File | Change |
|---|---|
| `backend/src/isp/services/domain/suggestion_service.py` | Add `_run_llm_enrichment(docs, intent_label)` — batches docs, calls LLM API, parses response |
| `backend/src/isp/services/infra/llm_client.py` | New — thin wrapper around Anthropic/OpenAI SDK, reads key from settings |
| `backend/src/isp/routers/admin.py` | Expose `llm_enrichment_enabled` flag in settings so operators can toggle it per environment |

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

### Editorial boost, merchandising, and pinning

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

**Controlled Experiments for Search Engine Result Page Merchandising**
Chapelle, O., Joachims, T., Radlinski, F., & Yue, Y. (2012).
*Large-scale validation and analysis of interleaved search evaluation.*
ACM Transactions on Information Systems, 30(1), 6.
https://dl.acm.org/doi/10.1145/2094072.2094078

> Covers interleaved evaluation of editorial pinning vs organic ranking — directly
> relevant to validating that pinning a specific doc to position #1 actually improves
> task completion without harming the surrounding results.  Motivates the choice to
> make `featured_doc_ids` operator-controlled rather than learned.

**Reciprocal Rank Fusion**
Cormack, G. V., Clarke, C. L. A., & Buettcher, S. (2009).
*Reciprocal rank fusion outperforms Condorcet and individual rank learning methods.*
Proceedings of SIGIR 2009, pp. 758–759.
https://dl.acm.org/doi/10.1145/1571941.1572114

> The foundational RRF paper.  ISP uses RRF to merge lexical and semantic candidate
> sets in `search_with_vec`.  Because RRF works entirely on ranks (not raw scores),
> injecting a pinned doc with a very high artificial score is stable — it does not
> distort the relative ordering of unpinned results.

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
