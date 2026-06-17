# Tab Intent Detection

Tab intent detection classifies a user's search query into one of the catalog tabs (Phones, Accessories, Smartwatch, Tablets) so the PLP can automatically navigate to the most relevant tab before returning results.

---

## Overview

When a user types a query in the PLP search bar, the frontend calls `GET /v1/intent/tab` before running the product search. If the winning tab differs from the currently active tab, the PLP navigates to the correct tab URL and re-runs the search there. If the query is too short or intent confidence is low, the current tab is kept.

```
User types query
       │
       ▼
GET /v1/intent/tab?q=<query>
       │
       ├─ winning tab == current tab ──▶ search current tab, show results
       │
       └─ winning tab != current tab ──▶ navigate to /phones | /accessories | /smartwatch | /tablets
                                               │
                                               ▼
                                          search new tab, show results
```

---

## API Reference

### `GET /v1/intent/tab`

**Query parameters**

| Parameter | Type   | Required | Description           |
|-----------|--------|----------|-----------------------|
| `q`       | string | yes      | The search query text |

**Response**

```json
{
  "tab":        "smartwatch",
  "confidence": 0.29,
  "scores": {
    "device":     0.71,
    "accessory":  0.74,
    "smartwatch": 0.75,
    "tablet":     0.66
  }
}
```

| Field        | Description                                                                                        |
|--------------|----------------------------------------------------------------------------------------------------|
| `tab`        | The winning `intent_label`, or `null` when intent cannot be determined (see *Null response* below) |
| `confidence` | Winning score divided by the sum of all scores — in (0, 1]; `0.0` when `tab` is `null`            |
| `scores`     | Per-label scores used to select the winner; empty object `{}` when `tab` is `null`                |

**Examples**

```bash
# Smartwatch query
curl 'http://localhost:8001/v1/intent/tab?q=samsung+galaxy+watch'
# {"tab":"smartwatch","confidence":0.29,"scores":{"device":0.71,"accessory":0.74,"smartwatch":0.75,"tablet":0.66}}

# Tablet query
curl 'http://localhost:8001/v1/intent/tab?q=ipad+tablet'
# {"tab":"tablet","confidence":0.28,"scores":{"device":0.69,"accessory":0.71,"smartwatch":0.63,"tablet":0.76}}

# Galaxy Tab query
curl 'http://localhost:8001/v1/intent/tab?q=galaxy+tab'
# {"tab":"tablet","confidence":0.28,"scores":{"device":0.61,"accessory":0.62,"smartwatch":0.58,"tablet":0.63}}

# Accessory query
curl 'http://localhost:8001/v1/intent/tab?q=phone+case+charger'
# {"tab":"accessory","confidence":0.36,"scores":{"device":0.67,"accessory":0.72,"smartwatch":0.63,"tablet":0.64}}

# No intent_label configured / empty query / embedding failure
# {"tab":null,"confidence":0.0,"scores":{}}
```

**Null response**

`tab` is `null` (and `scores` is `{}`) in three cases:

| Condition | Reason |
|-----------|--------|
| Empty or whitespace-only `q` | No query to classify |
| No indexes have `intent_label` set | Nothing to compare against |
| Embedding failure or all scores zero | Ambiguous result — caller decides the fallback |

Callers must treat `null` as "no opinion" and apply their own default (e.g. stay on the current tab).

---

## How It Works

### 1. Database-driven label mapping

At request time the endpoint fetches all ISP indexes from PostgreSQL and builds a map of `collection_name → intent_label`. No collection IDs or category keywords are hardcoded in the source. Adding a new tab only requires creating an index with `intent_label` set.

```python
col_meta = {
    "isp_22ffabfef4d1": ("device",     53),   # Phones
    "isp_847bf1f24c72": ("accessory", 560),   # Accessories
    "isp_8bf92948e950": ("smartwatch",  13),  # Smartwatches
    "isp_9ed56100f220": ("tablet",      15),  # Tablets
}
```

Only indexes where both `collection_name` and `intent_label` are non-empty participate in the vote.

### 2. Scoring strategy (per collection)

The endpoint uses one of two scoring modes depending on whether the collection's documents were ingested with the same embedding model as the current global model (`voyage-3`).

#### Mode A — Vector similarity (primary, all current indexes)

Embed the query with the global model (`voyage-3`, `input_type="query"`), run a k-NN vector search (k = 5) against the collection, and compute:

```
score = 1 - (average vector_distance of top-5 hits) / 2
```

`vector_distance` is cosine distance in [0, 2]. Score is normalised to [0, 1]; higher means more semantically similar.

This mode is used when the index's `embed_model` field is empty (= inherits the global `voyage-3` model). All four current indexes (Phones, Accessories, Smartwatches, Tablets) use this mode.

| Index        | embed_model | Scoring mode     |
|--------------|-------------|------------------|
| Phones       | *(global)*  | Vector similarity |
| Accessories  | *(global)*  | Vector similarity |
| Smartwatches | *(global)*  | Vector similarity |
| TABLETS      | *(global)*  | Vector similarity |

#### Mode B — Coverage ratio (fallback for custom-model indexes)

If an index was ingested with a custom `embed_model` (e.g. `rexbert-mini`), its vectors live in a different space and cannot be compared with the global query embedding. The endpoint falls back to lexical BM25 search:

```
score = min((found / doc_count) × 1.5, 1.0)
```

`found` is the number of documents matching the query; `doc_count` is the total index size. The 1.5× multiplier makes high-coverage scores (>50%) competitive with typical vector similarity values (~0.65–0.80). This mode is only triggered for indexes with a non-empty `embed_model`.

### 3. Winner selection

```
best_label = argmax(label_score)
confidence = label_score[best_label] / sum(all_scores)
```

If all scores are zero (e.g. embedding service down, no indexed documents), the endpoint returns `{"tab": null, "confidence": 0.0, "scores": {}}`. ISP does not know which tab is the caller's default — that decision belongs to the consumer.

### 4. Parallel Typesense multi_search

All searches are issued in a single `multi_search` call to avoid N round trips. For each collection the endpoint runs one vector search (Mode A) plus one lexical search as fallback. With all four current indexes on the global model:

```
searches = 4 vector + 4 lexical = 8 sub-searches per request
```

The lexical searches are retained so the coverage ratio fallback is available automatically if any future index is added with a custom `embed_model`.

---

## `intent_label` Field

`intent_label` is a free-text column on the `indexes` PostgreSQL table that links an ISP index to a PLP tab. It is also exposed in the `IndexMeta` Pydantic model and all API responses for indexes.

### Schema

```sql
ALTER TABLE indexes ADD COLUMN IF NOT EXISTS intent_label TEXT DEFAULT '';
```

### Seeded values (existing indexes)

| Index name   | collection_name  | intent_label |
|--------------|------------------|--------------|
| Phones       | isp_22ffabfef4d1 | `device`     |
| Accessories  | isp_847bf1f24c72 | `accessory`  |
| Smartwatches | isp_8bf92948e950 | `smartwatch` |
| TABLETS      | isp_9ed56100f220 | `tablet`     |

Seeding runs inside `init_db()` on every startup using `UPDATE ... WHERE intent_label = ''` — safe to re-run against existing databases.

### Setting `intent_label` at index creation

```bash
POST /v1/indexes
{
  "name": "Laptops",
  "description": "Laptop catalog",
  "intent_label": "laptop"
}
```

### Updating `intent_label` on an existing index

Use `set_intent_label` from `index_service`:

```python
from isp.services.domain.index_service import set_intent_label

await set_intent_label("9ed56100f220", "tablet")
```

Or update directly in PostgreSQL:

```sql
UPDATE indexes SET intent_label = 'tablet' WHERE id = '9ed56100f220';
```

---

## PLP Integration

The PLP (`catalogController.ts`) maps the `intent_label` returned by the API to a tab key using `INTENT_TO_TAB`:

```typescript
const INTENT_TO_TAB: Record<string, TabKey> = {
  device:     'phones',
  accessory:  'accessories',
  smartwatch: 'smartwatch',
  tablet:     'tablets',
};
```

If the intent API returns a label not present in this map, the PLP stays on the current tab. When adding a new tab, both the `intent_label` value in the DB and the `INTENT_TO_TAB` map in `catalogController.ts` must be updated.

### Intent flow in `loadProducts`

```typescript
if (!options.skipIntent && state.query.trim().length > 1) {
  const intent = await getTabIntent(state.query.trim());
  // intent.tab is null when ISP has no opinion — fall back to current tab.
  const intentTab = (intent.tab ? INTENT_TO_TAB[intent.tab] : undefined) ?? state.tab;
  if (intentTab !== state.tab) {
    navigateToTab(intentTab, state.query.trim()); // full page navigation
    return;
  }
}
```

`skipIntent` is set to `true` when the user explicitly clicks a tab, so a manual tab selection is never overridden by intent. When `tab` is `null` the `?? state.tab` fallback keeps the user on their current tab.

---

## Extending to a New Tab

1. **Create the ISP index** with the correct `intent_label`:
   ```bash
   POST /v1/indexes
   { "name": "Laptops", "intent_label": "laptop" }
   ```

2. **Ingest documents** into the index via a connector.

3. **Add the tab to the PLP** — update `INTENT_TO_TAB` in `catalogController.ts`:
   ```typescript
   const INTENT_TO_TAB = {
     ...existing,
     laptop: 'laptops',
   };
   ```

4. **Add the tab key** to `TabKey` in `types.ts`, add the route in `[category].astro`, and add the tab in `TabNav.svelte`.

No changes to `catalog.py` are required — the intent endpoint is fully self-configuring from the database.

---

## Embedding Model Requirements

All indexes participating in intent detection should be ingested with the global embedding model (leave `embed_model` empty when creating the index). When all collections share the same vector space, the query embedding from `embed_query()` is directly comparable across every collection and Mode A scoring is used throughout.

**Current state — all four indexes use `voyage-3` (global):**

```
GET /v1/indexes → embed_model field
  Phones        → ""   ✅ global
  Accessories   → ""   ✅ global
  Smartwatches  → ""   ✅ global
  TABLETS       → ""   ✅ global  (rebuilt 2026-06-17 from rexbert-micro → voyage-3)
```

The tablets index was originally ingested with `rexbert-micro`. Before the rebuild, "galaxy tab" failed to route to the tablet tab because lexical scoring could not distinguish Galaxy Tab products from Galaxy S-series phones (both match the `galaxy` token). After rebuilding with `voyage-3`, the semantic distance between "galaxy tab" and the tablet collection is correctly lower than its distance to accessories or phones.

**If a new index is added with a custom model**, the endpoint automatically falls back to coverage ratio scoring (Mode B) for that collection. Intent accuracy may be reduced for ambiguous queries. Rebuild with the global model to restore full vector scoring.

```bash
# Check which indexes use a custom model
curl http://localhost:8001/v1/indexes | jq '.[] | {name, embed_model, intent_label}'
```

---

## File Reference

| File | Role |
|------|------|
| `backend/src/isp/routers/catalog.py` | `GET /v1/intent/tab` endpoint |
| `backend/src/isp/services/infra/database.py` | `intent_label` column DDL + seed |
| `backend/src/isp/services/domain/index_service.py` | `set_intent_label()`, CRUD with `intent_label` |
| `backend/src/isp/models/index.py` | `IndexMeta.intent_label`, `IndexCreate.intent_label` |
| `plp/src/lib/controller/catalogController.ts` | `INTENT_TO_TAB` map, `loadProducts` intent flow |
| `plp/src/lib/repository/catalogRepository.ts` | `getTabIntent()` — calls `GET /v1/intent/tab` |
